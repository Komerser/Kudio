"""Atomic WAV, SRT and KSON exports from one precomputed audio timeline."""
import copy
import json
import os
from pathlib import Path
import tempfile
import wave

from .timeline import audio_source


def build_kson(project, timeline, generator_version='1.6.1'):
    """Return standard KSON 0.1 JSON data, including explicit draft status."""
    source = project.get('source', {})
    source_format = project.get('source_format') or (source.get('format', 'txt')
                                                    if isinstance(source, dict) else 'txt')
    source_info = {'format': source_format}
    if source_format == 'pcs':
        source_info['pcs_version'] = project.get('pcs_version', '0.1')
    if project.get('source_hash'):
        source_info['hash'] = project['source_hash']
    return {'format': 'kson', 'version': '0.1', 'timebase': 'ms',
            'generator': {'name': 'Kudio', 'version': generator_version},
            'project': {'id': project.get('id', ''), 'title': project.get('title', '')},
            'source': source_info, 'duration_ms': timeline['duration_ms'],
            'timing_status': timeline['timing_status'],
            'segments': copy.deepcopy(timeline['segments']),
            'events': copy.deepcopy(timeline['events'])}


def _require_exact(timeline):
    if timeline.get('timing_status') != 'exact' or not timeline.get('segments'):
        raise ValueError('请先完成所选范围的音频，再导出准确时间轴')


def _atomic_text(path, text, encoding):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding=encoding, newline='\n') as output:
            output.write(text)
        os.replace(temporary, str(path))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def subtitle_stamp(milliseconds):
    """Format an already rounded integer millisecond timestamp for SRT."""
    milliseconds = int(milliseconds)
    if milliseconds < 0:
        raise ValueError('字幕时间不可为负数')
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, remainder = divmod(remainder, 1000)
    return '%02d:%02d:%02d,%03d' % (hours, minutes, seconds, remainder)


def write_srt(path, timeline):
    """Write speech cues only; silence and controls stay in the shared timing."""
    _require_exact(timeline)
    blocks = []
    for index, segment in enumerate(timeline['segments'], 1):
        text = '\n'.join(line.strip() for line in segment['text'].splitlines() if line.strip())
        blocks.append('%d\n%s --> %s\n%s\n' %
                      (index, subtitle_stamp(segment['start_ms']),
                       subtitle_stamp(segment['end_ms']), text))
    return _atomic_text(path, '\n'.join(blocks), 'utf-8-sig')


def write_kson(path, project, timeline, generator_version='1.6.1'):
    """Write an exact KSON export; use build_kson for estimated UI previews."""
    _require_exact(timeline)
    text = json.dumps(build_kson(project, timeline, generator_version),
                      ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    return _atomic_text(path, text, 'utf-8')


def merge_wav(path, timeline, audio_dir, format_audio=None):
    """Stream planned PCM speech and silence to WAV without recomputing timing.

    Source format and frame counts are rechecked before writing each clip, so a
    changed audio file cannot silently diverge from its SRT/KSON timeline.
    """
    _require_exact(timeline)
    path, audio_dir = Path(path), Path(audio_dir)
    fmt = tuple(timeline['audio_format'])
    total_frames = timeline['total_frames']
    frame_bytes = fmt[0] * fmt[1]
    if total_frames * frame_bytes > 0xFFFFFFFF - 1024:
        raise ValueError('导出超过标准 WAV 的 4GB 上限，请选择较小的片段范围')
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=str(path.parent))
    os.close(handle)
    try:
        with wave.open(temporary, 'wb') as output:
            output.setnchannels(fmt[0])
            output.setsampwidth(fmt[1])
            output.setframerate(fmt[2])
            output.setnframes(total_frames)
            block_frames = max(1, 262144 // frame_bytes)
            silence_sample = b'\x80' if fmt[1] == 1 else b'\x00'
            silence_block = silence_sample * (block_frames * frame_bytes)
            for item in timeline['items']:
                expected = item['frames']
                if item['kind'] == 'silence':
                    remaining = expected
                    while remaining:
                        count = min(remaining, block_frames)
                        output.writeframesraw(silence_block[:count * frame_bytes])
                        remaining -= count
                elif item['kind'] == 'speech':
                    source_path = audio_dir / (item['segment_id'] + '.wav')
                    with audio_source(source_path, fmt, format_audio) as source:
                        if source.getnframes() != expected:
                            raise ValueError('音频已变更，请重新生成时间轴后导出')
                        copied = 0
                        while copied < expected:
                            raw = source.readframes(min(block_frames, expected - copied))
                            if not raw or len(raw) % frame_bytes:
                                raise ValueError('WAV 音频数据不完整，请重新生成该片段')
                            copied += len(raw) // frame_bytes
                            output.writeframesraw(raw)
                else:
                    raise ValueError('时间轴包含未知音频条目')
            if output.getnframes() != total_frames:
                raise ValueError('WAV 合并长度与时间轴不一致')
        os.replace(temporary, str(path))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path
