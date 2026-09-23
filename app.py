"""Local audiobook workstation. Python 3.9+, standard library only."""
import copy
import io
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
import wave
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs, urlencode

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
def discover_engine(root):
    config = root / 'settings.json'
    if config.is_file():
        value = json.loads(config.read_text(encoding='utf-8-sig')).get('engine_root', '')
        if value:
            path = Path(value).expanduser()
            return (path if path.is_absolute() else root / path).resolve()
    candidates = sorted(p for p in root.parent.iterdir() if p.is_dir() and
                        (p / 'api_v2.py').is_file() and (p / 'runtime/python.exe').is_file())
    if len(candidates) == 1:
        return candidates[0]
    return root / 'GPT-SoVITS'


ENGINE = discover_engine(ROOT)
API = 'http://127.0.0.1:9880'
LOCK = threading.RLock()
ACTIVE = None
STOP = threading.Event()
PROCESS = None
LIFECYCLE = ''
TRAINING_PROCESS = None


def tail_log(path, size=18000):
    if not path.is_file():
        return ''
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - size))
        return f.read().decode('utf-8', errors='replace')


def project_view(p):
    p = copy.deepcopy(p)
    p['folder'] = str(project_dir(p['id']).resolve())
    cursor, exact = 0.0, True
    gap = float(p['voice'].get('gap', 0.3))
    for i, s in enumerate(p['segments']):
        known = s['status'] == 'done' and s.get('duration', 0) > 0
        duration = float(s.get('duration', 0)) if known else max(0.5, len(s['text']) / 4.5)
        s['timeline'] = {'start': cursor, 'end': cursor + duration, 'estimated': not (exact and known)}
        s['number'] = i + 1
        cursor += duration + (gap if i < len(p['segments']) - 1 else 0)
        exact = exact and known
    p['timeline_duration'], p['timeline_estimated'] = cursor, not exact
    p['suggestions'] = suggest_groups(p)
    return p


def suggest_groups(p):
    groups = []
    for segment in p['segments']:
        if not groups or groups[-1]['name'] != segment['chapter']:
            groups.append({'name': segment['chapter'], 'start': segment['id'], 'end': segment['id'], 'source': 'auto'})
        else:
            groups[-1]['end'] = segment['id']
    hidden = set(p.get('dismissed_suggestions', []))
    accepted = {g.get('suggestion_id') for g in p.get('groups', [])}
    result = []
    for group in groups:
        group['id'] = 'auto-' + group['start'] + '-' + group['end']
        if group['id'] not in hidden and group['id'] not in accepted:
            result.append(group)
    return result


def selected_range(p, start, end):
    ids = [s['id'] for s in p['segments']]
    if start not in ids or end not in ids:
        raise ValueError('起始或终止片段已不存在，请重新选择')
    first, last = ids.index(start), ids.index(end)
    if first > last:
        raise ValueError('起始片段必须位于终止片段之前')
    return p['segments'][first:last + 1]


def invalidate_exports(p):
    p['exports'] = []
    for group in p.get('groups', []):
        group.pop('file', None)
        group.pop('srt', None)


def apply_voice(p, voice):
    validate_voice(voice)
    old = p['voice']
    synthesis_changed = {k: v for k, v in voice.items() if k not in ('name', 'gap')} != {k: v for k, v in old.items() if k not in ('name', 'gap')}
    if synthesis_changed:
        for s in p['segments']:
            s.update(status='pending', error='', duration=0)
    if synthesis_changed or old.get('gap') != voice.get('gap'):
        invalidate_exports(p)
    p['voice'] = voice


