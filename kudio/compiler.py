"""Compile ordered PCS AST nodes into inference segments and control events."""
import copy
import hashlib
import json
import uuid
from collections import defaultdict, deque

from .pcs import PCS_VERSION, decode_literal_text, parse_source
from .text import split_text_with_spans
from .tts import build_tts_payload, effective_voice


def source_hash(text):
    """Hash untouched source text to identify stale compiled plans."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def segment_fingerprint(segment, voice=None):
    """Hash semantic scope and every effective model and TTS payload setting."""
    effective = effective_voice(segment, voice)
    data = {'page': segment.get('page'), 'section': segment.get('section'),
            'rate': segment.get('rate'), 'gpt': effective['gpt'], 'sovits': effective['sovits'],
            'payload': build_tts_payload(segment, voice),
            'allow_control_literals': segment.get('allow_control_literals', False),
            'literal_controls': segment.get('literal_controls', [])}
    if segment.get('voice_label'):
        data['voice_label'] = segment['voice_label']
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _semantic_key(segment):
    data = {key: segment.get(key) for key in ('text', 'page', 'section', 'rate', 'voice_label')}
    data['literal_controls'] = segment.get('literal_controls', [])
    data['allow_control_literals'] = segment.get('allow_control_literals', False)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _reuse_segments(segments, previous_segments, voice):
    """Match identical scopes in source order; consume each old segment once."""
    candidates = defaultdict(deque)
    used_ids = set()
    for previous in previous_segments or []:
        if not isinstance(previous, dict) or not previous.get('id') or previous['id'] in used_ids:
            continue
        used_ids.add(previous['id'])
        candidates[_semantic_key(previous)].append(previous)
    for segment in segments:
        matching = candidates[_semantic_key(segment)]
        if not matching:
            segment['fingerprint'] = segment_fingerprint(segment, voice)
            continue
        previous = matching.popleft()
        segment['overrides'] = copy.deepcopy(previous.get('overrides') or {})
        fingerprint = segment_fingerprint(segment, voice)
        try:
            old_fingerprint = previous.get('fingerprint') or segment_fingerprint(previous, voice)
        except (ValueError, TypeError, KeyError):
            old_fingerprint = None
        segment['fingerprint'] = fingerprint
        if fingerprint != old_fingerprint:
            continue
        for field in ('id', 'status', 'error', 'duration', 'audio_version', 'role', 'emotion', 'previous'):
            if field in previous:
                segment[field] = copy.deepcopy(previous[field])
        if segment['status'] == 'running':
            segment['status'], segment['error'] = 'pending', ''


def compile_source(text, limit=160, voice=None, previous_segments=None, source_format='txt'):
    """Compile boundaries before natural splitting; invalid PCS yields no plan.

    Parser errors are returned with ``valid=False`` and empty output lists so
    callers can display diagnostics. Invalid API argument types raise ValueError.
    Unset rate is None and inherits the effective project/segment voice speed.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError('每段最大字数必须是正整数')
    parsed = parse_source(text, source_format)
    result = dict(parsed, segments=[], execution_plan=[], source_hash=source_hash(text), pcs_version=PCS_VERSION)
    if not parsed['valid']:
        return result
    segments, ordering = [], []
    page, section, rate, chapter, voice_label = None, None, None, '正文', None
    for node in parsed['ast']:
        if node['type'] == 'control':
            event = {'kind': 'event', 'id': uuid.uuid4().hex,
                     'source_start': node['source_start'], 'source_end': node['source_end']}
            command, value = node['command'], node['value']
            if command == 'p':
                page = value
                event.update(type='page', page=page)
            elif command == 'pause':
                event.update(type='pause', duration_ms=value)
            elif command == 'rate':
                rate = value
                event.update(type='rate', value=rate)
            elif command == 'section':
                section = value
                event.update(type='section', name=section)
            elif command == 'voice':
                voice_label = value
                event.update(type='voice', label=voice_label)
            ordering.append(event)
            continue
        raw = text[node['source_start']:node['source_end']]
        if parsed['source_format'] == 'pcs':
            decoded, mapping, literals = decode_literal_text(raw, node['source_start'])
        else:
            decoded = raw
            mapping = [(node['source_start'] + i, node['source_start'] + i + 1) for i in range(len(raw))]
            literals = []
        for segment in split_text_with_spans(decoded, limit):
            start, end = segment.pop('text_start'), segment.pop('text_end')
            positions = segment.pop('text_positions')
            if segment['chapter'] == '正文':
                segment['chapter'] = chapter
            else:
                chapter = segment['chapter']
            segment.update(page=page, section=section, rate=rate, voice_label=voice_label, source_format=parsed['source_format'],
                           source_start=mapping[start][0], source_end=mapping[end - 1][1],
                           overrides={}, audio_version=None)
            escaped = []
            for literal in literals:
                indices = [index for index, position in enumerate(positions)
                           if literal['start'] <= position < literal['end']]
                if indices:
                    lo, hi = indices[0], indices[-1] + 1
                    escaped.append({'start': lo, 'end': hi, 'text': segment['text'][lo:hi]})
            if escaped:
                segment.update(allow_control_literals=True, literal_controls=escaped)
            ordering.append({'kind': 'speech', 'segment_index': len(segments)})
            segments.append(segment)
    _reuse_segments(segments, previous_segments, voice)
    for item in ordering:
        if item['kind'] == 'speech':
            item['segment_id'] = segments[item.pop('segment_index')]['id']
    result.update(segments=segments, execution_plan=ordering)
    return result
