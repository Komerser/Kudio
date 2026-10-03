"""Pure PCS/compiler/TTS regression tests; no models or HTTP service needed."""
import copy
import io
import json
from pathlib import Path
import struct
import sys
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kudio.compiler import compile_source, segment_fingerprint
from kudio.pcs import lex_source, parse_source
from kudio.text import split_text
from kudio.tts import build_tts_payload, infer_segment


REGRESSION_SOURCE = (
    '#[p:1]#长假结束，周末还要上班，这种安排是不是只有中国有？'
    '#[p:2]#这期我们就从2026年的全年日历出发，看看哪些国家把原本休息的周末改成工作日，来换取节日连休。'
    '#[p:3]#统计按标准五天工作制理解，不含个人年假；资料核查截至2026年9月18日，后面提到的日期是已公布安排，不代表已经发生。'
)


def sample_wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0\0' * 2400)
    return output.getvalue()


class PCSParserTests(unittest.TestCase):
    def test_plain_txt_auto_and_explicit_modes(self):
        for mode in ('auto', 'txt', 'pcs'):
            parsed = parse_source('hello', mode)
            self.assertTrue(parsed['valid'])
            self.assertEqual(parsed['source_format'], 'pcs' if mode == 'pcs' else 'txt')
            self.assertEqual(parsed['ast'][0]['text'], 'hello')
        self.assertEqual(parse_source('#[p:1]#hello', 'txt')['source_format'], 'pcs')

    def test_lexer_keeps_raw_source_spans_and_source_order(self):
        source = '前文#[ P : 2 ]#后文'
        parsed = parse_source(source)
        self.assertEqual([node['type'] for node in parsed['ast']], ['text', 'control', 'text'])
        self.assertEqual(parsed['ast'][1]['command'], 'p')
        self.assertEqual(parsed['ast'][1]['value'], 2)
        self.assertEqual(source[parsed['ast'][1]['source_start']:parsed['ast'][1]['source_end']], '#[ P : 2 ]#')
        self.assertEqual(len(lex_source(source)['tokens']), 3)

    def test_section_uses_first_colon_and_trim(self):
        parsed = parse_source('#[ SECTION : 中国:intro ]#内容')
        self.assertTrue(parsed['valid'])
        self.assertEqual(parsed['ast'][0]['value'], '中国:intro')

    def test_consecutive_controls_support_shared_or_separate_hashes(self):
        for joiner in ('#', '##'):
            source = '#[p:1]' + joiner + '[pause:800]' + joiner + '[section:intro]' + joiner + '[rate:0.8]#hello'
            result = compile_source(source)
            self.assertTrue(result['valid'], result['diagnostics'])
            self.assertEqual([n['type'] for n in result['ast']], ['control'] * 4 + ['text'])
            self.assertEqual([e['type'] for e in result['execution_plan'] if e['kind'] == 'event'],
                             ['page', 'pause', 'section', 'rate'])
            self.assertEqual(len(result['segments']), 1)
            self.assertEqual(result['segments'][0]['text'], 'hello')
            self.assertEqual(result['segments'][0]['page'], 1)
            self.assertEqual(result['segments'][0]['rate'], .8)
            self.assertEqual(result['segments'][0]['section'], 'intro')
            self.assertEqual(build_tts_payload(result['segments'][0], {})['text'], 'hello')
            for node in result['ast'][:4]:
                raw = source[node['source_start']:node['source_end']]
                self.assertTrue(raw.startswith('#['))
                self.assertTrue(raw.endswith(']#'))
        exact = compile_source('#[p:1]#[rate:0.8]#hello')
        self.assertEqual([n['value'] for n in exact['ast'] if n['type'] == 'control'], [1, .8])

    def test_all_invalid_values_have_spanned_diagnostics_and_no_plan(self):
        inputs = {
            '#[p:]#': 'PCS_INVALID_PAGE', '#[p:abc]#': 'PCS_INVALID_PAGE',
            '#[p:0]#': 'PCS_INVALID_PAGE', '#[p:-1]#': 'PCS_INVALID_PAGE',
            '#[p:1.0]#': 'PCS_INVALID_PAGE', '#[p:１]#': 'PCS_INVALID_PAGE',
            '#[pause:30001]#': 'PCS_INVALID_PAUSE', '#[pause:-1]#': 'PCS_INVALID_PAUSE',
            '#[pause:0.1]#': 'PCS_INVALID_PAUSE', '#[rate:9]#': 'PCS_INVALID_RATE',
            '#[rate:4]#': 'PCS_INVALID_RATE', '#[rate:NaN]#': 'PCS_INVALID_RATE',
            '#[rate:inf]#': 'PCS_INVALID_RATE', '#[rate:0.49]#': 'PCS_INVALID_RATE',
            '#[section: ]#': 'PCS_INVALID_SECTION', '#[section:' + 'a' * 81 + ']#': 'PCS_INVALID_SECTION',
            '#[abc:123]#': 'PCS_UNKNOWN_COMMAND', '#[xxx:1]#': 'PCS_UNKNOWN_COMMAND',
            '#[voice:narrator]#': 'PCS_UNKNOWN_COMMAND', '#[p=1]#': 'PCS_INVALID_SYNTAX',
            '#[p:1': 'PCS_UNCLOSED_TAG', '#[p:#[rate:1]#]#': 'PCS_NESTED_TAG',
        }
        for source, code in inputs.items():
            with self.subTest(source=source):
                result = compile_source('第一行\n' + source + '正文')
                self.assertFalse(result['valid'])
                self.assertFalse(result['execution_plan'])
                self.assertFalse(result['segments'])
                diagnostic = next(d for d in result['diagnostics'] if d['code'] == code)
                self.assertEqual(diagnostic['level'], 'error')
                self.assertEqual(diagnostic['line'], 2)
                self.assertEqual(diagnostic['column'], 1)
                self.assertEqual(diagnostic['source_start'], 4)
                self.assertGreater(diagnostic['source_end'], diagnostic['source_start'])

    def test_numeric_and_section_bounds(self):
        for source in ('#[p:1]#', '#[pause:0]#', '#[pause:30000]#',
                       '#[rate:0.5]#', '#[rate:2.0]#', '#[section:' + '中' * 80 + ']#'):
            self.assertTrue(parse_source(source)['valid'], source)

    def test_escape_is_literal_and_not_an_event(self):
        result = compile_source(r'显示 \#[p:1]# 字样。')
        self.assertTrue(result['valid'])
        self.assertEqual(result['source_format'], 'txt')
        self.assertEqual(len(result['execution_plan']), 1)
        self.assertEqual(result['segments'][0]['text'], '显示 #[p:1]# 字样。')
        self.assertTrue(result['segments'][0]['allow_control_literals'])
        self.assertEqual(build_tts_payload(result['segments'][0], {})['text'], '显示 #[p:1]# 字样。')

    def test_escape_alongside_real_controls_and_truncated_literal(self):
        source = r'#[p:2]#\#[rate:0.9]# 原样。'
        result = compile_source(source, limit=5)
        self.assertTrue(result['valid'])
        self.assertEqual(len([item for item in result['execution_plan'] if item['kind'] == 'event']), 1)
        for segment in result['segments']:
            build_tts_payload(segment, {})
        self.assertEqual(''.join(''.join(s['text'].split()) for s in result['segments']), '#[rate:0.9]#原样。')
        truncated = compile_source(r'\#[p:1', source_format='pcs')
        self.assertTrue(truncated['valid'])
        self.assertEqual(build_tts_payload(truncated['segments'][0], {})['text'], '#[p:1')

    def test_argument_validation(self):
        with self.assertRaises(ValueError):
            parse_source(None)
        with self.assertRaises(ValueError):
            parse_source('hello', 'invalid')
        for limit in (0, -1, True, 2.5, '160'):
            with self.assertRaises(ValueError):
                compile_source('hello', limit=limit)


