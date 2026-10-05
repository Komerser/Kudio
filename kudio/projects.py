"""Project schema migration and source application, independent of HTTP state."""
import copy
from bisect import bisect_right

from .pcs import PCS_VERSION, parse_source
from .source import source_hash
from .compiler import compile_control_event

SCHEMA_VERSION = 2


def refresh_source_cache(project, force=False):
    """Derive AST/diagnostics from source; never rewrite the compiled snapshot.

    Schema 2 retains these fields for compatibility and inspection, but they
    are caches, not source authority. Missing identity in older projects forces
    a trusted parse even when their compiled source hash already matches.
    """
    source_format = project.get('source_format', 'legacy')
    if source_format == 'legacy':
        return project
    text = project.get('source_text', '')
    identity = {'source_hash': source_hash(text), 'source_format': source_format,
                'pcs_version': PCS_VERSION}
    if (force or project.get('parse_cache') != identity
            or project.get('source_hash') != identity['source_hash']
            or not isinstance(project.get('ast'), list)
            or not isinstance(project.get('diagnostics'), list)):
        parsed = parse_source(text, source_format)
        project.update(ast=parsed['ast'], diagnostics=parsed['diagnostics'], parse_cache=identity)
    return project


def migrate_project(project):
    """Upgrade metadata in place without reading, deleting, or replacing any WAV."""
    if int(project.get('schema_version', 1)) > SCHEMA_VERSION:
        raise ValueError('此项目来自更高版本 Kudio，请升级程序后打开')
    project.setdefault('exports', [])
    project.setdefault('error', '')
    project.setdefault('source_format', 'legacy')
    if project['source_format'] == 'legacy':
        # This is a recoverable editing draft. The existing segments remain authoritative
        # until the user explicitly applies/compiles the reconstructed source.
        project['source_text'] = '\n\n'.join(s['text'] for s in project['segments'])
        project['source_hash'] = source_hash(project['source_text'])
        project['execution_plan'] = [{'kind': 'speech', 'segment_id': s['id']}
                                     for s in project['segments']]
        project.setdefault('ast', [])
        project.setdefault('diagnostics', [])
    else:
        project.setdefault('source_text', '')
        project.setdefault('source_hash', source_hash(project['source_text']))
        refresh_source_cache(project)
    project.setdefault('pcs_version', '0.1')
    project.setdefault('limit', 160)
    project.setdefault('default_role_id', None)
    project.setdefault('voice_bindings', {})
    project.setdefault('role_snapshots', {})
    project['schema_version'] = SCHEMA_VERSION
    for segment in project['segments']:
        segment['source_format'] = 'pcs' if project['source_format'] == 'pcs' else 'txt'
        segment.setdefault('page', None)
        segment.setdefault('section', None)
        segment.setdefault('rate', None)
        segment.setdefault('voice_label', None)
        segment.setdefault('overrides', {})
        segment.setdefault('audio_version', None)
        segment.setdefault('source_start', None)
        segment.setdefault('source_end', None)
        segment.setdefault('fingerprint', None)
    return project


def apply_compilation(project, compiled, text, limit=160):
    """Commit a validated source and its plan together; never apply partial parses."""
    if compiled.get('source_hash') != source_hash(text):
        raise ValueError('编译结果与源稿不一致，请重新编译')
    if not compiled['valid']:
        errors = [d['message'] for d in compiled['diagnostics'] if d['level'] == 'error']
        raise ValueError('脚本尚未通过解析：' + '；'.join(errors))
    parsed = parse_source(text, compiled['source_format'])
    if not parsed['valid']:
        raise ValueError('源稿尚未通过解析，请修正源稿并重新编译')
    project.update(schema_version=SCHEMA_VERSION, source_text=text,
                   source_format=compiled['source_format'], source_hash=compiled['source_hash'],
                   pcs_version=compiled['pcs_version'], limit=limit,
                   ast=parsed['ast'], diagnostics=parsed['diagnostics'],
                   parse_cache={'source_hash': compiled['source_hash'],
                                'source_format': compiled['source_format'], 'pcs_version': PCS_VERSION},
                   execution_plan=copy.deepcopy(compiled['execution_plan']),
                   segments=copy.deepcopy(compiled['segments']), error='', exports=[])
    # Legacy ranges may reference segments removed by compilation. Discard only
    # those UI ranges; their audio files remain on disk and cannot enter this plan.
    ids = {s['id'] for s in project['segments']}
    project['groups'] = [g for g in project.get('groups', [])
                         if g.get('start') in ids and g.get('end') in ids]
    for group in project['groups']:
        for field in ('file', 'srt', 'kson'):
            group.pop(field, None)
    return project


def require_compiled(project):
    """Block inference/export on invalid or mismatched source and plan snapshots."""
    if project.get('source_format', 'legacy') == 'legacy':
        return
    if project.get('source_hash') != source_hash(project.get('source_text', '')):
        refresh_source_cache(project, force=True)
        raise ValueError('源稿已更改，请重新解析并应用编译')
    refresh_source_cache(project, force=True)
    if any(d.get('level') == 'error' for d in project.get('diagnostics', [])):
        raise ValueError('PCS 存在解析错误，请修正源稿并重新编译')
    # Validate event semantics against authoritative source without recompiling
    # speech or touching stored IDs/audio. Schema-2 random event IDs stay valid.
    expected_events = [compile_control_event(node, project['source_hash'])
                       for node in project['ast'] if node['type'] == 'control']
    actual_events = [item for item in project.get('execution_plan', []) if item.get('kind') == 'event']
    if len(actual_events) != len(expected_events) or any(
            any(key not in actual or type(actual[key]) is not type(value) or actual[key] != value
                for key, value in expected.items() if key != 'id')
            for expected, actual in zip(expected_events, actual_events)):
        raise ValueError('控制事件与源稿不一致，请重新编译源稿')
    expected = [s['id'] for s in project['segments']]
    actual = [item.get('segment_id') for item in project.get('execution_plan', [])
              if item.get('kind') == 'speech']
    if actual != expected:
        raise ValueError('推理计划已失效，请重新编译源稿')
    if expected_events:
        # Separate event/speech lists cannot detect moving a control across a
        # speech boundary. Use stored source spans for the interleaved signature.
        text_length = len(project['source_text'])
        for segment in project['segments']:
            start, end = segment.get('source_start'), segment.get('source_end')
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= text_length
                    or not any(node['type'] == 'text' and node['source_start'] <= start
                               and end <= node['source_end'] for node in project['ast'])):
                raise ValueError('语音源码位置已失效，请重新编译源稿')
        ends = sorted(segment['source_end'] for segment in project['segments'])
        expected_boundaries = [bisect_right(ends, event['source_start']) for event in expected_events]
        actual_boundaries, speech_count = [], 0
        for item in project.get('execution_plan', []):
            if item.get('kind') == 'speech':
                speech_count += 1
            elif item.get('kind') == 'event':
                actual_boundaries.append(speech_count)
        if actual_boundaries != expected_boundaries:
            raise ValueError('控制事件顺序与语音源码边界不一致，请重新编译源稿')
