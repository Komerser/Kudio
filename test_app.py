import copy
import importlib.util
import io
import tempfile
import unittest
import wave
import threading
import time
from unittest.mock import Mock, patch
from pathlib import Path

spec = importlib.util.spec_from_file_location('workstation', Path(__file__).with_name('app.py'))
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def wav():
    b = io.BytesIO()
    with wave.open(b, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
        f.writeframes(b'\0\0' * 2400)
    return b.getvalue()


class WorkstationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_rpc = a.DATA, a.rpc
        a.DATA = Path(self.temp.name)
        a.STOP.clear()
        a.ACTIVE = None

    def tearDown(self):
        a.DATA, a.rpc = self.old_data, self.old_rpc
        self.temp.cleanup()

    def project(self):
        p = {'id': 'a' * 32, 'title': '测试', 'voice': a.defaults(), 'error': '', 'exports': [],
             'segments': a.split_text('第一章 来信\n你好。今天阳光很好！\n第二章 回信\n明天见。', 40)}
        a.save(p)
        return p

    def test_split_preserves_content_and_length(self):
        text = '第一章 测试\n“你好！”她说。' + '超长文字' * 100 + '，然后离开。\n第二章 归来\n尾声。'
        pieces = a.split_text(text, 40)
        self.assertTrue(all(len(s['text']) <= 40 for s in pieces))
        self.assertEqual(''.join(text.split()), ''.join(''.join(s['text'].split()) for s in pieces))
        self.assertEqual(pieces[-1]['chapter'], '第二章 归来')

    def test_resume_and_export(self):
        p = self.project()
        calls = []
        def fake(path, payload=None, timeout=600):
            calls.append(path)
            return wav() if payload else b'{}'
        a.rpc = fake
        a.worker(p['id'])
        generated = a.read(p['id'])
        self.assertTrue(all(s['status'] == 'done' for s in generated['segments']))
        self.assertEqual(calls.count('/tts'), len(p['segments']))
        a.worker(p['id'])
        self.assertEqual(calls.count('/tts'), len(p['segments']))
        files = a.export_chapters(generated)
        self.assertEqual(len(files), 2)
        with wave.open(str(a.project_dir(p['id']) / 'exports' / files[0])) as f:
            expected = len([s for s in p['segments'] if s['chapter'] == '第一章 来信'])
            self.assertAlmostEqual(f.getnframes()/f.getframerate(), expected*.1+(expected-1)*.3)

    def test_failure_and_recovery(self):
        p = self.project()
        def fake(path, payload=None, timeout=600):
            if payload:
                a.STOP.set()
                raise ValueError('模拟错误')
            return b'{}'
        a.rpc = fake
        a.worker(p['id'])
        p = a.read(p['id'])
        self.assertEqual(p['segments'][0]['status'], 'failed')
        self.assertIn('模拟错误', p['segments'][0]['error'])
        self.assertIsNone(a.ACTIVE)
        p['segments'][1]['status'] = 'running'
        a.save(p); a.initialize()
        self.assertEqual(a.read(p['id'])['segments'][1]['status'], 'pending')
        with self.assertRaises(ValueError): a.export_chapters(p)

    def test_invalid_audio_and_path(self):
        with self.assertRaises(Exception): a.audio_info(b'not wav')
        with self.assertRaises(ValueError): a.project_dir('../bad')

    def test_pause_finishes_current_segment(self):
        p = self.project()
        def fake(path, payload=None, timeout=600):
            if payload:
                a.STOP.set()
                return wav()
            return b'{}'
        a.rpc = fake
        a.worker(p['id'])
        statuses = [s['status'] for s in a.read(p['id'])['segments']]
        self.assertEqual(statuses[0], 'done')
        self.assertTrue(all(s == 'pending' for s in statuses[1:]))

    def test_export_normalizes_mixed_formats_without_changing_sources(self):
        p = self.project()
        for s in p['segments']:
            s['status'] = 'done'
            (a.project_dir(p['id']) / (s['id'] + '.wav')).write_bytes(wav())
        with wave.open(str(a.project_dir(p['id']) / (p['segments'][1]['id'] + '.wav')), 'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(32000)
            f.writeframes(b'\0\0' * 3200)
        original = (a.project_dir(p['id']) / (p['segments'][1]['id'] + '.wav')).read_bytes()
        files = a.export_chapters(p)
        with wave.open(str(a.project_dir(p['id']) / 'exports' / files[0])) as f:
            self.assertEqual(f.getframerate(), 24000)
            self.assertAlmostEqual(f.getnframes()/f.getframerate(), .5, places=3)
        self.assertEqual((a.project_dir(p['id']) / (p['segments'][1]['id'] + '.wav')).read_bytes(), original)

    def test_stop_waits_for_current_segment(self):
        a.ACTIVE = 'current'
        stopped = threading.Event()
        with patch.object(a, 'stop_engine', side_effect=stopped.set):
            thread = threading.Thread(target=a.finish_queue_and_stop_engine)
            thread.start()
            self.assertTrue(a.STOP.wait(1))
            self.assertFalse(stopped.is_set())
            with a.LOCK:
                a.ACTIVE = None
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertTrue(stopped.is_set())

    def test_stop_terminates_only_managed_child(self):
        child = Mock()
        child.poll.return_value = None
        with patch.object(a, 'PROCESS', child):
            a.stop_engine()
            child.terminate.assert_called_once()
            child.wait.assert_called_once_with(timeout=15)
            self.assertIsNone(a.PROCESS)

    def test_stop_rejects_unrecognized_service(self):
        with patch.object(a, 'PROCESS', None), patch.object(a, 'rpc', return_value=b'{"paths":{}}') as rpc:
            with self.assertRaises(ValueError):
                a.stop_engine()
            self.assertEqual(rpc.call_count, 1)

    def test_stop_external_api_accepts_exit_disconnect(self):
        spec = b'{"paths":{"/tts":{},"/control":{},"/set_sovits_weights":{}}}'
        with patch.object(a, 'PROCESS', None), patch.object(a, 'rpc', side_effect=[spec, ConnectionError(), ConnectionError()]) as rpc:
            a.stop_engine()
            self.assertEqual(rpc.call_args_list[1].args[0], '/control?command=exit')

    def test_voice_presets_persist_without_changing_project(self):
        p = self.project()
        voice = a.defaults()
        with patch.object(a, 'validate_voice'):
            saved = a.save_preset(voice)
            self.assertEqual(a.read_presets()[0], saved)
            self.assertEqual(a.read(p['id']), p)
            with self.assertRaises(ValueError):
                a.save_preset(voice)
            voice['speed'] = 0.95
            updated = a.save_preset(voice, saved['id'])
            self.assertEqual(updated['id'], saved['id'])
            self.assertEqual(len(a.read_presets()), 1)
            self.assertEqual(a.read_presets()[0]['voice']['speed'], 0.95)
            self.assertEqual(a.read(p['id'])['voice']['speed'], 1.0)
            with self.assertRaises(ValueError):
                a.save_preset(voice, 'missing')

    def test_invalid_preset_does_not_overwrite_saved_file(self):
        with patch.object(a, 'validate_voice'):
            a.save_preset(a.defaults())
        before = (a.DATA / 'voice_presets.json').read_bytes()
        with patch.object(a, 'validate_voice', side_effect=ValueError('文件不存在')):
            with self.assertRaises(ValueError):
                a.save_preset(a.defaults(), a.read_presets()[0]['id'])
        self.assertEqual((a.DATA / 'voice_presets.json').read_bytes(), before)

    def test_timeline_marks_unknown_prefix_and_includes_gaps(self):
        p = self.project()
        for s in p['segments']: s.update(status='done', duration=2)
        view = a.project_view(p)
        self.assertEqual(view['segments'][1]['timeline']['start'], 2.3)
        self.assertFalse(view['timeline_estimated'])
        p['segments'][0]['status'] = 'pending'
        view = a.project_view(p)
        self.assertTrue(view['segments'][1]['timeline']['estimated'])
        self.assertNotIn('timeline', p['segments'][0])

    def test_delete_restore_and_inclusive_ranges(self):
        p = self.project()
        original = copy.deepcopy(p['segments'])
        first, last = original[0]['id'], original[2]['id']
        self.assertEqual(len(a.selected_range(p, first, last)), 3)
        a.mutate_project(p, 'group', {'start': first, 'end': last, 'name': '测试范围'})
        a.mutate_project(p, 'delete-segment', {'segment': original[1]['id']})
        self.assertEqual(len(a.selected_range(p, first, last)), 2)
        a.mutate_project(p, 'restore-segment', {})
        self.assertEqual(p['segments'], original)
        with self.assertRaises(ValueError): a.selected_range(p, last, first)
        a.mutate_project(p, 'delete-segment', {'segment': first})
        with self.assertRaises(ValueError): a.selected_range(p, first, last)

    def test_range_export_does_not_require_whole_book(self):
        p = self.project()
        for s in p['segments'][:2]:
            s.update(status='done', duration=.1)
            (a.project_dir(p['id']) / (s['id'] + '.wav')).write_bytes(wav())
        files = a.export_chapters(p, p['segments'][:2], '分组测试')
        self.assertEqual(len(files), 1)
        with wave.open(str(a.project_dir(p['id']) / 'exports' / files[0])) as f:
            self.assertAlmostEqual(f.getnframes()/f.getframerate(), .5)
        self.assertEqual(p['segments'][2]['status'], 'pending')

    def test_single_regeneration_only_processes_target_and_uses_overrides(self):
        p = self.project()
        target = p['segments'][1]
        target['overrides'] = {'speed': .85, 'seed': 99, 'text_lang': 'ja'}
        a.save(p)
        calls=[]
        def fake(path, payload=None, timeout=600):
            if payload: calls.append(payload)
            return wav() if payload else b'{}'
        a.rpc=fake
        a.worker(p['id'], {target['id']})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['speed_factor'], .85)
        self.assertEqual(calls[0]['seed'], 99)
        self.assertEqual(a.read(p['id'])['segments'][0]['status'], 'pending')
        self.assertEqual(a.read(p['id'])['segments'][1]['status'], 'done')

    def test_gap_and_name_do_not_invalidate_generated_audio(self):
        p=self.project()
        p['segments'][0].update(status='done', duration=2)
        p['exports']=['old.wav']
        v=dict(p['voice'], gap=.5, name='新名称')
        with patch.object(a, 'validate_voice'):
            a.apply_voice(p,v)
            self.assertEqual(p['segments'][0]['status'],'done')
            self.assertEqual(p['segments'][0]['duration'],2)
            self.assertEqual(p['exports'],[])
            a.apply_voice(p,dict(v,speed=.9))
            self.assertEqual(p['segments'][0]['status'],'pending')

    def test_suggestions_require_acceptance_and_preserve_segments(self):
        p = self.project()
        original = copy.deepcopy(p['segments'])
        suggestions = a.project_view(p)['suggestions']
        self.assertEqual(len(suggestions), 2)
        self.assertFalse(p.get('groups'))
        first = suggestions[0]
        a.mutate_project(p, 'dismiss-suggestion', {'suggestion_id': first['id']})
        self.assertEqual(len(a.suggest_groups(p)), 1)
        a.mutate_project(p, 'reset-suggestions', {})
        body = dict(first, suggestion_id=first['id'], color='#547cc2')
        a.mutate_project(p, 'group', body)
        self.assertEqual(p['groups'][0]['source'], 'auto')
        self.assertEqual(len(a.suggest_groups(p)), 1)
        with self.assertRaises(ValueError): a.mutate_project(p, 'group', body)
        group = p['groups'][0]
        a.mutate_project(p, 'update-group', dict(body, group=group['id'], name='我的分段', end=original[-1]['id']))
        self.assertEqual(len(a.selected_range(p, group['start'], group['end'])), len(original))
        self.assertEqual(p['segments'], original)

    def test_whole_book_export_api_ignores_chapter_suggestions(self):
        p = self.project()
        for s in p['segments']:
            s.update(status='done', duration=.1)
            (a.project_dir(p['id']) / (s['id'] + '.wav')).write_bytes(wav())
        a.save(p)
        server = a.LocalServer(('127.0.0.1', 0), a.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = a.Request('http://127.0.0.1:%s/api/export-all' % server.server_port,
                            data=a.json.dumps({'id': p['id']}).encode(), headers={'Content-Type': 'application/json'})
            with a.urlopen(req) as response: result = a.json.load(response)
            self.assertEqual(len(result['exports']), 2)
            self.assertTrue(result['exports'][1].endswith('.srt'))
            with wave.open(str(a.project_dir(p['id']) / 'exports' / result['exports'][0])) as f:
                self.assertAlmostEqual(f.getnframes()/f.getframerate(), len(p['segments'])*.1+(len(p['segments'])-1)*p['voice']['gap'])
            self.assertFalse(a.read(p['id']).get('groups'))
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_srt_matches_wav_frames_gap_and_selected_range(self):
        p = self.project()
        members = p['segments'][1:3]
        p['voice']['gap'] = .33333
        for s in members:
            s.update(status='done', duration=999)  # Ignore stale metadata.
            (a.project_dir(p['id']) / (s['id'] + '.wav')).write_bytes(wav())
        members[0]['text'] = '第一行\n\n第二行'
        files = a.export_chapters(p, members, '字幕测试')
        path = a.project_dir(p['id']) / 'exports' / files[0]
        auto = path.with_suffix('.srt').read_text(encoding='utf-8-sig')
        self.assertIn('1\n00:00:00,000 --> 00:00:00,100\n第一行\n第二行\n', auto)
        self.assertIn('2\n00:00:00,433 --> 00:00:00,533', auto)
        filename = a.export_subtitles(p, members, '单独字幕')
        self.assertEqual(auto, (path.parent / filename).read_text(encoding='utf-8-sig'))
        with self.assertRaises(ValueError): a.export_subtitles(p, p['segments'], '未完成')
        self.assertEqual(a.subtitle_stamp(3661.005), '01:01:01,005')
        p['groups'] = [{'id':'g', 'srt':filename, 'file':files[0]}]
        a.invalidate_exports(p)
        self.assertNotIn('srt', p['groups'][0])

    def test_portable_engine_discovery_and_generic_defaults(self):
        base = a.DATA / 'portable'
        root = base / 'studio'
        root.mkdir(parents=True)
        engine = base / '任意名称引擎'
        (engine / 'runtime').mkdir(parents=True)
        (engine / 'api_v2.py').touch()
        (engine / 'runtime/python.exe').touch()
        self.assertEqual(a.discover_engine(root), engine)
        (root / 'settings.json').write_text(a.json.dumps({'engine_root':'../任意名称引擎'}), encoding='utf-8-sig')
        self.assertEqual(a.discover_engine(root), engine.resolve())
        for key in ('gpt', 'sovits', 'reference'):
            self.assertEqual(a.defaults()[key], '')

    def test_project_directory_migration_rename_and_trash(self):
        p = self.project()
        named = a.project_dir(p['id'])
        self.assertEqual(named.parent.name, 'projects')
        self.assertTrue(named.name.startswith('测试--'))
        audio = named / 'keep.wav'
        audio.write_bytes(b'keep audio unchanged')
        legacy = a.DATA / p['id']
        named.rename(legacy)
        a.initialize()
        self.assertFalse(legacy.exists())
        self.assertEqual((a.project_dir(p['id'])/'keep.wav').read_bytes(), b'keep audio unchanged')
        p['title'] = '新的/名称'
        a.save(p)
        self.assertTrue(a.project_dir(p['id']).name.startswith('新的_名称--'))
        a.ACTIVE = p['id']
        with self.assertRaises(ValueError): a.manage_project(p['id'], 'delete-project')
        a.ACTIVE = None
        a.manage_project(p['id'], 'delete-project')
        self.assertEqual(a.project_files(), [])
        with self.assertRaises(ValueError): a.save(p)
        with self.assertRaises(ValueError): a.manage_project(p['id'], 'purge-project', '错误名称')
        a.manage_project(p['id'], 'restore-project')
        self.assertEqual((a.project_dir(p['id'])/'keep.wav').read_bytes(), b'keep audio unchanged')
        a.manage_project(p['id'], 'delete-project')
        a.manage_project(p['id'], 'purge-project', p['title'])
        with self.assertRaises(ValueError): a.trash_dir(p['id'])
        with self.assertRaises(ValueError): a.checked_data_path(a.DATA.parent)
        with self.assertRaises(ValueError): a.manage_project('../cache', 'delete-project')

    def test_workstation_port_is_exclusive(self):
        first = a.LocalServer(('127.0.0.1', 0), a.Handler)
        try:
            with self.assertRaises(OSError):
                second = a.LocalServer(first.server_address, a.Handler)
                second.server_close()
        finally:
            first.server_close()


if __name__ == '__main__': unittest.main()
