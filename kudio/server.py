"""Local audiobook workstation. Python 3.9+, standard library only."""
import copy
import base64
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
from contextlib import contextmanager
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, parse_qs, urlencode
from .models import defaults, validate_voice
from .text import split_text
from .pcs import parse_source
from .compiler import compile_source, segment_fingerprint
from .tts import build_tts_payload, infer_segment, effective_voice
from .roles import voice_labels, resolved_role, binding_errors, require_bindings, apply_bindings
from .projects import migrate_project, apply_compilation, require_compiled
from .timeline import build_timeline, wav_details
from .exporters import build_kson, write_srt, write_kson, merge_wav

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'


def read_settings(root=ROOT):
    config = root / 'settings.json'
    if not config.is_file():
        return {}
    settings = json.loads(config.read_text(encoding='utf-8-sig'))
    if not isinstance(settings, dict):
        raise ValueError('settings.json 必须是配置对象')
    return settings


def setting_path(value, root=ROOT):
    if not value:
        return ''
    if not isinstance(value, str):
        raise ValueError('配置路径必须是文字')
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else root / path).resolve())


def discover_engine(root):
    value = read_settings(root).get('engine_root', '')
    if value:
        return Path(setting_path(value, root))
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

ASSET_KINDS = {'gpt': '.ckpt', 'sovits': '.pth',
               'reference': ('.wav', '.mp3', '.flac')}
IMAGE_KINDS = {'avatar': ('.png', '.jpg', '.jpeg', '.webp', '.gif'),
               'portrait': ('.png', '.jpg', '.jpeg', '.webp', '.gif')}
ROOT_FIELDS = ('engine_root', 'model_root', 'reference_root')
SKIP_ASSET_DIRS = {'runtime', 'temp', 'tmp', 'pretrained', 'pretrained_models',
                   'logs', 'log', 'cache', '__pycache__', '.git', 'venv', 'dist'}
MAX_ASSET_DEPTH = 9
MAX_SCAN_ENTRIES = 4000
MAX_CANDIDATES = 300


def asset_roots():
    settings = read_settings()
    roots = {field: setting_path(settings.get(field, '')) for field in ROOT_FIELDS}
    roots['engine_root'] = roots['engine_root'] or str(ENGINE)
    if 'model_root' not in settings:
        local_models = ROOT.parent / 'model'
        if local_models.is_dir():
            roots['model_root'] = str(local_models.resolve())
    return roots


def engine_restart_required(roots):
    return os.path.normcase(roots['engine_root']) != os.path.normcase(str(ENGINE))


def validate_asset_root(field, value):
    if not isinstance(value, str):
        raise ValueError('目录路径必须是文字：' + field)
    value = value.strip()
    if not value:
        if field == 'engine_root':
            raise ValueError('请选择 GPT-SoVITS 整合包目录')
        return ''
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError('请选择存在的本机绝对目录：' + field)
    path = path.resolve()
    if field == 'engine_root' and not ((path / 'api_v2.py').is_file() and
                                        (path / 'runtime' / 'python.exe').is_file()):
        raise ValueError('GPT-SoVITS 目录需要包含 api_v2.py 和 runtime/python.exe')
    return str(path)