def mutate_project(p, action, d):
    """Reversible edits, keeping stable segment IDs for saved ranges."""
    if action == 'rename':
        title = d.get('title', '').strip()
        if not title or len(title) > 160:
            raise ValueError('作品名应为 1–160 字')
        p['title'] = title
    elif action == 'archive':
        p['archived'] = bool(d.get('archived', True))
    elif action == 'delete-segment':
        index = next((i for i, s in enumerate(p['segments']) if s['id'] == d['segment']), None)
        if index is None: raise ValueError('片段不存在')
        p.setdefault('trash', []).append({'index': index, 'segment': p['segments'].pop(index)})
        invalidate_exports(p)
    elif action == 'restore-segment':
        trash = p.setdefault('trash', [])
        if not trash: raise ValueError('没有可恢复的片段')
        entry = trash.pop()
        p['segments'].insert(min(entry['index'], len(p['segments'])), entry['segment'])
        invalidate_exports(p)
    elif action in ('group', 'update-group'):
        segments = selected_range(p, d['start'], d['end'])
        name = d.get('name', '').strip()
        if not name or len(name) > 80: raise ValueError('请填写 1–80 字的分组名')
        color = d.get('color', '#287f78')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color): raise ValueError('分段颜色无效')
        if action == 'update-group':
            group = next((g for g in p.get('groups', []) if g['id'] == d.get('group')), None)
            if group is None: raise ValueError('分段不存在')
            group.update(name=name, start=segments[0]['id'], end=segments[-1]['id'], color=color)
            group.pop('file', None)
            group.pop('srt', None)
        else:
            suggestion_id = d.get('suggestion_id')
            if suggestion_id:
                suggestion = next((g for g in suggest_groups(p) if g['id'] == suggestion_id), None)
                if suggestion is None: raise ValueError('建议已变更或已采纳，请刷新后重试')
                if (suggestion['start'], suggestion['end']) != (d['start'], d['end']):
                    raise ValueError('建议边界已变更')
            p.setdefault('groups', []).append({'id': uuid.uuid4().hex, 'name': name,
                'start': segments[0]['id'], 'end': segments[-1]['id'], 'color': color,
                'source': 'auto' if suggestion_id else 'manual', 'suggestion_id': suggestion_id})
    elif action == 'dismiss-suggestion':
        p.setdefault('dismissed_suggestions', []).append(d['suggestion_id'])
    elif action == 'reset-suggestions':
        p['dismissed_suggestions'] = []
    elif action == 'remove-group':
        p['groups'] = [g for g in p.get('groups', []) if g['id'] != d['group']]
    elif action == 'edit-segment':
        s = next(s for s in p['segments'] if s['id'] == d['segment'])
        text = d['text'].strip()
        if not text: raise ValueError('片段不能为空')
        overrides = d.get('overrides', {})
        if set(overrides) - {'speed', 'seed', 'text_lang', 'reference', 'prompt', 'prompt_lang'}:
            raise ValueError('包含不支持的片段参数')
        voice = dict(p['voice'], **overrides)
        validate_voice(voice)
        audio = project_dir(p['id']) / (s['id'] + '.wav')
        if s['status'] == 'done' and audio.is_file():
            s['previous'] = {k: copy.deepcopy(v) for k, v in s.items() if k != 'previous'}
            shutil.copyfile(str(audio), str(audio.with_name(s['id'] + '-previous.wav')))
        s.update(text=text, overrides=overrides, status='pending', error='', duration=0)
        invalidate_exports(p)
    else:
        raise ValueError('未知编辑操作')
    return p


def stop_engine():
    """Stop only our child process or the recognized API on the configured port."""
    global PROCESS
    if PROCESS is not None and PROCESS.poll() is None:
        PROCESS.terminate()
        try:
            PROCESS.wait(timeout=15)
        except subprocess.TimeoutExpired:
            PROCESS.kill()
            PROCESS.wait(timeout=5)
        PROCESS = None
        return
    try:
        spec = json.loads(rpc('/openapi.json', timeout=2))
    except Exception:
        return
    if not all(path in spec.get('paths', {}) for path in ('/tts', '/control', '/set_sovits_weights')):
        raise ValueError('9880 端口上的服务不是可识别的 GPT-SoVITS，引擎未被停止')
    try:
        rpc('/control?command=exit', timeout=3)
    except Exception:
        pass  # The upstream process may close the connection before responding.
    for _ in range(20):
        try:
            rpc('/openapi.json', timeout=1)
        except Exception:
            return
        time.sleep(0.25)
    raise RuntimeError('引擎没有退出，请检查引擎日志后重试')


def finish_queue_and_stop_engine():
    STOP.set()
    while True:
        with LOCK:
            if ACTIVE is None:
                break
        time.sleep(0.2)
    stop_engine()


def split_text(text, limit=160):
    """Preserve all non-whitespace text; prefer sentences, then clauses."""
    result, chapter, buf = [], '正文', ''
    def flush():
        nonlocal buf
        if buf.strip():
            result.append({'id': uuid.uuid4().hex, 'chapter': chapter, 'text': buf.strip(),
                           'role': '旁白', 'emotion': '默认', 'status': 'pending', 'error': '', 'duration': 0})
        buf = ''
    for line in text.replace('\r', '').split('\n'):
        line = line.strip()
        if not line:
            flush()
            continue
        if re.match(r'^(第[零〇一二三四五六七八九十百千万两\d]+[章节卷回部篇].{0,60}|序章|序言|楔子|尾声|后记)$', line):
            flush()
            chapter = line
        pieces = re.findall(r'.+?(?:[。！？!?；;]+[”’」』]*|$)', line)
        for piece in pieces:
            if len(piece) > limit:
                clauses = re.findall(r'.+?(?:[，、,:：]+|$)', piece)
            else:
                clauses = [piece]
            for clause in clauses:
                while clause:
                    if len(buf) + len(clause) <= limit:
                        buf += clause
                        break
                    flush()
                    if len(clause) <= limit:
                        buf = clause
                        break
                    buf, clause = clause[:limit], clause[limit:]
                    flush()
        flush()
    return result


