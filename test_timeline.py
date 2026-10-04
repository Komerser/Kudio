"""Regression tests for the common WAV/SRT/KSON clock and ordered controls."""
import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest
import wave
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kudio.exporters import build_kson, merge_wav, write_kson, write_srt
from kudio.timeline import build_timeline


class TimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def audio(self, sid, frames=2400, rate=24000, channels=1, width=2, data=None):
        path = self.folder / (sid + '.wav')
        if data is None:
            data = (b'\x80' if width == 1 else b'\x00') * frames * channels * width
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(channels)
            output.setsampwidth(width)
            output.setframerate(rate)
            output.writeframes(data)
        return path

    def project(self, count=2, gap=.3):
        segments = [{'id': 's%d' % index, 'text': '第%d句' % index,
                     'status': 'done', 'duration': 999, 'rate': None,
                     'page': None, 'section': None} for index in range(count)]
        for segment in segments:
            self.audio(segment['id'])
        return {'id': 'book', 'title': '时间轴测试', 'voice': {'gap': gap, 'speed': 1},
                'source': {'format': 'pcs'}, 'segments': segments}

    def speech(self, sid):
        return {'kind': 'speech', 'segment_id': sid}

    def event(self, kind, **metadata):
        return dict({'kind': 'event', 'type': kind}, **metadata)

    def test_exact_ignores_cached_duration_and_exports_one_clock(self):
        project = self.project(gap=.33333)
        project['segments'][0]['text'] = '第一行\n\n第二行'
        timeline = build_timeline(project, self.folder, exact=True)
        self.assertEqual(timeline['duration_ms'], 533)
        self.assertEqual(timeline['segments'][1]['start_ms'], 433)
        self.assertEqual(timeline['total_frames'], 4800 + 7999)
        self.assertEqual(timeline['timing_status'], 'exact')
        merge_wav(self.folder / 'book.wav', timeline, self.folder)
        write_srt(self.folder / 'book.srt', timeline)
        write_kson(self.folder / 'book.kson', project, timeline)
        with wave.open(str(self.folder / 'book.wav'), 'rb') as audio:
            self.assertEqual(audio.getnframes(), timeline['total_frames'])
            self.assertLessEqual(abs(audio.getnframes() * 1000 / audio.getframerate()
                                     - timeline['duration_ms']), .5)
        kson = json.loads((self.folder / 'book.kson').read_text(encoding='utf-8'))
        self.assertEqual(kson['segments'], timeline['segments'])
        self.assertEqual(kson['source'], {'format': 'pcs', 'pcs_version': '0.1'})
        self.assertEqual(kson['timebase'], 'ms')
        self.assertEqual(kson['version'], '0.1')
        subtitle = (self.folder / 'book.srt').read_text(encoding='utf-8-sig')
        self.assertIn('00:00:00,000 --> 00:00:00,100\n第一行\n第二行', subtitle)
        self.assertIn('00:00:00,433 --> 00:00:00,533', subtitle)

    def test_voice_events_rebase_for_range_and_export_saved_role_metadata(self):
        project = self.project()
        project['source_format'] = 'pcs'
        project['role_snapshots'] = {'elder': {'id': 'elder', 'name': '老人', 'voice': {'speed': .8}}}
        project['voice_bindings'] = {'male_elder': 'elder'}
        for segment in project['segments']:
            segment.update(source_format='pcs', voice_label='male_elder')
        project['execution_plan'] = [self.event('voice', label='male_elder'),
                                     self.speech('s0'), self.speech('s1')]
        timeline = build_timeline(project, self.folder, exact=True, members=project['segments'][1:])
        self.assertEqual(timeline['events'][0]['type'], 'voice')
        self.assertEqual(timeline['events'][0]['time_ms'], 0)
        self.assertTrue(timeline['events'][0]['inherited'])
        self.assertEqual(timeline['segments'][0]['speech']['rate'], .8)
        self.assertEqual(timeline['segments'][0]['role_name'], '老人')
        self.assertEqual(timeline['duration_ms'], 100)

    def test_rounds_cumulative_frame_boundaries_not_individual_durations(self):
        project = self.project(count=100, gap=0)
        for segment in project['segments']:
            self.audio(segment['id'], frames=445, rate=44100)
        timeline = build_timeline(project, self.folder, exact=True)
        self.assertEqual(timeline['duration_ms'], 1009)
        self.assertEqual(timeline['total_frames'], 44500)
        for index, segment in enumerate(timeline['segments']):
            self.assertEqual(segment['start_ms'], int(index * 445 * 1000 / 44100 + .5))
            self.assertEqual(segment['end_ms'], int((index + 1) * 445 * 1000 / 44100 + .5))
            self.assertEqual(segment['duration_ms'], segment['end_ms'] - segment['start_ms'])
        merge_wav(self.folder / 'many.wav', timeline, self.folder)
        with wave.open(str(self.folder / 'many.wav'), 'rb') as source:
            self.assertEqual(source.getnframes(), 44500)

    def test_pause_replaces_default_gap_even_when_zero(self):
        for pause, expected in ((800, 1000), (0, 200)):
            project = self.project()
            project['execution_plan'] = [self.speech('s0'), self.event('pause', duration_ms=pause),
                                         self.speech('s1')]
            timeline = build_timeline(project, self.folder, exact=True)
            self.assertEqual(timeline['duration_ms'], expected)
            self.assertEqual(timeline['segments'][1]['start_ms'], 100 + pause)
            self.assertEqual([s['reason'] for s in timeline['items'] if s['kind'] == 'silence'], ['pause'])
            merge_wav(self.folder / 'pause.wav', timeline, self.folder)
            with wave.open(str(self.folder / 'pause.wav'), 'rb') as source:
                self.assertEqual(source.getnframes(), expected * 24)

    def test_source_event_order_changes_page_time(self):
        project = self.project()
        page = self.event('page', page=2)
        pause = self.event('pause', duration_ms=800)
        project['execution_plan'] = [self.speech('s0'), page, pause, self.speech('s1')]
        before = build_timeline(project, self.folder, exact=True)
        project['execution_plan'] = [self.speech('s0'), pause, page, self.speech('s1')]
        after = build_timeline(project, self.folder, exact=True)
        self.assertEqual(before['events'][0]['time_ms'], 100)
        self.assertEqual(after['events'][1]['time_ms'], 900)
        self.assertEqual(before['segments'][1]['start_ms'], after['segments'][1]['start_ms'])
        self.assertEqual([event['type'] for event in before['events']], ['page', 'pause'])
        self.assertEqual([event['type'] for event in after['events']], ['pause', 'page'])

    def test_metadata_event_occurs_before_implicit_gap(self):
        project = self.project()
        project['execution_plan'] = [self.speech('s0'), self.event('page', page=2),
                                     self.event('rate', value=.8), self.event('section', name='intro'),
                                     self.speech('s1')]
        project['segments'][1].update(page=2, section='intro', rate=.8)
        timeline = build_timeline(project, self.folder, exact=True)
        self.assertEqual([e['time_ms'] for e in timeline['events']], [100, 100, 100])
        self.assertEqual(timeline['segments'][1]['start_ms'], 400)
        self.assertEqual(timeline['segments'][1]['speech']['rate'], .8)
        self.assertEqual(timeline['segments'][1]['section'], 'intro')

    def test_leading_trailing_consecutive_pauses_keep_order(self):
        project = self.project()
        project['execution_plan'] = [self.event('pause', duration_ms=100), self.speech('s0'),
                                     self.event('pause', duration_ms=50), self.event('pause', duration_ms=0),
                                     self.event('page', page=2), self.event('pause', duration_ms=50),
                                     self.speech('s1'), self.event('pause', duration_ms=500)]
        timeline = build_timeline(project, self.folder, exact=True, members=project['segments'])
        self.assertEqual(timeline['duration_ms'], 900)
        self.assertEqual(timeline['segments'][0]['start_ms'], 100)
        self.assertEqual(timeline['segments'][1]['start_ms'], 300)
        self.assertEqual(timeline['events'][3]['time_ms'], 250)
        self.assertEqual(timeline['events'][-1]['end_ms'], 900)
        self.assertTrue(all(i.get('reason') != 'gap' for i in timeline['items']))

    def test_range_rebases_and_retains_latest_metadata(self):
        project = self.project(count=3)
        project['execution_plan'] = [self.event('page', page=1), self.event('rate', value=.9),
                                     self.event('section', name='intro'), self.speech('s0'),
                                     self.event('pause', duration_ms=800), self.event('page', page=2),
                                     self.speech('s1'), self.event('pause', duration_ms=50),
                                     self.speech('s2'), self.event('pause', duration_ms=500)]
        project['segments'][1].update(page=2, section='intro', rate=.9)
        project['segments'][2].update(page=2, section='intro', rate=.9)
        project['segments'][0]['status'] = 'pending'
        timeline = build_timeline(project, self.folder, exact=True, members=project['segments'][1:])
        self.assertEqual(timeline['duration_ms'], 250)
        self.assertEqual(timeline['segments'][0]['start_ms'], 0)
        self.assertEqual(timeline['segments'][1]['start_ms'], 150)
        self.assertEqual([e['type'] for e in timeline['events']], ['rate', 'section', 'page', 'pause'])
        self.assertEqual([e['time_ms'] for e in timeline['events'][:-1]], [0, 0, 0])
        self.assertTrue(all(e['inherited'] for e in timeline['events'][:-1]))
        self.assertEqual(timeline['events'][-1]['duration_ms'], 50)

    def test_pending_and_invalid_audio_rejected_exact_but_available_as_draft(self):
        project = self.project(count=3)
        project['segments'][1]['status'] = 'pending'
        # Existing audio for pending speech is stale and must not count as done.
        with self.assertRaises(ValueError):
            build_timeline(project, self.folder, exact=True)
        timeline = build_timeline(project, self.folder)
        self.assertEqual(timeline['timing_status'], 'estimated')
        self.assertFalse(timeline['segments'][0]['estimated'])
        self.assertTrue(timeline['segments'][1]['estimated'])
        self.assertTrue(timeline['segments'][2]['estimated'])
        self.assertEqual(timeline['segments'][2]['duration_ms'], 100)
        self.assertEqual(build_kson(project, timeline)['timing_status'], 'estimated')
        with self.assertRaises(ValueError):
            write_kson(self.folder / 'draft.kson', project, timeline)
        with self.assertRaises(ValueError):
            write_srt(self.folder / 'draft.srt', timeline)
        project['segments'][1]['status'] = 'done'
        (self.folder / 's1.wav').write_bytes(b'not wav')
        with self.assertRaises(ValueError):
            build_timeline(project, self.folder, exact=True)
        self.assertEqual(build_timeline(project, self.folder)['timing_status'], 'estimated')
        (self.folder / 's1.wav').unlink()
        with self.assertRaises(ValueError):
            build_timeline(project, self.folder, exact=True)

    def test_header_duration_cannot_hide_truncated_wav(self):
        project = self.project(count=1)
        path = self.folder / 's0.wav'
        path.write_bytes(path.read_bytes()[:-10])
        with self.assertRaisesRegex(ValueError, '不完整'):
            build_timeline(project, self.folder, exact=True)
        self.assertEqual(build_timeline(project, self.folder)['timing_status'], 'estimated')

    def test_mixed_formats_use_normalized_frames_for_all_exports(self):
        project = self.project(count=3)
        self.audio('s1', frames=3200, rate=32000)
        original = (self.folder / 's1.wav').read_bytes()
        @contextmanager
        def normalize(path, fmt):
            if path.stem == 's1':
                data = io.BytesIO()
                with wave.open(data, 'wb') as output:
                    output.setnchannels(fmt[0]); output.setsampwidth(fmt[1]); output.setframerate(fmt[2])
                    output.writeframes(b'\0\0' * 2401)
                data.seek(0)
                with wave.open(data, 'rb') as source:
                    yield source
            else:
                with wave.open(str(path), 'rb') as source:
                    yield source
        with self.assertRaises(ValueError):
            build_timeline(project, self.folder, exact=True)
        self.assertEqual(build_timeline(project, self.folder)['timing_status'], 'estimated')
        timeline = build_timeline(project, self.folder, exact=True, format_audio=normalize)
        self.assertEqual(timeline['total_frames'], 2400 + 2401 + 2400 + 14400)
        self.assertEqual(timeline['audio_format'], [1, 2, 24000])
        merge_wav(self.folder / 'normalized.wav', timeline, self.folder, format_audio=normalize)
        with wave.open(str(self.folder / 'normalized.wav'), 'rb') as source:
            self.assertEqual(source.getnframes(), timeline['total_frames'])
        self.assertEqual((self.folder / 's1.wav').read_bytes(), original)

    def test_pcm_width_channels_and_unsigned_silence_preserved(self):
        for width in (1, 3):
            project = self.project(gap=.1)
            for segment in project['segments']:
                data = b'\x10' * 100 * 2 * width
                self.audio(segment['id'], frames=100, rate=1000, channels=2, width=width, data=data)
            timeline = build_timeline(project, self.folder, exact=True)
            merge_wav(self.folder / 'pcm.wav', timeline, self.folder)
            with wave.open(str(self.folder / 'pcm.wav'), 'rb') as source:
                self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate()),
                                 (2, width, 1000))
                self.assertEqual(source.readframes(100), b'\x10' * 100 * 2 * width)
                expected_silence = (b'\x80' if width == 1 else b'\x00') * 100 * 2 * width
                self.assertEqual(source.readframes(100), expected_silence)

    def test_changed_audio_preserves_existing_export_and_size_guard(self):
        project = self.project(count=1)
        timeline = build_timeline(project, self.folder, exact=True)
        target = self.folder / 'stable.wav'
        target.write_bytes(b'existing export')
        self.audio('s0', frames=4800)
        with self.assertRaisesRegex(ValueError, '音频已变更'):
            merge_wav(target, timeline, self.folder)
        self.assertEqual(target.read_bytes(), b'existing export')
        oversized = copy.deepcopy(timeline)
        oversized['total_frames'] = 0xFFFFFFFF
        with self.assertRaisesRegex(ValueError, '4GB'):
            merge_wav(target, oversized, self.folder)
        self.assertEqual(target.read_bytes(), b'existing export')
        self.assertFalse(list(self.folder.glob('*.tmp')))

    def test_invalid_plan_references_are_not_exported(self):
        project = self.project()
        project['execution_plan'] = [self.speech('s0')]
        with self.assertRaisesRegex(ValueError, '缺少'):
            build_timeline(project, self.folder, exact=True)
        project['execution_plan'] = [self.speech('s0'), self.speech('s0'), self.speech('s1')]
        with self.assertRaisesRegex(ValueError, '重复'):
            build_timeline(project, self.folder, exact=True)

    def test_empty_draft_and_flat_project_source_metadata(self):
        project = self.project(count=0)
        timeline = build_timeline(project, self.folder)
        self.assertEqual(timeline['timing_status'], 'estimated')
        self.assertEqual(timeline['duration_ms'], 0)
        project.update(source_format='pcs', pcs_version='0.1', source_hash='example-hash')
        self.assertEqual(build_kson(project, timeline)['source'],
                         {'format': 'pcs', 'pcs_version': '0.1', 'hash': 'example-hash'})

    def test_file_identifiers_cannot_escape_audio_directory(self):
        project = self.project(count=1)
        project['segments'][0]['id'] = '../elsewhere'
        with self.assertRaisesRegex(ValueError, '标识无效'):
            build_timeline(project, self.folder, exact=True)


if __name__ == '__main__':
    unittest.main()
