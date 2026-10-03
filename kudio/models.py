"""Voice defaults and validation for the local GPT-SoVITS workflow."""
from pathlib import Path


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