def check_project_id(pid):
    if not re.fullmatch('[a-f0-9]{32}', pid):
        raise ValueError('项目编号无效')


def checked_data_path(path):
    resolved = path.resolve()
    if resolved == DATA.resolve() or DATA.resolve() not in resolved.parents:
        raise ValueError('作品路径不在数据目录内，已取消操作')
    if path.is_symlink(): raise ValueError('不支持操作链接目录')
    return resolved


def project_files():
    return sorted((DATA / 'projects').glob('*/project.json')) + sorted(
        f for f in DATA.glob('*/project.json') if re.fullmatch('[a-f0-9]{32}', f.parent.name))


def named_project_folder(p):
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', p['title'])[:40].strip(' .') or '未命名作品'
    return title + '--' + p['id']


def project_dir(pid):
    check_project_id(pid)
    matches = list((DATA / 'projects').glob('*--' + pid))
    legacy = DATA / pid
    if legacy.is_dir(): matches.append(legacy)
    if len(matches) > 1: raise ValueError('发现重复作品目录，请先检查数据目录')
    return checked_data_path(matches[0]) if matches else DATA / 'projects' / ('未命名作品--' + pid)


def trash_dir(pid):
    check_project_id(pid)
    matches = list((DATA / 'trash').glob('*--' + pid))
    if len(matches) != 1: raise ValueError('回收站中没有唯一匹配的作品')
    return checked_data_path(matches[0])


def manage_project(pid, operation, title=None):
    if ACTIVE == pid: raise ValueError('请先暂停该作品，等待当前片段生成完成')
    if operation == 'delete-project':
        source = checked_data_path(project_dir(pid))
        p = read(pid)
        destination = checked_data_path(DATA / 'trash' / named_project_folder(p))
    else:
        source = trash_dir(pid)
        p = json.loads((source / 'project.json').read_text(encoding='utf-8'))
        if p['id'] != pid: raise ValueError('作品编号不匹配')
        if operation == 'purge-project':
            if title != p['title']: raise ValueError('请输入完整作品名确认永久删除')
            shutil.rmtree(checked_data_path(source))
            return
        if operation != 'restore-project': raise ValueError('未知作品操作')
        if project_dir(pid).exists(): raise ValueError('作品已存在，不能覆盖恢复')
        destination = checked_data_path(DATA / 'projects' / named_project_folder(p))
    if destination.exists(): raise ValueError('目标目录已存在，操作已取消')
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)


def save(p):
    folder = project_dir(p['id'])
    if list((DATA / 'trash').glob('*--' + p['id'])):
        raise ValueError('作品已删除，请先从回收站恢复')
    target = checked_data_path(DATA / 'projects' / named_project_folder(p))
    target.parent.mkdir(parents=True, exist_ok=True)
    if folder.exists() and folder.resolve() != target:
        if target.exists(): raise ValueError('作品目标目录已存在')
        checked_data_path(folder).rename(target)
    folder = target
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / 'project.tmp'
    temp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(temp), str(folder / 'project.json'))


def read(pid):
    return json.loads((project_dir(pid) / 'project.json').read_text(encoding='utf-8'))


def read_presets():
    path = DATA / 'voice_presets.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def save_preset(voice, preset_id=None):
    voice = copy.deepcopy(voice)
    voice['name'] = str(voice.get('name', '')).strip()
    if not voice['name'] or len(voice['name']) > 80:
        raise ValueError('请填写 1–80 字的音色名称，用于识别预设')
    validate_voice(voice)
    presets = read_presets()
    selected = next((p for p in presets if p['id'] == preset_id), None)
    if preset_id and selected is None:
        raise ValueError('该音色预设不存在，请重新选择')
    if any(p['name'] == voice['name'] and p['id'] != preset_id for p in presets):
        raise ValueError('已有同名预设，请改名另存，或选中原预设后点击“更新所选预设”')
    record = {'id': preset_id or uuid.uuid4().hex, 'name': voice['name'], 'voice': voice}
    if selected is None:
        presets.append(record)
    else:
        presets[presets.index(selected)] = record
    DATA.mkdir(exist_ok=True)
    temp = DATA / 'voice_presets.tmp'
    temp.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(str(temp), str(DATA / 'voice_presets.json'))
    return record


def rpc(path, payload=None, timeout=600):
    body = None if payload is None else json.dumps(payload).encode()
    req = Request(API + path, data=body, headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:1500]
        raise RuntimeError('语音引擎返回错误：' + detail) from exc
    except URLError as exc:
        raise RuntimeError('无法连接语音引擎，请先启动引擎并等待加载完成。' + str(exc.reason)) from exc