class PCSCompilerTests(unittest.TestCase):
    def test_required_chinese_three_page_regression(self):
        result = compile_source(REGRESSION_SOURCE)
        self.assertTrue(result['valid'])
        self.assertEqual([n['type'] for n in result['ast']], ['control', 'text'] * 3)
        self.assertEqual([s['page'] for s in result['segments']], [1, 2, 3])
        self.assertEqual(result['stats']['pages'], 3)
        self.assertEqual([e['page'] for e in result['execution_plan'] if e['kind'] == 'event'], [1, 2, 3])
        for segment in result['segments']:
            self.assertNotIn('#[', build_tts_payload(segment, {})['text'])
        json.dumps(result, ensure_ascii=False, allow_nan=False)

    def test_every_control_is_a_hard_boundary_and_state_persists(self):
        inputs = (
            ('A#[p:2]#B', 'page', 2),
            ('A#[pause:800]#B', None, None),
            ('A#[rate:0.9]#B', 'rate', .9),
            ('A#[section:intro]#B', 'section', 'intro'),
        )
        for source, key, value in inputs:
            with self.subTest(source=source):
                result = compile_source(source, limit=1000)
                self.assertEqual([s['text'] for s in result['segments']], ['A', 'B'])
                self.assertEqual([i['kind'] for i in result['execution_plan']], ['speech', 'event', 'speech'])
                if key:
                    self.assertEqual(result['segments'][1][key], value)
        result = compile_source('#[p:1]##[rate:0.8]##[section:中国]#长长长长长长长长长长。', limit=4)
        self.assertGreater(len(result['segments']), 1)
        self.assertTrue(all((s['page'], s['rate'], s['section']) == (1, .8, '中国') for s in result['segments']))

    def test_control_order_never_changes(self):
        first = compile_source('A#[p:2]##[pause:800]#B')
        second = compile_source('A#[pause:800]##[p:2]#B')
        self.assertEqual([e['type'] for e in first['execution_plan'] if e['kind'] == 'event'], ['page', 'pause'])
        self.assertEqual([e['type'] for e in second['execution_plan'] if e['kind'] == 'event'], ['pause', 'page'])

    def test_span_mapping_survives_crlf_escapes_and_repeated_text(self):
        source = '#[p:1]#\r\n hello \r\nhello\r\n' + r'\#[p:8]#'
        result = compile_source(source)
        segments = result['segments']
        self.assertEqual([s['text'] for s in segments], ['hello', 'hello', '#[p:8]#'])
        self.assertEqual(source[segments[0]['source_start']:segments[0]['source_end']], 'hello')
        self.assertLess(segments[0]['source_end'], segments[1]['source_start'])
        self.assertEqual(source[segments[-1]['source_start']:segments[-1]['source_end']], r'\#[p:8]#')

    def test_txt_splitter_preserves_legacy_chapters_and_content(self):
        source = '第一章 来信\n“你好！”她说。' + '超长文字' * 30 + '，然后离开。\n第二章 回信\n明天见。'
        legacy = split_text(source, 40)
        compiled = compile_source(source, 40)['segments']
        self.assertEqual([(s['text'], s['chapter']) for s in compiled], [(s['text'], s['chapter']) for s in legacy])
        self.assertTrue(all(len(s['text']) <= 40 for s in compiled))
        self.assertEqual(''.join(source.split()), ''.join(''.join(s['text'].split()) for s in compiled))
        scoped = compile_source('第一章 来信\nA#[p:2]#B')['segments']
        self.assertEqual([s['chapter'] for s in scoped], ['第一章 来信'] * 3)

    def test_unchanged_recompile_reuses_generated_state_and_is_duplicate_safe(self):
        source = '#[p:1]#hello\nhello\nother'
        previous = compile_source(source)['segments']
        for index, segment in enumerate(previous):
            segment.update(status='done', duration=index + .1, audio_version='v' + str(index))
        previous[0]['overrides'] = {'speed': .8, 'seed': 99}
        previous[0]['fingerprint'] = segment_fingerprint(previous[0], {})
        before = copy.deepcopy(previous)
        result = compile_source(source, previous_segments=previous)
        self.assertEqual([s['id'] for s in result['segments']], [s['id'] for s in previous])
        self.assertEqual([s['audio_version'] for s in result['segments']], ['v0', 'v1', 'v2'])
        self.assertEqual(result['segments'][0]['overrides'], {'speed': .8, 'seed': 99})
        self.assertEqual(previous, before)
        added = compile_source(source + '\nhello', previous_segments=previous)['segments']
        self.assertEqual(len(set(s['id'] for s in added)), 4)
        self.assertEqual(added[-1]['status'], 'pending')

    def test_fingerprints_include_semantics_models_and_effective_voice(self):
        segment = compile_source('hello')['segments'][0]
        initial = segment_fingerprint(segment, {})
        self.assertEqual(initial, segment_fingerprint(segment, {'name': 'display only', 'gap': 2}))
        for field, value in (('gpt', 'new.ckpt'), ('sovits', 'new.pth'), ('reference', 'new.wav'),
                             ('prompt', 'different'), ('prompt_lang', 'ja'), ('text_lang', 'en'),
                             ('speed', .9), ('seed', 9)):
            self.assertNotEqual(initial, segment_fingerprint(segment, {field: value}), field)
        for field, value in (('page', 1), ('section', 'intro'), ('rate', .9)):
            self.assertNotEqual(initial, segment_fingerprint(dict(segment, **{field: value}), {}), field)
        overridden = dict(segment, overrides={'speed': .8})
        self.assertEqual(segment_fingerprint(overridden, {'speed': .9}), segment_fingerprint(overridden, {'speed': 1.1}))

    def test_changed_voice_or_scope_resets_audio_but_unchanged_text_keeps_audio(self):
        previous = compile_source('#[p:1]#A\nB')['segments']
        for segment in previous:
            segment.update(status='done', duration=1, audio_version='old')
        result = compile_source('#[p:1]#A\nchanged', previous_segments=previous)['segments']
        self.assertEqual(result[0]['id'], previous[0]['id'])
        self.assertEqual(result[1]['status'], 'pending')
        result = compile_source('#[p:2]#A\nB', previous_segments=previous)['segments']
        self.assertTrue(all(s['status'] == 'pending' for s in result))
        result = compile_source('#[p:1]#A\nB', voice={'speed': .9}, previous_segments=previous)['segments']
        self.assertTrue(all(s['status'] == 'pending' for s in result))

    def test_reordering_reuses_only_correct_content_and_does_not_reuse_running(self):
        previous = compile_source('A\nB')['segments']
        previous[0].update(status='done', audio_version='a')
        previous[1].update(status='running')
        result = compile_source('B\nA', previous_segments=previous)['segments']
        self.assertEqual(result[1]['id'], previous[0]['id'])
        self.assertEqual(result[1]['audio_version'], 'a')
        self.assertEqual(result[0]['status'], 'pending')

    def test_unchanged_audio_reuse_keeps_independent_previous_version_metadata(self):
        previous = compile_source('hello')['segments']
        previous[0].update(status='done', duration=1, audio_version='current')
        previous[0]['previous'] = dict(copy.deepcopy(previous[0]), audio_version='backup',
                                       overrides={'speed': .9})
        before = copy.deepcopy(previous)
        reused = compile_source('\nhello', previous_segments=previous)['segments'][0]
        self.assertEqual(reused['previous'], previous[0]['previous'])
        self.assertEqual(reused['source_start'], 1)
        reused['previous']['overrides']['speed'] = .8
        self.assertEqual(previous, before)
        changed = compile_source('hello', voice={'speed': 1.2}, previous_segments=previous)['segments'][0]
        self.assertNotIn('previous', changed)