def save_asset_roots(values):
    if not isinstance(values, dict) or not any(field in values for field in ROOT_FIELDS):
        raise ValueError('请提供要保存的素材目录')
    settings = read_settings()
    for field in ROOT_FIELDS:
        if field in values:
            settings[field] = validate_asset_root(field, values[field])
    temp = ROOT / ('settings.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(str(temp), str(ROOT / 'settings.json'))
    finally:
        temp.unlink(missing_ok=True)
    roots = asset_roots()
    return {'roots': roots, 'restart_required': engine_restart_required(roots)}


def is_link_or_reparse(path):
    try:
        info = path.lstat()
        return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
    except OSError:
        return True


def asset_group(root, path, fallback):
    if fallback:
        return fallback
    generic = {'model', 'models', 'weights', 'reference_audio',
               'reference_audios', '参考音频', '音频'}
    for name in path.relative_to(root).parts[:-1]:
        if name.casefold() not in generic and not re.fullmatch(r'v\d+(?:pro)?', name, re.IGNORECASE):
            return name
    folder = root
    while (folder.name.casefold() in generic or re.fullmatch(r'v\d+(?:pro)?', folder.name, re.IGNORECASE)) and folder != folder.parent:
        folder = folder.parent
    return folder.name


def asset_key(kind, path):
    stem = path.stem
    if kind in ('gpt', 'sovits'):
        stem = re.sub(r'[-_]e\d+(?:[-_]s\d+)?(?:[-_].*)?$', '', stem,
                      flags=re.IGNORECASE) or path.stem
    return stem.casefold()


def scan_asset_folder(root, allowed, group, candidates, seen):
    """Walk one chosen material folder with fixed limits; never follow links."""
    if not root.is_dir() or is_link_or_reparse(root):
        return False
    stack = [(root, 0)]
    entries = 0
    truncated = False
    while stack:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as listing:
                for entry in listing:
                    entries += 1
                    if entries > MAX_SCAN_ENTRIES:
                        return True
                    path = Path(entry.path)
                    if is_link_or_reparse(path):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        name = entry.name.casefold()
                        if name in SKIP_ASSET_DIRS or name.startswith(('pretrained', 'runtime', 'temp')):
                            continue
                        if depth < MAX_ASSET_DEPTH:
                            stack.append((path, depth + 1))
                        else:
                            truncated = True
                    elif entry.is_file(follow_symlinks=False):
                        extension = path.suffix.casefold()
                        kind = next((name for name, suffixes in allowed.items()
                                     if extension in suffixes), None)
                        if kind is None:
                            continue
                        resolved = path.resolve()
                        key = os.path.normcase(str(resolved))
                        if key in seen[kind]:
                            continue
                        seen[kind].add(key)
                        if len(candidates[kind]) >= MAX_CANDIDATES:
                            truncated = True
                            continue
                        candidates[kind].append({'path': str(resolved),
                            'label': str(path.relative_to(root)),
                            'group': asset_group(root, path, group),
                            'key': asset_key(kind, path)})
        except OSError:
            truncated = True
    return truncated


def asset_catalog():
    roots = asset_roots()
    candidates = {kind: [] for kind in ASSET_KINDS}
    seen = {kind: set() for kind in ASSET_KINDS}
    truncated = False
    model_root = roots['model_root']
    if model_root and os.path.normcase(model_root) != os.path.normcase(roots['engine_root']):
        truncated |= scan_asset_folder(Path(model_root),
            {'gpt': ('.ckpt',), 'sovits': ('.pth',),
             'reference': ASSET_KINDS['reference']}, None, candidates, seen)
    reference_root = roots['reference_root']
    if reference_root:
        truncated |= scan_asset_folder(Path(reference_root),
            {'reference': ASSET_KINDS['reference']}, None, candidates, seen)
    engine_root = Path(roots['engine_root'])
    for parent in (engine_root, engine_root / 'GPT_SoVITS'):
        if not parent.is_dir() or is_link_or_reparse(parent):
            continue
        try:
            with os.scandir(parent) as listing:
                for index, entry in enumerate(listing):
                    if index >= 200:
                        truncated = True
                        break
                    path = Path(entry.path)
                    if not entry.is_dir(follow_symlinks=False) or is_link_or_reparse(path):
                        continue
                    name = entry.name.casefold()
                    if name.startswith('gpt_weights'):
                        allowed = {'gpt': ('.ckpt',)}
                    elif name.startswith('sovits_weights'):
                        allowed = {'sovits': ('.pth',)}
                    else:
                        continue
                    truncated |= scan_asset_folder(path, allowed,
                        'GPT-SoVITS · ' + entry.name, candidates, seen)
        except OSError:
            truncated = True
    for items in candidates.values():
        items.sort(key=lambda item: (item['group'].casefold(), item['label'].casefold()))
    return {'roots': roots, 'active_engine': str(ENGINE),
            'restart_required': engine_restart_required(roots),
            'candidates': candidates, 'truncated': bool(truncated)}


PICK_PATH_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class KudioFolderDialog {
    [ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IFileDialog {
        [PreserveSig] int Show(IntPtr owner);
        void SetFileTypes(uint count, IntPtr filters);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem folder);
        void SetFolder(IShellItem folder);
        void GetFolder(out IShellItem folder);
        void GetCurrentSelection(out IShellItem selection);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName(out IntPtr name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetResult(out IShellItem result);
    }
    [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IShellItem {
        void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr result);
        void GetParent(out IShellItem parent);
        void GetDisplayName(uint format, out IntPtr name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem other, uint hint, out int order);
    }
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, PreserveSig=false)]
    private static extern void SHCreateItemFromParsingName(string path, IntPtr context, ref Guid iid,
                                                          out IShellItem item);
    public static string Pick(IntPtr owner, string initial, string title) {
        IFileDialog dialog = (IFileDialog)Activator.CreateInstance(Type.GetTypeFromCLSID(
            new Guid("dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")));
        IShellItem folder = null, result = null;
        IntPtr name = IntPtr.Zero;
        try {
            uint options;
            dialog.GetOptions(out options);
            // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST | FOS_NOCHANGEDIR.
            dialog.SetOptions(options | 0x20 | 0x40 | 0x800 | 0x8);
            dialog.SetTitle(title);
            if (!String.IsNullOrEmpty(initial) && System.IO.Directory.Exists(initial)) {
                Guid iid = typeof(IShellItem).GUID;
                SHCreateItemFromParsingName(initial, IntPtr.Zero, ref iid, out folder);
                dialog.SetFolder(folder);
            }
            int status = dialog.Show(owner);
            if (status == unchecked((int)0x800704c7)) return "";
            Marshal.ThrowExceptionForHR(status);
            dialog.GetResult(out result);
            result.GetDisplayName(0x80058000, out name);
            return Marshal.PtrToStringUni(name);
        } finally {
            if (name != IntPtr.Zero) Marshal.FreeCoTaskMem(name);
            if (result != null) Marshal.FinalReleaseComObject(result);
            if (folder != null) Marshal.FinalReleaseComObject(folder);
            Marshal.FinalReleaseComObject(dialog);
        }
    }
}
'@
$kind = $env:KUDIO_PICK_KIND
$initial = $env:KUDIO_PICK_INITIAL
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Width = 1
$owner.Height = 1
$owner.Opacity = 0
$owner.TopMost = $true
$owner.Show()
try {
if ($kind -in @('engine_root', 'model_root', 'reference_root')) {
    $selected = [KudioFolderDialog]::Pick($owner.Handle, $initial, $env:KUDIO_PICK_TITLE)
    if ($selected) {
        [Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($selected)))
    }
} else {
    $picker = New-Object System.Windows.Forms.OpenFileDialog
    $picker.CheckFileExists = $true
    $picker.AutoUpgradeEnabled = $true
    $picker.RestoreDirectory = $true
    $picker.Multiselect = $false
    $picker.Title = $env:KUDIO_PICK_TITLE
    $picker.Filter = $env:KUDIO_PICK_FILTER
    if ($initial -and (Test-Path -LiteralPath $initial)) {
        if (Test-Path -LiteralPath $initial -PathType Container) {
            $picker.InitialDirectory = $initial
        } else {
            $picker.InitialDirectory = Split-Path -Parent $initial
            $picker.FileName = Split-Path -Leaf $initial
        }
    }
    try {
        if ($picker.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
            [Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($picker.FileName)))
        }
    } finally { $picker.Dispose() }
}
} finally {
    $owner.Close()
    $owner.Dispose()
}
'''


def pick_local_path(kind, initial='', ui_language='zh'):
    if kind not in ASSET_KINDS and kind not in ROOT_FIELDS and kind not in IMAGE_KINDS:
        raise ValueError('未知的文件或目录类型')
    if os.name != 'nt':
        raise ValueError('本机选择窗口仅支持 Windows')
    if not isinstance(initial, str):
        raise ValueError('初始路径无效')
    history_path = DATA / 'path_history.json'
    history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.is_file() else {}
    remembered = history.get(kind, '')
    if remembered and Path(remembered).is_dir():
        initial = remembered
    if not initial:
        roots = asset_roots()
        initial = {
            'gpt': roots['model_root'] or roots['engine_root'],
            'sovits': roots['model_root'] or roots['engine_root'],
            'reference': roots['reference_root'] or roots['model_root'],
            'engine_root': roots['engine_root'],
            'model_root': roots['model_root'] or roots['engine_root'],
            'reference_root': roots['reference_root'] or roots['model_root'],
            'avatar': str(Path.home() / 'Pictures'),
            'portrait': str(Path.home() / 'Pictures'),
        }[kind]
    env = dict(os.environ)
    env['KUDIO_PICK_KIND'] = kind
    env['KUDIO_PICK_INITIAL'] = initial
    picker_copy = {
        'zh': ('选择 Kudio 使用的本机文件夹', '选择 Kudio 使用的本机文件', 'GPT 模型', 'SoVITS 模型', '角色图片', '参考音频'),
        'en': ('Select a local folder for Kudio', 'Select a local file for Kudio', 'GPT models', 'SoVITS models', 'Character images', 'Reference audio'),
        'ja': ('Kudio で使用するフォルダーを選択', 'Kudio で使用するファイルを選択', 'GPT モデル', 'SoVITS モデル', 'キャラクター画像', '参照音声'),
    }.get(ui_language if isinstance(ui_language, str) else 'zh')
    if picker_copy is None:
        picker_copy = ('选择 Kudio 使用的本机文件夹', '选择 Kudio 使用的本机文件', 'GPT 模型', 'SoVITS 模型', '角色图片', '参考音频')
    env['KUDIO_PICK_TITLE'] = picker_copy[0 if kind in ROOT_FIELDS else 1]
    extensions = '*.ckpt' if kind == 'gpt' else '*.pth' if kind == 'sovits' else '*.png;*.jpg;*.jpeg;*.webp;*.gif' if kind in IMAGE_KINDS else '*.wav;*.mp3;*.flac'
    label = picker_copy[2 if kind == 'gpt' else 3 if kind == 'sovits' else 4 if kind in IMAGE_KINDS else 5]
    env['KUDIO_PICK_FILTER'] = '%s (%s)|%s' % (label, extensions, extensions)
    command = base64.b64encode(PICK_PATH_SCRIPT.encode('utf-16le')).decode('ascii')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    if not powershell.is_file():
        raise RuntimeError('未找到 Windows PowerShell，无法打开本机选择窗口')
    result = subprocess.run([str(powershell), '-NoProfile', '-STA', '-EncodedCommand', command],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
        env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('无法打开本机选择窗口：' + result.stderr[-500:])
    encoded = result.stdout.lstrip('\ufeff').strip()
    if not encoded:
        return {'path': '', 'cancelled': True}
    selected = base64.b64decode(encoded, validate=True).decode('utf-16le')
    if kind in ROOT_FIELDS:
        selected = validate_asset_root(kind, selected)
    elif kind in IMAGE_KINDS:
        selected = validated_role_image(selected)
    else:
        path = Path(selected)
        extensions = ASSET_KINDS[kind]
        if isinstance(extensions, str):
            extensions = (extensions,)
        if not path.is_file() or path.suffix.casefold() not in extensions:
            raise ValueError('请选择对应格式的本机文件')
        selected = str(path.resolve())
    history[kind] = selected if kind in ROOT_FIELDS else str(Path(selected).parent)
    DATA.mkdir(exist_ok=True)
    temporary = DATA / ('path_history.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(str(temporary), str(history_path))
    finally:
        temporary.unlink(missing_ok=True)
    return {'path': selected, 'cancelled': False}


def tail_log(path, size=18000):
    if not path.is_file():
        return ''
    with path.open('rb') as f:
        f.seek(max(0, path.stat().st_size - size))
        return f.read().decode('utf-8', errors='replace')


def project_view(p):
    """Present a shared timeline and draft KSON without changing the saved project."""
    p = copy.deepcopy(p)
    p['folder'] = str(project_dir(p['id']).resolve())
    p['voice_labels'] = voice_labels(p)
    p['voice_binding_errors'] = binding_errors(p)
    timeline = build_timeline(p, project_dir(p['id']), format_audio=export_source)
    by_id = {s['id']: s for s in timeline['segments']}
    for i, s in enumerate(p['segments']):
        timing = by_id[s['id']]
        s['timeline'] = {'start': timing['start_ms'] / 1000, 'end': timing['end_ms'] / 1000,
                         'estimated': timing['estimated']}
        if s.get('status') == 'done' and not timing['estimated']:
            s['duration'] = timing['duration_ms'] / 1000
        s['number'] = i + 1
        role_id, role = resolved_role(s, p)
        s['resolved_role_id'] = role_id
        s['resolved_role_name'] = role['name'] if role else p['voice'].get('name', '默认角色')
    p['timeline'] = {k: v for k, v in timeline.items() if k not in ('items',)}
    p['timeline_duration'] = timeline['duration_ms'] / 1000
    p['timeline_estimated'] = timeline['timing_status'] != 'exact'
    p['kson'] = build_kson(p, timeline)
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
        group.pop('kson', None)


def apply_voice(p, voice):
    validate_voice(voice)
    old = copy.deepcopy(p)
    p['voice'] = copy.deepcopy(voice)
    p['default_role_id'] = None
    refresh_voice_fingerprints(p, old)


def refresh_voice_fingerprints(p, old):
    changed = False
    for segment in p['segments']:
        current = segment_fingerprint(segment, p)
        if current != segment_fingerprint(segment, old):
            segment.update(status='pending', error='', duration=0, audio_version=None)
            changed = True
        segment['fingerprint'] = current
    if changed or p['voice'].get('gap') != old['voice'].get('gap'):
        invalidate_exports(p)


def apply_project_voices(p, default_role_id, bindings, refresh_role_ids=None):
    old = copy.deepcopy(p)
    apply_bindings(p, default_role_id, bindings, read_presets(), refresh_role_ids=refresh_role_ids)
    refresh_voice_fingerprints(p, old)
    def role_metadata(project):
        values = []
        for segment in project['segments']:
            role_id, role = resolved_role(segment, project)
            values.append((role_id, role['name'] if role else None))
        return values
    if role_metadata(p) != role_metadata(old):
        invalidate_exports(p)
    return p


def mutate_project(p, action, d):
    """Reversible edits, keeping stable segment IDs for saved ranges."""
    source_based = p.get('source_format', 'legacy') != 'legacy'
    if source_based and action in ('delete-segment', 'restore-segment'):
        raise ValueError('请在源稿中修改正文，然后重新解析并应用编译')
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
        if source_based and text != s['text']:
            raise ValueError('请在源稿中编辑正文，片段窗口仅修改声音参数')
        overrides = d.get('overrides', {})
        if set(overrides) - {'speed', 'seed', 'text_lang', 'reference', 'prompt', 'prompt_lang'}:
            raise ValueError('包含不支持的片段参数')
        voice = dict(effective_voice(s, p), **overrides)
        validate_voice(voice)
        audio = project_dir(p['id']) / (s['id'] + '.wav')
        if s['status'] == 'done' and audio.is_file():
            s['previous'] = {k: copy.deepcopy(v) for k, v in s.items() if k != 'previous'}
            s['previous']['fingerprint'] = segment_fingerprint(s, p)
            shutil.copyfile(str(audio), str(audio.with_name(s['id'] + '-previous.wav')))
        s.update(text=text, overrides=overrides, status='pending', error='', duration=0)
        s['fingerprint'] = segment_fingerprint(s, p)
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
    migrate_project(p)
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
    return migrate_project(json.loads((project_dir(pid) / 'project.json').read_text(encoding='utf-8')))


def read_presets():
    path = DATA / 'voice_presets.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def validated_role_image(value):
    if not isinstance(value, str):
        raise ValueError('角色图片路径必须是文字')
    if not value:
        return ''
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or is_link_or_reparse(path):
        raise ValueError('请选择存在的本机图片文件')
    if path.suffix.casefold() not in IMAGE_KINDS['avatar'] or path.stat().st_size > 20_000_000:
        raise ValueError('角色图片需为 PNG、JPEG、WebP 或 GIF，且小于 20MB')
    with path.open('rb') as source:
        header = source.read(16)
    signatures = (header.startswith(b'\x89PNG\r\n\x1a\n'), header.startswith(b'\xff\xd8\xff'),
                  header.startswith((b'GIF87a', b'GIF89a')),
                  header.startswith(b'RIFF') and header[8:12] == b'WEBP')
    if not any(signatures):
        raise ValueError('图片内容不是受支持的图片格式')
    return str(path.resolve())


def validated_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError('角色个人属性必须是对象')
    description = profile.get('description', '')
    tags = profile.get('tags', [])
    color = profile.get('color', '#287f78')
    if not isinstance(description, str) or len(description) > 4000:
        raise ValueError('角色描述最多 4000 字')
    if not isinstance(tags, list) or len(tags) > 20 or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 40 for tag in tags):
        raise ValueError('角色标签最多 20 个，每个 1–40 字')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('角色颜色无效')
    return {'description': description.strip(), 'tags': list(dict.fromkeys(tag.strip() for tag in tags)),
            'color': color, 'avatar': validated_role_image(profile.get('avatar', '')),
            'portrait': validated_role_image(profile.get('portrait', ''))}


def write_presets(presets):
    DATA.mkdir(exist_ok=True)
    temp = DATA / ('voice_presets.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(str(temp), str(DATA / 'voice_presets.json'))
    finally:
        temp.unlink(missing_ok=True)


def save_preset(voice, preset_id=None, profile=None):
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
    record['profile'] = validated_profile(profile if profile is not None else (selected or {}).get('profile', {}))
    if selected is None:
        presets.append(record)
    else:
        presets[presets.index(selected)] = record
    write_presets(presets)
    return record


def delete_preset(preset_id):
    presets = read_presets()
    if not any(record['id'] == preset_id for record in presets):
        raise ValueError('该角色不存在，请刷新角色列表')
    write_presets([record for record in presets if record['id'] != preset_id])
    return {'ok': True}


def role_image(preset_id, kind):
    if kind not in IMAGE_KINDS:
        raise ValueError('角色图片类型无效')
    record = next((record for record in read_presets() if record['id'] == preset_id), None)
    if record is None:
        raise ValueError('该角色不存在')
    image = (record.get('profile') or {}).get(kind, '')
    if not image:
        raise ValueError('该角色尚未设置此图片')
    path = Path(validated_role_image(image))
    mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.webp': 'image/webp', '.gif': 'image/gif'}[path.suffix.casefold()]
    return path, mime


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
        fmt, frames = wav_details(f)
        return frames / fmt[2]



def worker(pid, only=None):
    global ACTIVE
    failures = 0
    try:
        with LOCK:
            initial = read(pid)
            require_compiled(initial)
            require_bindings(initial)
        loaded_models = None
        while not STOP.is_set():
            with LOCK:
                p = read(pid)
                s = next((s for s in p['segments'] if s['status'] == 'pending' and (only is None or s['id'] in only)), None)
                if s is None:
                    break
                s['status'], s['error'] = 'running', ''
                sid = s['id']
                build_tts_payload(s, p)  # Validate before committing running state.
                speech, voice_snapshot = copy.deepcopy(s), copy.deepcopy(p)
                save(p)
            success, error, duration = False, '', 0
            for attempt in range(3):
                try:
                    voice = effective_voice(speech, voice_snapshot)
                    models = (voice['gpt'], voice['sovits'])
                    if loaded_models != models:
                        # Either RPC may partially change engine state before failing.
                        # Invalidate first so a later role must reload both models.
                        loaded_models = None
                        rpc('/set_gpt_weights?' + urlencode({'weights_path': models[0]}))
                        rpc('/set_sovits_weights?' + urlencode({'weights_path': models[1]}))
                        loaded_models = models
                    raw, duration = infer_segment(speech, voice_snapshot, rpc)
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
                if success: s['fingerprint'] = segment_fingerprint(s, p)
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



def export_filename(label, index=1):
    return '%03d_%s.wav' % (index, re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', label)[:60].rstrip(' .') or '正文')


def export_subtitles(p, members, label):
    """Write exact subtitles using the same frame plan as WAV and KSON."""
    require_compiled(p)
    timeline = build_timeline(p, project_dir(p['id']), exact=True,
                              members=members, format_audio=export_source)
    filename = str(Path(export_filename(label)).with_suffix('.srt'))
    write_srt(project_dir(p['id']) / 'exports' / filename, timeline)
    return filename


def export_kson(p, members=None, label=None):
    """Export only an exact, complete machine-readable timeline."""
    require_compiled(p)
    timeline = build_timeline(p, project_dir(p['id']), exact=True,
                              members=members, format_audio=export_source)
    filename = str(Path(export_filename(label or p['title'] + '_整本')).with_suffix('.kson'))
    write_kson(project_dir(p['id']) / 'exports' / filename, p, timeline)
    return filename


def export_chapters(p, selection=None, label=None):
    """Export WAV/SRT/KSON together; legacy chapter grouping remains available."""
    require_compiled(p)
    if selection is not None:
        chapters = [(label or p['title'], selection)]
    elif p.get('source_format', 'legacy') != 'legacy':
        chapters = [(p['title'], p['segments'])]
    else:
        chapters = []
        for segment in p['segments']:
            if not chapters or chapters[-1][0] != segment['chapter']:
                chapters.append((segment['chapter'], []))
            chapters[-1][1].append(segment)
    if not chapters:
        raise ValueError('请先完成全部片段，避免导出时漏读')
    names = []
    # Validate all ranges before creating any deliverables.
    prepared = [(name, members, build_timeline(p, project_dir(p['id']), exact=True,
                 members=members, format_audio=export_source)) for name, members in chapters]
    for index, (name, members, timeline) in enumerate(prepared, 1):
        filename = export_filename(name, index)
        target = project_dir(p['id']) / 'exports' / filename
        merge_wav(target, timeline, project_dir(p['id']), format_audio=export_source)
        write_srt(target.with_suffix('.srt'), timeline)
        write_kson(target.with_suffix('.kson'), p, timeline)
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
            if u.path == '/api/assets':
                self.reply(asset_catalog())
                return
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
            elif u.path in ('/studio.js', '/pcs-editor.js', '/segmentation.js', '/library.js', '/kudio.js', '/assets.js', '/rebuild.js', '/i18n.js', '/i18n-catalog.js', '/studio.css'):
                path, mime = ROOT / u.path[1:], ('text/javascript; charset=utf-8' if u.path.endswith('.js') else 'text/css; charset=utf-8')
            elif u.path == '/api/role-image':
                if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                    raise ValueError('不允许其他网页读取角色图片')
                path, mime = role_image(q['preset_id'][0], q['kind'][0])
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
                if Path(filename).suffix.lower() == '.kson': mime = 'application/json; charset=utf-8'
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
            port = self.server.server_port
            if origin and origin not in ('http://127.0.0.1:%d' % port, 'http://localhost:%d' % port):
                raise ValueError('不允许来自其他网页的请求')
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 20_000_000:
                raise ValueError('请求过大或为空')
            d = json.loads(self.rfile.read(size))
            action = self.path
            if action == '/api/pcs/parse':
                self.reply(parse_source(d['text'], d.get('source_format', 'txt')))
                return
            if action == '/api/pcs/compile':
                self.reply(compile_source(d['text'], int(d.get('limit', 160)),
                           voice=d.get('voice') or defaults(), source_format=d.get('source_format', 'txt')))
                return
            if action == '/api/pick-path':
                with LOCK:
                    if LIFECYCLE:
                        raise ValueError('正在停止或退出，请等待当前操作完成')
                self.reply(pick_local_path(d.get('kind'), d.get('initial', ''), d.get('ui_language', 'zh')))
                return
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
                if action == '/api/asset-roots':
                    self.reply(save_asset_roots(d)); return
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
                    self.reply(save_preset(d['voice'], d.get('preset_id'), d.get('profile')))
                    return
                if action == '/api/delete-preset':
                    self.reply(delete_preset(d.get('preset_id')))
                    return
                if action == '/api/create':
                    text = d['text']
                    if not text.strip(): raise ValueError('请先输入正文')
                    limit = int(d.get('limit', 160))
                    if not 40 <= limit <= 500: raise ValueError('分段长度应为 40–500')
                    compiled = compile_source(text, limit, voice=defaults(),
                                               source_format=d.get('source_format', 'txt'))
                    p = {'id': uuid.uuid4().hex, 'title': d.get('title', '').strip() or '未命名作品',
                         'voice': defaults(), 'segments': compiled['segments'], 'error': '', 'exports': [],
                         'source_text': text, 'source_format': compiled['source_format'],
                         'source_hash': compiled['source_hash'], 'pcs_version': compiled['pcs_version'],
                         'ast': compiled['ast'], 'diagnostics': compiled['diagnostics'],
                         'execution_plan': compiled['execution_plan'], 'limit': limit, 'schema_version': 2,
                         'default_role_id': None, 'voice_bindings': {}, 'role_snapshots': {}}
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
                if action == '/api/project/source':
                    limit = int(d.get('limit', p.get('limit', 160)))
                    if not 40 <= limit <= 500: raise ValueError('分段长度应为 40–500')
                    compiled = compile_source(d['text'], limit, voice=p,
                                               previous_segments=p['segments'],
                                               source_format=d.get('source_format', 'pcs' if p.get('source_format') == 'pcs' else 'txt'))
                    apply_compilation(p, compiled, d['text'], limit)
                    for segment in p['segments']:
                        if segment['status'] == 'done' and not (project_dir(pid) / (segment['id'] + '.wav')).is_file():
                            segment.update(status='pending', duration=0, audio_version=None)
                elif action == '/api/edit-project':
                    mutate_project(p, d['action'], d)
                elif action == '/api/export-group':
                    group = next(g for g in p.get('groups', []) if g['id'] == d['group'])
                    members = selected_range(p, group['start'], group['end'])
                    group['file'] = export_chapters(p, members, group['name'] + '_' + group['id'][:6])[0]
                    group['srt'] = str(Path(group['file']).with_suffix('.srt'))
                    group['kson'] = str(Path(group['file']).with_suffix('.kson'))
                elif action == '/api/export-kson':
                    filename = export_kson(p)
                    p['exports'] = list(dict.fromkeys(p.get('exports', []) + [filename]))
                elif action == '/api/export-srt':
                    group = next((g for g in p.get('groups', []) if g['id'] == d.get('group')), None)
                    if d.get('group') and group is None: raise ValueError('分段不存在')
                    members = selected_range(p, group['start'], group['end']) if group else p['segments']
                    label = group['name'] + '_' + group['id'][:6] if group else p['title'] + '_整本'
                    filename = export_subtitles(p, members, label)
                    if group: group['srt'] = filename
                    else: p['exports'] = list(dict.fromkeys(p.get('exports', []) + [filename]))
                elif action == '/api/regenerate':
                    require_compiled(p)
                    require_bindings(p)
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
                    if previous.get('fingerprint') != segment_fingerprint(previous, p):
                        raise ValueError('上一版音频的音色参数与当前作品不一致，请重新生成')
                    if p.get('source_format', 'legacy') != 'legacy' and any(
                            previous.get(key) != s.get(key) for key in ('text', 'page', 'section', 'rate', 'voice_label')):
                        raise ValueError('上一版音频与当前源稿不一致，请重新编译并生成')
                    location = {key: s.get(key) for key in ('source_start', 'source_end', 'chapter')}
                    backup = project_dir(pid) / (s['id'] + '-previous.wav')
                    with wave.open(str(backup), 'rb') as audio:
                        wav_details(audio)
                    target = project_dir(pid) / (s['id'] + '.wav')
                    temporary = target.with_suffix('.restore.tmp')
                    try:
                        shutil.copyfile(str(backup), str(temporary))
                        os.replace(str(temporary), str(target))
                    finally:
                        temporary.unlink(missing_ok=True)
                    s.clear(); s.update(previous); s['audio_version'] = uuid.uuid4().hex
                    if p.get('source_format', 'legacy') != 'legacy': s.update(location)
                    invalidate_exports(p)
                elif action == '/api/voice':
                    apply_voice(p, d['voice'])
                elif action == '/api/project/voices':
                    apply_project_voices(p, d.get('default_role_id', p.get('default_role_id')),
                                         d.get('voice_bindings', p.get('voice_bindings', {})),
                                         d.get('refresh_role_ids'))
                elif action == '/api/segment':
                    mutate_project(p, 'edit-segment', d)
                elif action == '/api/retry':
                    for s in p['segments']:
                        if s['status'] == 'failed': s.update(status='pending', error='')
                    p['error'] = ''
                elif action == '/api/start':
                    if ACTIVE: raise ValueError('另一部作品正在生成，请先暂停它')
                    require_compiled(p)
                    require_bindings(p)
                    for segment in p['segments']:
                        if segment['status'] == 'pending':
                            validate_voice(effective_voice(segment, p))
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
                        group['kson'] = str(Path(group['file']).with_suffix('.kson'))
                        p['exports'].append(group['kson'])
                elif action == '/api/export-all':
                    p['exports'] = export_chapters(p, p['segments'], p['title'] + '_整本')
                    wavs = list(p['exports'])
                    p['exports'] += [str(Path(f).with_suffix(ext)) for f in wavs for ext in ('.srt', '.kson')]
                else: raise ValueError('未知操作')
                save(p); self.reply(project_view(p))
        except Exception as exc:
            self.reply({'error': str(exc)}, 400)


def initialize():
    DATA.mkdir(exist_ok=True)
    for file in project_files():
        p = json.loads(file.read_text(encoding='utf-8'))
        if p.get('schema_version', 1) < 2:
            backup = file.with_name('project.schema-1.backup.json')
            if not backup.exists(): shutil.copyfile(str(file), str(backup))
        migrate_project(p)
        for s in p['segments']:
            if s['status'] == 'running': s['status'] = 'pending'
            if s['status'] == 'done' and not (file.parent / (s['id'] + '.wav')).exists(): s['status'] = 'pending'
        save(p)


def main():
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