def audio_info(raw):
    with wave.open(io.BytesIO(raw), 'rb') as f:
        if not f.getnframes() or f.getcomptype() != 'NONE':
            raise ValueError('引擎返回了空音频或不支持的 WAV 格式')
        return f.getnframes() / f.getframerate()


def defaults():
    return {'name': '自定义音色', 'gpt': '', 'sovits': '',
            'reference': '', 'prompt': '', 'prompt_lang': 'zh',
            'text_lang': 'zh', 'speed': 1.0, 'seed': 42, 'gap': 0.3}


def validate_voice(v):
    for field in ('gpt', 'sovits', 'reference'):
        if not Path(v.get(field, '')).is_file():
            raise ValueError('文件不存在：' + field + ' / ' + v.get(field, ''))
    if not v.get('prompt', '').strip():
        raise ValueError('请填写参考音频实际说出的文字')
    if v.get('text_lang') not in ('zh', 'en', 'ja', 'auto') or v.get('prompt_lang') not in ('zh', 'en', 'ja'):
        raise ValueError('语言设置无效')
    if not 0.5 <= float(v['speed']) <= 2 or not 0 <= float(v['gap']) <= 3:
        raise ValueError('语速应为 0.5–2，间隔应为 0–3 秒')
    int(v['seed'])


def worker(pid, only=None):
    global ACTIVE
    failures = 0
    try:
        with LOCK:
            v = copy.deepcopy(read(pid)['voice'])
        rpc('/set_gpt_weights?' + urlencode({'weights_path': v['gpt']}))
        rpc('/set_sovits_weights?' + urlencode({'weights_path': v['sovits']}))
        while not STOP.is_set():
            with LOCK:
                p = read(pid)
                s = next((s for s in p['segments'] if s['status'] == 'pending' and (only is None or s['id'] in only)), None)
                if s is None:
                    break
                s['status'], s['error'] = 'running', ''
                sid, source = s['id'], s['text']
                v = dict(p['voice'], **s.get('overrides', {}))
                save(p)
            success, error, duration = False, '', 0
            for attempt in range(3):
                try:
                    raw = rpc('/tts', {'text': source, 'text_lang': v['text_lang'],
                        'ref_audio_path': v['reference'], 'prompt_text': v['prompt'],
                        'prompt_lang': v['prompt_lang'], 'speed_factor': float(v['speed']),
                        'seed': int(v['seed']), 'text_split_method': 'cut5', 'batch_size': 1,
                        'parallel_infer': False, 'streaming_mode': False, 'media_type': 'wav'})
                    duration = audio_info(raw)
                    target = project_dir(pid) / (sid + '.wav')
                    temp = target.with_suffix('.tmp')
                    temp.write_bytes(raw)
                    os.replace(str(temp), str(target))
                    success = True
                    break
                except Exception as exc:
                    error = str(exc)[:1000]
                    if STOP.wait(2):
                        break
            with LOCK:
                p = read(pid)
                s = next(s for s in p['segments'] if s['id'] == sid)
                s.update(status='done' if success else 'failed', error='' if success else error, duration=duration)
                if success: s['audio_version'] = uuid.uuid4().hex
                save(p)
            failures = 0 if success else failures + 1
            if failures >= 3:
                raise RuntimeError('连续三个片段失败，队列已暂停。请检查引擎日志后重试。')
    except Exception as exc:
        with LOCK:
            p = read(pid)
            p['error'] = str(exc)[:1000]
            save(p)
    finally:
        with LOCK:
            ACTIVE = None


