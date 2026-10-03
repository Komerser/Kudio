"""Execute an ordered synthesis plan on one PCM frame clock.

All public times are integer milliseconds. Cached project durations are never
used for completed speech: the WAV (and its normalized export format) is the
authority shared by WAV, SRT and KSON exports.
"""
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
import math
import re
import wave


DEFAULT_AUDIO_FORMAT = (1, 2, 24000)


def _number(value, label, minimum=0):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(label + '必须是有效数字') from exc
    if not math.isfinite(result) or result < minimum:
        raise ValueError(label + '超出有效范围')
    return result


def wav_details(source):
    """Validate a PCM WAV, including its final frame, without loading its data."""
    fmt = (source.getnchannels(), source.getsampwidth(), source.getframerate())
    frames = source.getnframes()
    if (source.getcomptype() != 'NONE' or fmt[0] < 1 or fmt[1] not in (1, 2, 3, 4)
            or fmt[2] < 1 or frames < 1):
        raise ValueError('音频为空或不是支持的 PCM WAV')
    # getnframes() comes from a header and can lie when a file was truncated.
    source.setpos(frames - 1)
    if len(source.readframes(1)) != fmt[0] * fmt[1]:
        raise ValueError('WAV 音频数据不完整，请重新生成该片段')
    source.rewind()
    return fmt, frames


@contextmanager
def audio_source(path, fmt, format_audio=None):
    """Open a validated WAV, applying the application's normalizer if needed."""
    if format_audio is not None:
        with format_audio(Path(path), tuple(fmt)) as source:
            actual, unused = wav_details(source)
            if actual != tuple(fmt):
                raise ValueError('音频转换后的格式与导出格式不一致')
            yield source
    else:
        with wave.open(str(path), 'rb') as source:
            actual, unused = wav_details(source)
            if actual != tuple(fmt):
                raise ValueError('导出需要统一 WAV 格式，请提供音频格式转换器')
            yield source


def _execution_plan(project):
    plan = project.get('execution_plan')
    if plan is None:
        return [{'kind': 'speech', 'segment_id': s['id']}
                for s in project.get('segments', [])]
    if not isinstance(plan, list):
        raise ValueError('推理计划必须是有序列表')
    return plan


def _speech_id(item):
    return item.get('segment_id', item.get('id'))


