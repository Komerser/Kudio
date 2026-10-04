"""Project-owned role snapshots and PCS label resolution, without disk access."""
import copy


def voice_labels(project):
    """Return labels in source order; TXT deliberately has no role controls."""
    if project.get('source_format') != 'pcs':
        return []
    labels = []
    for segment in project.get('segments', []):
        label = segment.get('voice_label')
        if label and label not in labels:
            labels.append(label)
    return labels


def resolved_role(segment, project):
    label = segment.get('voice_label') if segment.get('source_format') == 'pcs' else None
    role_id = (project.get('voice_bindings') or {}).get(label) if label else project.get('default_role_id')
    record = (project.get('role_snapshots') or {}).get(role_id) if role_id else None
    return role_id, record


def project_voice(segment, project):
    """Use the saved role snapshot, falling back for an editable unbound draft."""
    unused, record = resolved_role(segment, project)
    return record['voice'] if record else project.get('voice', {})


def binding_errors(project):
    errors = []
    snapshots = project.get('role_snapshots') or {}
    default = project.get('default_role_id')
    if default and default not in snapshots:
        errors.append('默认角色不存在，请重新选择')
    for label in voice_labels(project):
        role_id = (project.get('voice_bindings') or {}).get(label)
        if not role_id or role_id not in snapshots:
            errors.append('请为声音标签「%s」绑定角色' % label)
    return errors


def require_bindings(project):
    errors = binding_errors(project)
    if errors:
        raise ValueError('；'.join(errors))


def apply_bindings(project, default_role_id, bindings, presets, refresh_role_ids=None):
    """Resolve IDs once; project snapshots survive edits/deletion of the library."""
    if default_role_id is not None and not isinstance(default_role_id, str):
        raise ValueError('默认角色编号无效')
    if not isinstance(bindings, dict) or any(not isinstance(k, str) or not k or len(k) > 80
                                            or not isinstance(v, str) for k, v in bindings.items()):
        raise ValueError('声音标签绑定无效')
    default_role_id = default_role_id or None
    bindings = {label: role_id for label, role_id in bindings.items() if role_id}
    refresh_role_ids = [] if refresh_role_ids is None else refresh_role_ids
    if not isinstance(refresh_role_ids, list) or any(not isinstance(role_id, str) or not role_id
                                                  for role_id in refresh_role_ids):
        raise ValueError('要同步的角色编号无效')
    refresh = set(refresh_role_ids)
    wanted = set(bindings.values()) | ({default_role_id} if default_role_id else set())
    if refresh - wanted:
        raise ValueError('只能同步作品当前选用的角色')
    selected = {role['id']: role for role in presets}
    previous = project.get('role_snapshots') or {}
    snapshots = {}
    for role_id in wanted:
        if role_id in refresh:
            record = selected.get(role_id)
            if record is None:
                raise ValueError('要同步的角色已从角色库删除，请沿用作品快照或重新选择角色')
        else:
            record = previous.get(role_id) or selected.get(role_id)
        if record is None:
            raise ValueError('所选角色不存在，请刷新角色列表')
        snapshots[role_id] = copy.deepcopy(record)
    project.update(default_role_id=default_role_id, voice_bindings=copy.deepcopy(bindings),
                   role_snapshots=snapshots)
    if default_role_id:
        project['voice'] = copy.deepcopy(snapshots[default_role_id]['voice'])
    return project