@contextmanager
def export_source(path, fmt):
    """Normalize only mismatched WAVs in temporary storage, never the source."""
    with wave.open(str(path), 'rb') as original:
        current = (original.getnchannels(), original.getsampwidth(), original.getframerate())
        if current == fmt:
            yield original
            return
    converter = ENGINE / 'runtime' / 'ffmpeg.exe'
    if not converter.is_file():
        raise ValueError('导出需要统一音频格式，但未找到 GPT-SoVITS runtime/ffmpeg.exe')
    codecs = {1: 'pcm_u8', 2: 'pcm_s16le', 3: 'pcm_s24le', 4: 'pcm_s32le'}
    if fmt[1] not in codecs:
        raise ValueError('导出不支持此 WAV 位深')
    cache = DATA / 'cache' / 'export'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(cache)) as temp:
        converted = Path(temp) / 'normalized.wav'
        result = subprocess.run([str(converter), '-nostdin', '-v', 'error', '-y', '-i', str(path),
            '-ar', str(fmt[2]), '-ac', str(fmt[0]), '-c:a', codecs[fmt[1]], str(converted)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('音频格式转换失败：' + result.stderr.decode('utf-8', errors='replace')[-1000:])
        with wave.open(str(converted), 'rb') as source:
            yield source


def subtitle_stamp(seconds):
    ms = int(seconds * 1000 + 0.5)
    hours, ms = divmod(ms, 3600000)
    minutes, ms = divmod(ms, 60000)
    seconds, ms = divmod(ms, 1000)
    return '%02d:%02d:%02d,%03d' % (hours, minutes, seconds, ms)


def write_subtitles(p, filename, cues):
    filename = str(Path(filename).with_suffix('.srt'))
    folder = project_dir(p['id']) / 'exports'
    folder.mkdir(exist_ok=True)
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        text = '\n'.join(line.strip() for line in text.splitlines() if line.strip())
        blocks.append('%d\n%s --> %s\n%s\n' % (i, subtitle_stamp(start), subtitle_stamp(end), text))
    target = folder / filename
    temp = target.with_suffix('.srt.tmp')
    try:
        temp.write_text('\n'.join(blocks), encoding='utf-8-sig')
        os.replace(str(temp), str(target))
    finally:
        if temp.exists(): temp.unlink()
    return filename


def export_filename(label, index=1):
    return '%03d_%s.wav' % (index, re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', label)[:60].rstrip(' .') or '正文')


def export_subtitles(p, members, label):
    if not members or any(s['status'] != 'done' for s in members):
        raise ValueError('请先完成所选范围的音频，再导出准确时间轴字幕')
    formats = Counter()
    for s in members:
        with wave.open(str(project_dir(p['id']) / (s['id'] + '.wav')), 'rb') as source:
            formats[(source.getnchannels(), source.getsampwidth(), source.getframerate())] += 1
    fmt = formats.most_common(1)[0][0]
    cursor, cues = 0, []
    for i, s in enumerate(members):
        if i: cursor += int(float(p['voice']['gap']) * fmt[2])
        with export_source(project_dir(p['id']) / (s['id'] + '.wav'), fmt) as source:
            end = cursor + source.getnframes()
        cues.append((cursor / fmt[2], end / fmt[2], s['text']))
        cursor = end
    return write_subtitles(p, export_filename(label), cues)


def export_chapters(p, selection=None, label=None):
    if selection is not None:
        p = copy.deepcopy(p)
        p['segments'] = copy.deepcopy(selection)
        for segment in p['segments']: segment['chapter'] = label
    if not p['segments'] or any(s['status'] != 'done' for s in p['segments']):
        raise ValueError('请先完成全部片段，避免导出时漏读')
    formats = Counter()
    lengths = {}
    for s in p['segments']:
        with wave.open(str(project_dir(p['id']) / (s['id'] + '.wav')), 'rb') as source:
            formats[(source.getnchannels(), source.getsampwidth(), source.getframerate())] += 1
            lengths[s['id']] = source.getnframes() / source.getframerate()
    fmt = formats.most_common(1)[0][0]
    folder = project_dir(p['id']) / 'exports'
    folder.mkdir(exist_ok=True)
    chapters = []
    for s in p['segments']:
        if not chapters or chapters[-1][0] != s['chapter']:
            chapters.append((s['chapter'], []))
        chapters[-1][1].append(s)
    names = []
    for name, segments in chapters:
        duration = sum(lengths[s['id']] for s in segments) + max(0, len(segments)-1)*float(p['voice']['gap'])
        if duration * fmt[2] * fmt[0] * fmt[1] > 0xFFFFFFFF - 1024:
            raise ValueError('章节“' + name + '”超过标准 WAV 的 4GB 上限，请用起止片段分成较小分组导出')
    for index, (name, segments) in enumerate(chapters, 1):
        filename = export_filename(name, index)
        target = folder / filename
        temp = folder / (filename + '.tmp')
        cues = []
        try:
            with wave.open(str(temp), 'wb') as output:
                output.setnchannels(fmt[0]); output.setsampwidth(fmt[1]); output.setframerate(fmt[2])
                for i, s in enumerate(segments):
                    with export_source(project_dir(p['id']) / (s['id'] + '.wav'), fmt) as source:
                        if i:
                            silence = b'\x80' if fmt[1] == 1 else b'\x00'
                            output.writeframesraw(silence * int(float(p['voice']['gap']) * fmt[2]) * fmt[0] * fmt[1])
                        start = output.getnframes() / fmt[2]
                        while True:
                            frames = source.readframes(65536)
                            if not frames:
                                break
                            output.writeframesraw(frames)
                        cues.append((start, output.getnframes() / fmt[2], s['text']))
            os.replace(str(temp), str(target))
            write_subtitles(p, filename, cues)
        finally:
            if temp.exists():
                temp.unlink()
        names.append(filename)
    return names


class LocalServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits two processes to bind the same port, causing
    # requests to reach an older workstation after an upgrade.
    allow_reuse_address = False

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            with LOCK:
                if u.path == '/api/presets':
                    self.reply({'presets': read_presets()})
                    return
                if u.path == '/api/state':
                    projects = []
                    for path in project_files():
                        p = json.loads(path.read_text(encoding='utf-8'))
                        projects.append({'id': p['id'], 'title': p['title'], 'archived': p.get('archived', False), 'count': len(p['segments']), 'done': sum(s['status']=='done' for s in p['segments'])})
                    trash = []
                    for path in sorted((DATA / 'trash').glob('*/project.json')):
                        p = json.loads(checked_data_path(path).read_text(encoding='utf-8'))
                        trash.append({'id': p['id'], 'title': p['title'], 'count': len(p['segments'])})
                    self.reply({'projects': projects, 'trash': trash, 'active': ACTIVE, 'defaults': defaults(), 'lifecycle': LIFECYCLE})
                    return
                if u.path == '/api/project':
                    self.reply({'project': project_view(read(q['id'][0])), 'active': ACTIVE, 'stopping': STOP.is_set() and ACTIVE is not None})
                    return
            if u.path == '/api/monitor':
                engine_log = tail_log(DATA / 'engine.log')
                try:
                    rpc('/openapi.json', timeout=1); online = True
                except Exception: online = False
                alive = PROCESS is not None and PROCESS.poll() is None
                stage = '可用' if online else ('正在加载' if alive else '已停止 / 未连接')
                if alive and not online:
                    for marker, label in [('Loading GPT-SoVITS', '载入依赖'), ('Loading Text2Semantic', '载入 GPT 模型'), ('Loading VITS', '载入 SoVITS 模型'), ('Loading BERT', '载入文本模型'), ('Loading CNHuBERT', '载入音频模型')]:
                        if marker in engine_log: stage = label
                if ACTIVE:
                    stage = '正在生成音频'
                try:
                    with urlopen('http://127.0.0.1:9874', timeout=0.5): training_online = True
                except Exception: training_online = False
                self.reply({'online': online, 'training_online': training_online, 'stage': stage, 'engine_log': engine_log[-12000:],
                    'training_log': tail_log(DATA / 'training.log')[-12000:],
                    'training_running': TRAINING_PROCESS is not None and TRAINING_PROCESS.poll() is None,
                    'paths': {'workspace': str(ROOT), 'data': str(DATA), 'engine': str(ENGINE)},
                    'active': ACTIVE, 'lifecycle': LIFECYCLE})
                return
            if u.path == '/api/health':
                try:
                    rpc('/openapi.json', timeout=2)
                    self.reply({'online': True, 'lifecycle': LIFECYCLE})
                except Exception:
                    self.reply({'online': False, 'lifecycle': LIFECYCLE})
                return
            if u.path == '/':
                path, mime = ROOT / 'index.html', 'text/html; charset=utf-8'
            elif u.path in ('/studio.js', '/segmentation.js', '/library.js', '/kudio.js', '/studio.css'):
                path, mime = ROOT / u.path[1:], ('text/javascript; charset=utf-8' if u.path.endswith('.js') else 'text/css; charset=utf-8')
            elif u.path == '/audio':
                pid, sid = q['id'][0], q['segment'][0]
                if not re.fullmatch('[a-f0-9]{32}', sid):
                    raise ValueError('片段编号无效')
                path, mime = project_dir(pid) / (sid + '.wav'), 'audio/wav'
            elif u.path == '/download':
                filename = q['file'][0]
                if Path(filename).name != filename or '/' in filename or '\\' in filename:
                    raise ValueError('文件名无效')
                path, mime = project_dir(q['id'][0]) / 'exports' / filename, 'audio/wav'
                if Path(filename).suffix.lower() == '.srt': mime = 'application/x-subrip; charset=utf-8'
            else:
                self.reply({'error': '未找到'}, 404); return
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(path.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with path.open('rb') as f:
                while True:
                    block = f.read(65536)
                    if not block: break
                    self.wfile.write(block)
        except Exception as exc:
            self.reply({'error': str(exc)}, 400)

    def do_POST(self):
        global ACTIVE, PROCESS, LIFECYCLE, TRAINING_PROCESS
        try:
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765'):
                raise ValueError('不允许来自其他网页的请求')
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 20_000_000:
                raise ValueError('请求过大或为空')
            d = json.loads(self.rfile.read(size))
            action = self.path
            if action in ('/api/engine-stop', '/api/exit'):
                with LOCK:
                    if LIFECYCLE:
                        raise ValueError('正在停止或退出，请等待当前操作完成')
                    LIFECYCLE = 'exit' if action == '/api/exit' else 'engine-stop'
                    STOP.set()
                exiting = False
                try:
                    finish_queue_and_stop_engine()
                    exiting = action == '/api/exit'
                    try:
                        self.reply({'message': 'Kudio和引擎已退出，可关闭此页面。' if exiting else '引擎已停止，已完成的音频和进度已保留。'})
                    finally:
                        if exiting:
                            threading.Thread(target=self.server.shutdown, daemon=True).start()
                finally:
                    if not exiting:
                        with LOCK:
                            LIFECYCLE = ''
                return
            with LOCK:
                if LIFECYCLE:
                    raise ValueError('正在停止或退出，请等待当前操作完成')
                if action in ('/api/delete-project', '/api/restore-project', '/api/purge-project'):
                    manage_project(d['id'], action[5:], d.get('title'))
                    self.reply({'ok': True}); return
                if action == '/api/open-path':
                    raw_path = d.get('path', '')
                    target = Path(raw_path).expanduser()
                    if not raw_path or not target.is_absolute() or not target.exists():
                        raise ValueError('请提供存在的本机绝对路径')
                    target = target.resolve()
                    if target.is_dir():
                        subprocess.Popen(['explorer.exe', str(target)])
                    else:
                        subprocess.Popen(['explorer.exe', '/select,', str(target)])
                    self.reply({'ok': True}); return
                if action == '/api/training':
                    if TRAINING_PROCESS is None or TRAINING_PROCESS.poll() is not None:
                        try:
                            with urlopen('http://127.0.0.1:9874', timeout=2): pass
                        except Exception:
                            env = dict(os.environ)
                            env['PATH'] = str(ENGINE / 'runtime') + os.pathsep + env.get('PATH', '')
                            with (DATA / 'training.log').open('ab') as log:
                                TRAINING_PROCESS = subprocess.Popen([str(ENGINE / 'runtime/python.exe'), '-I', '-u', str(ROOT / 'training_runner.py')], cwd=str(ENGINE), env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    self.reply({'url':'http://127.0.0.1:9874','message':'训练面板正在启动；实际训练请在面板中配置数据后启动。'}); return
                if action == '/api/preset':
                    self.reply(save_preset(d['voice'], d.get('preset_id')))
                    return
                if action == '/api/create':
                    text = d['text'].strip()
                    if not text: raise ValueError('请先输入正文')
                    limit = int(d.get('limit', 160))
                    if not 40 <= limit <= 500: raise ValueError('分段长度应为 40–500')
                    p = {'id': uuid.uuid4().hex, 'title': d.get('title', '').strip() or '未命名作品',
                         'voice': defaults(), 'segments': split_text(text, limit), 'error': '', 'exports': []}
                    save(p); self.reply(project_view(p)); return
                if action == '/api/engine':
                    if PROCESS and PROCESS.poll() is None:
                        self.reply({'message': '引擎已启动，正在加载或运行'}); return
                    try:
                        rpc('/openapi.json', timeout=2)
                        self.reply({'message': '已有引擎在线'}); return
                    except Exception: pass
                    log = (DATA / 'engine.log').open('ab')
                    env = dict(os.environ)
                    env['PATH'] = str(ENGINE / 'runtime') + os.pathsep + env.get('PATH', '')
                    env['PYTHONIOENCODING'] = 'utf-8'
                    config = DATA / 'tts_infer.yaml'
                    if not config.exists():
                        shutil.copyfile(str(ENGINE / 'GPT_SoVITS/configs/tts_infer.yaml'), str(config))
                    try:
                        PROCESS = subprocess.Popen([str(ENGINE / 'runtime/python.exe'), '-I', '-u', str(ROOT / 'engine_runner.py'), '-a', '127.0.0.1', '-p', '9880', '-c', str(config)],
                            cwd=str(ENGINE), env=env, stdout=log, stderr=log,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    finally: log.close()
                    self.reply({'message': '引擎正在加载，首次启动可能需要数分钟。日志保存在 data/engine.log'}); return
                pid = d['id']
                p = read(pid)
                if action == '/api/pause':
                    if ACTIVE == pid: STOP.set()
                    self.reply({'ok': True}); return
                if ACTIVE == pid:
                    raise ValueError('请先暂停队列，等待当前片段完成后再修改或导出')
                if action == '/api/edit-project':
                    mutate_project(p, d['action'], d)
                elif action == '/api/export-group':
                    group = next(g for g in p.get('groups', []) if g['id'] == d['group'])
                    members = selected_range(p, group['start'], group['end'])
                    group['file'] = export_chapters(p, members, group['name'] + '_' + group['id'][:6])[0]
                    group['srt'] = str(Path(group['file']).with_suffix('.srt'))
                elif action == '/api/export-srt':
                    group = next((g for g in p.get('groups', []) if g['id'] == d.get('group')), None)
                    if d.get('group') and group is None: raise ValueError('分段不存在')
                    members = selected_range(p, group['start'], group['end']) if group else p['segments']
                    label = group['name'] + '_' + group['id'][:6] if group else p['title'] + '_整本'
                    filename = export_subtitles(p, members, label)
                    if group: group['srt'] = filename
                    else: p['exports'] = list(dict.fromkeys(p.get('exports', []) + [filename]))
                elif action == '/api/regenerate':
                    if ACTIVE: raise ValueError('请先暂停当前队列，再单独重做片段')
                    rpc('/openapi.json', timeout=2)
                    s = next(s for s in p['segments'] if s['id'] == d['segment'])
                    mutate_project(p, 'edit-segment', d)
                    p['error'] = ''; save(p)
                    ACTIVE = pid; STOP.clear()
                    threading.Thread(target=worker, args=(pid, {s['id']}), daemon=True).start()
                    self.reply({'ok': True}); return
                elif action == '/api/restore-audio':
                    s = next(s for s in p['segments'] if s['id'] == d['segment'])
                    previous = s.get('previous')
                    if not previous: raise ValueError('没有可恢复的上一版本')
                    shutil.copyfile(str(project_dir(pid) / (s['id'] + '-previous.wav')), str(project_dir(pid) / (s['id'] + '.wav')))
                    s.clear(); s.update(previous); s['audio_version'] = uuid.uuid4().hex
                    invalidate_exports(p)
                elif action == '/api/voice':
                    apply_voice(p, d['voice'])
                elif action == '/api/segment':
                    s = next(s for s in p['segments'] if s['id'] == d['segment'])
                    if not d['text'].strip(): raise ValueError('片段文字不能为空')
                    s.update(text=d['text'].strip(), status='pending', error='', duration=0)
                    p['exports'] = []
                elif action == '/api/retry':
                    for s in p['segments']:
                        if s['status'] == 'failed': s.update(status='pending', error='')
                    p['error'] = ''
                elif action == '/api/start':
                    if ACTIVE: raise ValueError('另一部作品正在生成，请先暂停它')
                    validate_voice(p['voice'])
                    rpc('/openapi.json', timeout=2)
                    if not any(s['status'] == 'pending' for s in p['segments']):
                        raise ValueError('没有待生成片段；失败片段请先点“重试失败”')
                    p['error'] = ''; p['exports'] = []; save(p)
                    ACTIVE = pid; STOP.clear()
                    threading.Thread(target=worker, args=(pid,), daemon=True).start()
                    self.reply({'ok': True}); return
                elif action == '/api/export':
                    if not p.get('groups'):
                        raise ValueError('请先在下方确认分段建议或手动保存分段，也可直接整本导出')
                    ranges = [(g, selected_range(p, g['start'], g['end'])) for g in p['groups']]
                    if any(s['status'] != 'done' for _, members in ranges for s in members):
                        raise ValueError('已确认分段中仍有未完成片段，可单独导出已完成的分段')
                    p['exports'] = []
                    for group, members in ranges:
                        group['file'] = export_chapters(p, members, group['name'] + '_' + group['id'][:6])[0]
                        p['exports'].append(group['file'])
                        group['srt'] = str(Path(group['file']).with_suffix('.srt'))
                        p['exports'].append(group['srt'])
                elif action == '/api/export-all':
                    p['exports'] = export_chapters(p, p['segments'], p['title'] + '_整本')
                    p['exports'] += [str(Path(f).with_suffix('.srt')) for f in p['exports']]
                else: raise ValueError('未知操作')
                save(p); self.reply(project_view(p))
        except Exception as exc:
            self.reply({'error': str(exc)}, 400)


def initialize():
    DATA.mkdir(exist_ok=True)
    for file in project_files():
        p = json.loads(file.read_text(encoding='utf-8'))
        for s in p['segments']:
            if s['status'] == 'running': s['status'] = 'pending'
            if s['status'] == 'done' and not (file.parent / (s['id'] + '.wav')).exists(): s['status'] = 'pending'
        save(p)


if __name__ == '__main__':
    import webbrowser
    try:
        server = LocalServer(('127.0.0.1', 8765), Handler)
    except OSError:
        print('端口 8765 已被占用，请使用已打开的工作台。', flush=True)
        webbrowser.open('http://127.0.0.1:8765')
        raise SystemExit(1)
    initialize()
    (DATA / 'server.pid').write_text(str(os.getpid()), encoding='ascii')
    print('Kudio已启动：http://127.0.0.1:8765', flush=True)
    webbrowser.open('http://127.0.0.1:8765')
    try:
        server.serve_forever()
    finally:
        server.server_close()
        (DATA / 'server.pid').unlink(missing_ok=True)