class TTSPayloadTests(unittest.TestCase):
    def test_pcs_rate_and_inherited_segment_overrides(self):
        segments = compile_source('#[p:1]##[rate:0.8]#hello')['segments']
        segments[0]['overrides'] = {'speed': 1.5, 'seed': 99, 'text_lang': 'ja'}
        payload = build_tts_payload(segments[0], {'speed': 1.2})
        self.assertEqual(payload['speed_factor'], .8)
        self.assertEqual(payload['seed'], 99)
        self.assertEqual(payload['text_lang'], 'ja')
        self.assertEqual(payload['text_split_method'], 'cut5')
        self.assertEqual(payload['text'], 'hello')
        inherited = compile_source('hello')['segments'][0]
        self.assertIsNone(inherited['rate'])
        self.assertEqual(build_tts_payload(inherited, {'speed': 1.2})['speed_factor'], 1.2)
        inherited['overrides'] = {'speed': .85}
        self.assertEqual(build_tts_payload(inherited, {'speed': 1.2})['speed_factor'], .85)

    def test_raw_tags_and_injected_tags_are_rejected(self):
        for text in ('#[p:1]#hello', '#[unknown:1]#', '#[p:'):
            with self.assertRaises(ValueError):
                build_tts_payload({'text': text}, {})
            with self.assertRaises(ValueError):
                build_tts_payload({'text': text, 'allow_control_literals': True}, {})
        segment = compile_source(r'\#[p:1]#')['segments'][0]
        segment['text'] += '#[rate:2]#'
        with self.assertRaises(ValueError):
            build_tts_payload(segment, {})

    def test_infer_segment_uses_injected_rpc_and_actual_wav_duration(self):
        calls = []
        def rpc(path, payload, timeout=600):
            calls.append((path, payload, timeout))
            return sample_wav()
        segment = compile_source('#[rate:0.9]#hello')['segments'][0]
        raw, duration = infer_segment(segment, {}, rpc, timeout=10)
        self.assertEqual(raw, sample_wav())
        self.assertAlmostEqual(duration, .1)
        self.assertEqual(calls[0][0], '/tts')
        self.assertEqual(calls[0][1]['speed_factor'], .9)
        self.assertEqual(calls[0][2], 10)
        with self.assertRaises(Exception):
            infer_segment(segment, {}, lambda *args, **kwargs: b'invalid wav')
        truncated = bytearray(sample_wav())
        data_size = truncated.index(b'data') + 4
        truncated[data_size:data_size + 4] = struct.pack('<I', 48000)
        with self.assertRaisesRegex(ValueError, 'WAV'):
            infer_segment(segment, {}, lambda *args, **kwargs: bytes(truncated))


if __name__ == '__main__':
    unittest.main()
