"""Project schema migration and source application, independent of HTTP state."""
import copy
import hashlib

SCHEMA_VERSION = 2


def source_hash(text):
    """Identify the exact UTF-8 source, including whitespace and controls."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


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
    project.setdefault('pcs_version', '0.1')
    project.setdefault('limit', 160)
    project['schema_version'] = SCHEMA_VERSION
    for segment in project['segments']:
        segment.setdefault('page', None)
        segment.setdefault('section', None)
        segment.setdefault('rate', None)
        segment.setdefault('overrides', {})
        segment.setdefault('audio_version', None)
        segment.setdefault('source_start', None)
        segment.setdefault('source_end', None)
        segment.setdefault('fingerprint', None)
    return project


def apply_compilation(project, compiled, text, limit=160):
    """Commit a validated source and its plan together; never apply partial parses."""
    if not compiled['valid']:
        errors = [d['message'] for d in compiled['diagnostics'] if d['level'] == 'error']
        raise ValueError('脚本尚未通过解析：' + '；'.join(errors))
    project.update(schema_version=SCHEMA_VERSION, source_text=text,
                   source_format=compiled['source_format'], source_hash=compiled['source_hash'],
                   pcs_version=compiled['pcs_version'], limit=limit,
                   ast=copy.deepcopy(compiled['ast']), diagnostics=copy.deepcopy(compiled['diagnostics']),
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
        raise ValueError('源稿已更改，请重新解析并应用编译')
    if any(d.get('level') == 'error' for d in project.get('diagnostics', [])):
        raise ValueError('PCS 存在解析错误，请修正源稿并重新编译')
    expected = [s['id'] for s in project['segments']]
    actual = [item.get('segment_id') for item in project.get('execution_plan', [])
              if item.get('kind') == 'speech']
    if actual != expected:
        raise ValueError('推理计划已失效，请重新编译源稿')
