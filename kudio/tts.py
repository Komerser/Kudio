"""Pure GPT-SoVITS payload construction and an injectable inference boundary."""
import io
import math
import wave


VOICE_DEFAULTS = {'gpt': '', 'sovits': '', 'reference': '', 'prompt': '',
                  'prompt_lang': 'zh', 'text_lang': 'zh', 'speed': 1.0, 'seed': 42}


def effective_voice(segment, project_voice):
    """Resolve project settings, segment overrides and persistent PCS rate."""
    voice = dict(VOICE_DEFAULTS, **(project_voice or {}))
    voice.update(segment.get('overrides') or {})
    if segment.get('rate') is not None:
        voice['speed'] = segment['rate']
    return voice


def _check_text(segment):
    text = segment.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('推理片段不能为空')
    if segment.get('source_format') == 'txt':
        return text  # Explicit TXT is ordinary text, including tag-like characters.
    cursor = 0
    while True:
        cursor = text.find('#[', cursor)
        if cursor < 0:
            break
        allowed = False
        if segment.get('allow_control_literals') is True:
            for literal in segment.get('literal_controls', []):
                start, end = literal.get('start'), literal.get('end')
                if (isinstance(start, int) and isinstance(end, int) and start <= cursor < end
                        and 0 <= start < end <= len(text) and text[start:end] == literal.get('text')):
                    allowed = True
                    break
        if not allowed:
            raise ValueError('推理文本含未解析的 PCS 标签，请重新解析并编译源码')
        cursor += 2
    return text


def build_tts_payload(segment, project_voice):
    """Construct the stable local API payload and reject leaked PCS controls."""
    text = _check_text(segment)
    voice = effective_voice(segment, project_voice)
    speed = float(voice['speed'])
    if not math.isfinite(speed) or not 0.5 <= speed <= 2:
        raise ValueError('语速应为 0.5–2')
    return {'text': text, 'text_lang': voice['text_lang'],
            'ref_audio_path': voice['reference'], 'prompt_text': voice['prompt'],
            'prompt_lang': voice['prompt_lang'], 'speed_factor': speed,
            'seed': int(voice['seed']), 'text_split_method': 'cut5', 'batch_size': 1,
            'parallel_infer': False, 'streaming_mode': False, 'media_type': 'wav'}


def infer_segment(segment, project_voice, rpc, timeout=600):
    """Call an injected local RPC and validate returned uncompressed WAV data."""
    raw = rpc('/tts', build_tts_payload(segment, project_voice), timeout=timeout)
    with wave.open(io.BytesIO(raw), 'rb') as audio:
        if not audio.getnframes() or audio.getcomptype() != 'NONE':
            raise ValueError('引擎返回了空音频或不支持的 WAV 格式')
        try:
            audio.setpos(audio.getnframes() - 1)
            last_frame = audio.readframes(1)
        except (EOFError, OSError, RuntimeError, wave.Error) as exc:
            raise ValueError('引擎返回的 WAV 数据不完整') from exc
        if len(last_frame) != audio.getnchannels() * audio.getsampwidth():
            raise ValueError('引擎返回的 WAV 数据不完整')
        duration = audio.getnframes() / audio.getframerate()
    return raw, duration