def _selected_plan(project, members):
    """Trim ranges and reintroduce their preceding persistent state at time 0."""
    available = {s['id']: s for s in project.get('segments', [])}
    for sid in available:
        if (not isinstance(sid, str) or not sid or sid in ('.', '..')
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', sid)):
            raise ValueError('片段标识无效')
    selected = project.get('segments', []) if members is None else list(members)
    selected_ids = [s['id'] if isinstance(s, dict) else s for s in selected]
    if len(selected_ids) != len(set(selected_ids)) or any(s not in available for s in selected_ids):
        raise ValueError('所选片段无效或重复')
    wanted = set(selected_ids)
    plan = []
    seen = set()
    for position, original in enumerate(_execution_plan(project)):
        if not isinstance(original, dict):
            raise ValueError('推理计划包含无效条目')
        item = dict(original)
        if item.get('kind') == 'speech':
            sid = _speech_id(item)
            if sid not in available:
                continue  # A removed legacy segment must never be exported.
            if sid in seen:
                raise ValueError('推理计划包含重复语音片段')
            seen.add(sid)
        elif item.get('kind') != 'event':
            raise ValueError('推理计划包含未知条目类型')
        item.setdefault('id', 'event-%04d' % (position + 1))
        plan.append(item)
    if wanted - seen:
        raise ValueError('推理计划缺少当前语音片段，请重新编译脚本')
    if not wanted:
        return [], available
    if wanted == set(available):
        return plan, available
    positions = [i for i, item in enumerate(plan)
                 if item.get('kind') == 'speech' and _speech_id(item) in wanted]
    first, last = positions[0], positions[-1]
    # State controls preceding a selected range are useful; preceding pauses
    # and excluded narration belong to the surrounding book, not this export.
    preceding = {}
    for index, item in enumerate(plan[:first]):
        if item.get('kind') == 'event' and item.get('type') in ('page', 'rate', 'section'):
            preceding[item['type']] = (index, item)
    opening = [dict(item, inherited=True) for unused, item in
               sorted(preceding.values(), key=lambda pair: pair[0])]
    trimmed = []
    for item in plan[first:last + 1]:
        if item.get('kind') != 'speech' or _speech_id(item) in wanted:
            trimmed.append(item)
    return opening + trimmed, available


def build_timeline(project, audio_dir, exact=False, members=None, format_audio=None):
    """Build ordered speech, controls and silence from actual or estimated WAVs.

    Explicit pauses (even zero) replace the default gap between two speech
    items. Metadata controls execute at the current cursor before an implicit
    gap, while pauses advance it immediately. Leading/trailing pauses survive
    full-project exports. Exact mode rejects pending, absent or invalid audio.
    """
    audio_dir = Path(audio_dir)
    plan, available = _selected_plan(project, members)
    speech_ids = [_speech_id(item) for item in plan if item.get('kind') == 'speech']
    original_audio = {}
    formats = Counter()
    for sid in speech_ids:
        segment = available[sid]
        path = audio_dir / (sid + '.wav')
        if segment.get('status') != 'done':
            if exact:
                raise ValueError('请先完成所选范围的音频，再导出准确时间轴')
            continue
        try:
            with wave.open(str(path), 'rb') as source:
                fmt, frames = wav_details(source)
            original_audio[sid] = (fmt, frames)
            formats[fmt] += 1
        except (OSError, EOFError, wave.Error, ValueError) as exc:
            if exact:
                raise ValueError('片段 %s 的 WAV 不可用：%s' % (sid, exc)) from exc
    fmt = formats.most_common(1)[0][0] if formats else DEFAULT_AUDIO_FORMAT
    rate = fmt[2]
    gap = _number(project.get('voice', {}).get('gap', .3), '片段间隔')
    gap_frames = int(gap * rate)
    base_rate = _number(project.get('voice', {}).get('speed', 1), '语速', .5)
    if base_rate > 2:
        raise ValueError('语速应为 0.5–2')
    state = {'page': None, 'section': None, 'rate': base_rate}
    cursor = 0
    known_prefix = True
    had_speech, explicit_pause = False, False
    segments, events, items = [], [], []

    def milliseconds(frames):
        # Integer arithmetic gives identical rounding at every export boundary.
        return (frames * 1000 + rate // 2) // rate

    def silence(frames, reason):
        nonlocal cursor
        start = cursor
        cursor += frames
        items.append({'kind': 'silence', 'frames': frames,
                      'start_frame': start, 'end_frame': cursor,
                      'start_ms': milliseconds(start), 'end_ms': milliseconds(cursor),
                      'duration_ms': milliseconds(cursor) - milliseconds(start),
                      'reason': reason})

    for item in plan:
        if item['kind'] == 'event':
            event = {k: v for k, v in item.items()
                     if k not in ('kind', 'time_ms', 'start_ms', 'end_ms', 'duration_ms')}
            kind = event.get('type')
            event['estimated'] = not known_prefix
            if kind == 'pause':
                duration = _number(item.get('duration_ms', item.get('value', 0)), '停顿')
                if duration != int(duration) or duration > 30000:
                    raise ValueError('停顿应为 0–30000 的整数毫秒')
                event['start_ms'] = milliseconds(cursor)
                silence((int(duration) * rate + 500) // 1000, 'pause')
                event['end_ms'] = milliseconds(cursor)
                event['duration_ms'] = event['end_ms'] - event['start_ms']
                event['requested_duration_ms'] = int(duration)
                explicit_pause = True
            elif kind in ('page', 'rate', 'section'):
                event['time_ms'] = milliseconds(cursor)
                if kind == 'page':
                    value = item.get('page', item.get('value'))
                    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                        raise ValueError('页码必须是正整数')
                    event['page'], state['page'] = value, value
                elif kind == 'rate':
                    value = _number(item.get('value', item.get('rate')), '语速', .5)
                    if value > 2:
                        raise ValueError('语速应为 0.5–2')
                    event['value'], state['rate'] = value, value
                else:
                    value = item.get('name', item.get('section', item.get('value')))
                    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
                        raise ValueError('章节名应为 1–80 字')
                    event['name'], state['section'] = value.strip(), value.strip()
            else:
                raise ValueError('未知时间轴控制事件：' + str(kind))
            events.append(event)
            continue

        sid = _speech_id(item)
        segment = available[sid]
        if had_speech and not explicit_pause and gap_frames:
            silence(gap_frames, 'gap')
        speech_rate = segment.get('rate')
        if speech_rate is None:
            speech_rate = (segment.get('overrides') or {}).get('speed', base_rate)
        speech_rate = _number(speech_rate, '语速', .5)
        if speech_rate > 2:
            raise ValueError('语速应为 0.5–2')
        actual = False
        path = audio_dir / (sid + '.wav')
        if sid in original_audio:
            source_fmt, frames = original_audio[sid]
            if source_fmt == fmt:
                actual = True
            elif format_audio is not None:
                try:
                    with audio_source(path, fmt, format_audio) as source:
                        frames = source.getnframes()
                    actual = True
                except (OSError, EOFError, wave.Error, ValueError) as exc:
                    if exact:
                        raise ValueError('片段 %s 无法转换为导出格式：%s' % (sid, exc)) from exc
            elif exact:
                raise ValueError('导出需要统一 WAV 格式，请提供音频格式转换器')
            else:
                frames = max(1, (frames * rate + source_fmt[2] // 2) // source_fmt[2])
        else:
            frames = max(1, int(max(.5, len(segment.get('text', '')) / 4.5 / speech_rate) * rate))
        if not actual and sid in original_audio and format_audio is not None:
            source_fmt, source_frames = original_audio[sid]
            frames = max(1, (source_frames * rate + source_fmt[2] // 2) // source_fmt[2])
        start = cursor
        cursor += frames
        start_ms, end_ms = milliseconds(start), milliseconds(cursor)
        if end_ms <= start_ms:
            raise ValueError('语音片段不足 1 毫秒，无法生成毫秒时间轴')
        known_prefix = known_prefix and actual
        record = {'id': sid, 'index': len(segments) + 1,
                  'text': segment.get('text', ''), 'start_ms': start_ms,
                  'end_ms': end_ms, 'duration_ms': end_ms - start_ms,
                  'page': segment.get('page', state['page']),
                  'section': segment.get('section', state['section']),
                  'speech': {'rate': speech_rate}, 'estimated': not known_prefix}
        for field in ('source_start', 'source_end'):
            if field in segment:
                record[field] = segment[field]
        segments.append(record)
        audio_item = {'kind': 'speech', 'segment_id': sid, 'frames': frames,
                      'start_frame': start, 'end_frame': cursor,
                      'start_ms': start_ms, 'end_ms': end_ms,
                      'duration_ms': end_ms - start_ms, 'estimated': not actual}
        if actual:
            audio_item['path'] = str(path)
        items.append(audio_item)
        had_speech, explicit_pause = True, False
    return {'segments': segments, 'events': events, 'items': items,
            'duration_ms': milliseconds(cursor), 'total_frames': cursor,
            'audio_format': list(fmt),
            'timing_status': 'exact' if known_prefix and segments else 'estimated'}
