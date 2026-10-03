"""HTTP, persistence and queue regressions for the complete PCS pipeline."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import wave
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kudio import server as a
from kudio.compiler import compile_source
from kudio.projects import apply_compilation, require_compiled

SCRIPT = '#[p:1]#开场。#[p:2]#第二页。#[pause:800]#[section:intro]#[rate:0.9]#继续。'


def audio_bytes(frames=2400):
    output = io.BytesIO()
    with wave.open(output, 'wb') as source:
        source.setnchannels(1)
        source.setsampwidth(2)
        source.setframerate(24000)
        source.writeframes(b'\0\0' * frames)
    return output.getvalue()


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_active = a.DATA, a.ACTIVE
        a.DATA, a.ACTIVE = Path(self.temp.name), None
        a.STOP.clear()
        self.server = a.LocalServer(('127.0.0.1', 0), a.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        a.DATA, a.ACTIVE = self.old_data, self.old_active
        a.STOP.clear()
        self.temp.cleanup()

    def request(self, route, data=None):
        request = Request('http://127.0.0.1:%d/%s' % (self.server.server_port, quote(route, safe='/?=&')),
                          data=None if data is None else json.dumps(data).encode('utf-8'),
                          headers={'Content-Type': 'application/json'})
        with urlopen(request) as response:
            return json.load(response)

    def create(self, text=SCRIPT, source_format='pcs'):
        return self.request('api/create', {'title': 'PCS回归', 'text': text,
                                           'source_format': source_format, 'limit': 160})

    def generate(self, project):
        payloads = []
        def rpc(path, payload=None, timeout=600):
            if payload:
                payloads.append(payload)
                return audio_bytes()
            return b'{}'
        with patch.object(a, 'rpc', side_effect=rpc):
            a.worker(project['id'])
        return a.read(project['id']), payloads

    def test_http_parse_compile_and_no_tts_side_effect(self):
        with patch.object(a, 'rpc') as rpc:
            parsed = self.request('api/pcs/parse', {'text': SCRIPT})
            compiled = self.request('api/pcs/compile', {'text': SCRIPT, 'limit': 160})
        rpc.assert_not_called()
        self.assertTrue(parsed['valid'])
        self.assertEqual(len(compiled['segments']), 3)
        self.assertEqual([s['page'] for s in compiled['segments']], [1, 2, 2])
        self.assertEqual(compiled['segments'][-1]['section'], 'intro')

    def test_complete_queue_and_three_exports_share_clock(self):
        project, payloads = self.generate(self.create())
        self.assertEqual(len(payloads), 3)
        self.assertEqual(payloads[-1]['speed_factor'], .9)
        self.assertTrue(all('#[' not in p['text'] for p in payloads))
        result = self.request('api/export-all', {'id': project['id']})
        self.assertEqual(len(result['exports']), 3)
        folder = a.project_dir(project['id']) / 'exports'
        wav_path = folder / result['exports'][0]
        kson = json.loads(wav_path.with_suffix('.kson').read_text(encoding='utf-8'))
        self.assertEqual(kson['source']['format'], 'pcs')
        self.assertEqual(kson['timing_status'], 'exact')
        with wave.open(str(wav_path), 'rb') as audio:
            self.assertEqual(kson['duration_ms'], round(audio.getnframes() * 1000 / audio.getframerate()))
        self.assertEqual(kson['duration_ms'], 1400)  # 3*.1 + implicit .3 + explicit .8.
        self.assertIn('00:00:01,300 --> 00:00:01,400', wav_path.with_suffix('.srt').read_text(encoding='utf-8-sig'))
        self.assertTrue(self.request('api/export-kson', {'id': project['id']})['exports'])

    def test_invalid_source_cannot_infer_or_overwrite_valid_plan(self):
        project = self.create()
        before = (a.project_dir(project['id']) / 'project.json').read_bytes()
        with self.assertRaises(HTTPError):
            self.request('api/project/source', {'id': project['id'], 'text': '#[xxx:1]#B'})
        self.assertEqual((a.project_dir(project['id']) / 'project.json').read_bytes(), before)
        invalid = self.create('#[p:abc]#正文')
        self.assertTrue(invalid['diagnostics'])
        self.assertEqual(invalid['segments'], [])
        with patch.object(a, 'rpc') as rpc, self.assertRaises(HTTPError):
            self.request('api/start', {'id': invalid['id']})
        rpc.assert_not_called()

    def test_recompile_reuses_done_audio_and_removes_obsolete_plan_entries(self):
        project, unused = self.generate(self.create('A#[pause:800]#B'))
        original = copy.deepcopy(project['segments'])
        preserved = a.project_dir(project['id']) / (original[0]['id'] + '.wav')
        before = preserved.read_bytes()
        updated = self.request('api/project/source', {'id': project['id'], 'text': 'A#[pause:500]#C'})
        self.assertEqual(updated['segments'][0]['id'], original[0]['id'])
        self.assertEqual(updated['segments'][0]['audio_version'], original[0]['audio_version'])
        self.assertEqual(updated['segments'][0]['status'], 'done')
        self.assertEqual(updated['segments'][1]['status'], 'pending')
        self.assertNotIn(original[1]['id'], [i.get('segment_id') for i in updated['execution_plan']])
        self.assertEqual(preserved.read_bytes(), before)
        with self.assertRaises(HTTPError):
            self.request('api/export-kson', {'id': project['id']})

    def test_legacy_migration_keeps_audio_and_recovers_interrupted_queue(self):
        pid = 'b' * 32
        folder = a.DATA / pid
        folder.mkdir()
        segments = a.split_text('你好。\n下次见。')
        segments[0].update(status='done', duration=123)
        segments[1]['status'] = 'running'
        old = {'id': pid, 'title': '旧项目', 'voice': a.defaults(), 'segments': segments,
               'exports': [], 'error': ''}
        (folder / 'project.json').write_text(json.dumps(old), encoding='utf-8')
        (folder / (segments[0]['id'] + '.wav')).write_bytes(audio_bytes())
        a.initialize()
        migrated = a.read(pid)
        self.assertEqual(migrated['schema_version'], 2)
        self.assertEqual(migrated['source_format'], 'legacy')
        self.assertEqual(migrated['segments'][1]['status'], 'pending')
        self.assertEqual((a.project_dir(pid) / (segments[0]['id'] + '.wav')).read_bytes(), audio_bytes())
        self.assertTrue((a.project_dir(pid) / 'project.schema-1.backup.json').is_file())
        a.export_chapters(migrated, migrated['segments'][:1], '旧音频')

    def test_source_authority_and_hash_guard(self):
        project = self.create('A#[p:2]#B')
        with self.assertRaises(HTTPError):
            self.request('api/edit-project', {'id': project['id'], 'action': 'edit-segment',
                                              'segment': project['segments'][0]['id'], 'text': '不同正文'})
        project['source_text'] += '更改'
        with self.assertRaises(ValueError):
            require_compiled(project)

    def test_rate_overrides_do_not_regenerate_when_only_inherited_speed_changes(self):
        project, unused = self.generate(self.create('#[rate:0.9]#正文'))
        with patch.object(a, 'validate_voice'):
            a.apply_voice(project, dict(project['voice'], speed=1.2))
        self.assertEqual(project['segments'][0]['status'], 'done')
        self.assertEqual(project['segments'][0]['rate'], .9)

    def test_restore_audio_rejects_old_voice_fingerprint(self):
        project, unused = self.generate(self.create('正文'))
        segment = project['segments'][0]
        with patch.object(a, 'validate_voice'):
            a.mutate_project(project, 'edit-segment', {'segment': segment['id'],
                             'text': segment['text'], 'overrides': {'seed': 99}})
            a.apply_voice(project, dict(project['voice'], speed=1.3))
        a.save(project)
        before = (a.project_dir(project['id']) / (segment['id'] + '.wav')).read_bytes()
        with self.assertRaises(HTTPError):
            self.request('api/restore-audio', {'id': project['id'], 'segment': segment['id']})
        self.assertEqual((a.project_dir(project['id']) / (segment['id'] + '.wav')).read_bytes(), before)

    def test_txt_detection_source_spans_and_exact_download(self):
        project = self.create('  #[p:1]#你好。\r\n', 'txt')
        self.assertEqual(project['source_format'], 'pcs')
        self.assertEqual(project['source_text'], '  #[p:1]#你好。\r\n')
        plain = self.create('hello', 'txt')
        self.assertEqual(plain['source_format'], 'txt')
        self.assertEqual(plain['timeline']['timing_status'], 'estimated')
        with self.assertRaises(HTTPError):
            self.request('api/export-kson', {'id': plain['id']})
        project, unused = self.generate(project)
        result = self.request('api/export-kson', {'id': project['id']})
        exported = self.request('download?id=%s&file=%s' % (project['id'], result['exports'][0]))
        self.assertEqual(exported['format'], 'kson')


if __name__ == '__main__':
    unittest.main()
