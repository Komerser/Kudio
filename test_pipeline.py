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
from kudio.projects import apply_compilation, migrate_project, refresh_source_cache, require_compiled
from kudio.pcs import parse_source, PCS_VERSION
from kudio.source import source_hash

SCRIPT = '#[p:1]#开场。#[p:2]#第二页。#[pause:800]#[section:intro]#[rate:0.9]#继续。'


def audio_bytes(frames=2400):
    output = io.BytesIO()
    with wave.open(output, 'wb') as source:
        source.setnchannels(1)
        source.setsampwidth(2)
        source.setframerate(24000)
        source.writeframes(b'\0\0' * frames)
    return output.getvalue()


class ProjectSourceCacheTests(unittest.TestCase):
    def project(self, text=SCRIPT, source_format='pcs'):
        project = {'groups': []}
        apply_compilation(project, compile_source(text, source_format=source_format), text)
        return project

    def test_schema2_without_cache_identity_reparses_even_matching_source_hash(self):
        project = self.project()
        project.pop('parse_cache')
        project.update(ast=[{'type': 'text', 'text': '错误缓存'}], diagnostics=[{'level': 'error', 'message': '错误缓存'}])
        source_digest = project['source_hash']
        old_plan, old_segments = copy.deepcopy(project['execution_plan']), copy.deepcopy(project['segments'])
        migrate_project(project)
        parsed = parse_source(SCRIPT, 'pcs')
        self.assertEqual(project['ast'], parsed['ast'])
        self.assertEqual(project['diagnostics'], parsed['diagnostics'])
        self.assertEqual(project['source_hash'], source_digest)
        self.assertEqual(project['execution_plan'], old_plan)
        self.assertEqual(project['segments'], old_segments)
        self.assertEqual(project['schema_version'], 2)
        require_compiled(project)

    def test_source_mismatch_refreshes_cache_but_cannot_certify_the_old_compilation(self):
        project = self.project()
        old_hash, old_plan, old_segments = project['source_hash'], copy.deepcopy(project['execution_plan']), copy.deepcopy(project['segments'])
        project['source_text'] = '#[voice:]#新正文'
        migrate_project(project)
        self.assertEqual(project['ast'], parse_source(project['source_text'], 'pcs')['ast'])
        self.assertTrue(project['diagnostics'])
        self.assertEqual(project['parse_cache']['source_hash'], source_hash(project['source_text']))
        self.assertEqual(project['source_hash'], old_hash)
        self.assertEqual(project['execution_plan'], old_plan)
        self.assertEqual(project['segments'], old_segments)
        with self.assertRaisesRegex(ValueError, '源稿已更改'):
            require_compiled(project)

    def test_cache_identity_includes_explicit_format_and_pcs_version(self):
        text = '#[voice:narrator]#正文'
        project = self.project(text)
        project['source_format'] = 'txt'
        refresh_source_cache(project)
        self.assertEqual(project['ast'], parse_source(text, 'txt')['ast'])
        self.assertEqual(project['parse_cache']['source_format'], 'txt')
        with self.assertRaisesRegex(ValueError, '控制事件'):
            require_compiled(project)
        project['source_format'] = 'pcs'
        project['parse_cache']['pcs_version'] = 'old'
        project['ast'] = []
        refresh_source_cache(project)
        self.assertEqual(project['ast'], parse_source(text, 'pcs')['ast'])
        self.assertEqual(project['parse_cache']['pcs_version'], PCS_VERSION)

    def test_require_compiled_uses_source_diagnostics_even_for_poisoned_matching_cache(self):
        project = self.project('正文')
        project.update(ast=[], diagnostics=[{'level': 'error', 'message': '伪错误'}])
        require_compiled(project)
        self.assertEqual(project['diagnostics'], [])
        project.update(source_text='#[voice:]#正文', source_hash=source_hash('#[voice:]#正文'), diagnostics=[])
        project['parse_cache']['source_hash'] = project['source_hash']
        with self.assertRaisesRegex(ValueError, '解析错误'):
            require_compiled(project)
        self.assertTrue(project['diagnostics'])

    def test_apply_rejects_a_compilation_for_different_source_atomically(self):
        project = self.project('旧正文')
        before = copy.deepcopy(project)
        with self.assertRaisesRegex(ValueError, '编译结果与源稿不一致'):
            apply_compilation(project, compile_source('新正文', source_format='pcs'), '另一个源稿')
        self.assertEqual(project, before)
        compiled = compile_source('新正文', source_format='pcs')
        compiled.update(ast=[{'type': 'text', 'text': '伪缓存'}], diagnostics=[{'level': 'error'}])
        apply_compilation(project, compiled, '新正文')
        self.assertEqual(project['ast'], parse_source('新正文', 'pcs')['ast'])
        self.assertEqual(project['diagnostics'], [])

    def test_event_guard_rejects_type_spans_values_and_order_but_keeps_old_ids(self):
        project = self.project()
        for event in project['execution_plan']:
            if event['kind'] == 'event':
                event['id'] = 'a' * 32  # Legacy UUIDs are not migrated or compared.
        require_compiled(project)
        for field, value in (('page', 9), ('page', True), ('source_start', -1), ('source_end', 0), ('type', 'voice')):
            broken = copy.deepcopy(project)
            broken['execution_plan'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, '控制事件'):
                require_compiled(broken)
        broken = copy.deepcopy(project)
        indexes = [index for index, item in enumerate(broken['execution_plan']) if item['kind'] == 'event']
        first, last = indexes[0], indexes[-1]
        broken['execution_plan'][first], broken['execution_plan'][last] = broken['execution_plan'][last], broken['execution_plan'][first]
        with self.assertRaisesRegex(ValueError, '控制事件'):
            require_compiled(broken)

    def test_event_guard_preserves_speech_boundary_and_rejects_unknown_source_spans(self):
        project = self.project('A#[p:2]#B')
        require_compiled(project)
        before = copy.deepcopy(project)
        project['execution_plan'][0], project['execution_plan'][1] = project['execution_plan'][1], project['execution_plan'][0]
        with self.assertRaisesRegex(ValueError, '源码边界'):
            require_compiled(project)
        project = copy.deepcopy(before)
        project['segments'][0]['source_end'] = None
        with self.assertRaisesRegex(ValueError, '源码位置'):
            require_compiled(project)
        project = copy.deepcopy(before)
        project['segments'][0]['source_end'] = project['execution_plan'][1]['source_end']
        with self.assertRaisesRegex(ValueError, '源码位置'):
            require_compiled(project)


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

    def role(self, name, speed=1.0, profile=None):
        folder = a.DATA / name
        folder.mkdir(exist_ok=True)
        for filename in ('role.ckpt', 'role.pth'):
            (folder / filename).touch()
        (folder / 'reference.wav').write_bytes(audio_bytes())
        voice = dict(a.defaults(), name=name, gpt=str(folder / 'role.ckpt'),
                     sovits=str(folder / 'role.pth'), reference=str(folder / 'reference.wav'),
                     prompt='参考音频', speed=speed)
        return self.request('api/preset', {'voice': voice, 'profile': profile or {}})

    def test_role_binding_switches_models_and_reference_and_survives_preset_deletion(self):
        narrator, elder, child = self.role('旁白'), self.role('老人', .8), self.role('儿童', 1.2)
        project = self.create('开场。#[voice:male_elder]#老人。\n再说。#[voice:female_child]#孩子。')
        self.assertEqual(project['voice_labels'], ['male_elder', 'female_child'])
        self.assertEqual(len(project['voice_binding_errors']), 2)
        with patch.object(a, 'rpc') as rpc, self.assertRaises(HTTPError):
            self.request('api/start', {'id': project['id']})
        rpc.assert_not_called()
        project = self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                    'voice_bindings': {'male_elder': elder['id'], 'female_child': child['id']}})
        self.assertEqual(project['voice_binding_errors'], [])
        self.request('api/delete-preset', {'preset_id': elder['id']})
        self.assertEqual(len(a.read_presets()), 2)
        calls = []
        def rpc(path, payload=None, timeout=600):
            calls.append((path, payload))
            return audio_bytes() if payload else b'{}'
        with patch.object(a, 'rpc', side_effect=rpc):
            a.worker(project['id'])
        payloads = [payload for path, payload in calls if payload]
        self.assertEqual([payload['ref_audio_path'] for payload in payloads],
                         [narrator['voice']['reference'], elder['voice']['reference'],
                          elder['voice']['reference'], child['voice']['reference']])
        self.assertEqual([payload['speed_factor'] for payload in payloads], [1, .8, .8, 1.2])
        self.assertEqual(len([path for path, payload in calls if path.startswith('/set_gpt_weights?')]), 3)
        self.assertTrue(all(segment['status'] == 'done' for segment in a.read(project['id'])['segments']))
        exported = self.request('api/export-all', {'id': project['id']})
        filename = next(filename for filename in exported['exports'] if filename.endswith('.kson'))
        kson = self.request('download?id=%s&file=%s' % (project['id'], filename))
        self.assertEqual([event['label'] for event in kson['events'] if event['type'] == 'voice'],
                         ['male_elder', 'female_child'])
        self.assertEqual(kson['segments'][1]['role_id'], elder['id'])
        self.assertEqual(kson['segments'][1]['role_name'], elder['name'])
        self.assertEqual(kson['segments'][1]['voice_label'], 'male_elder')

    def test_binding_change_invalidates_only_affected_role_and_recompile_keeps_others(self):
        narrator, first, replacement = self.role('旁白'), self.role('声音一'), self.role('声音二')
        text = '开场。#[voice:male_elder]#老人。#[voice:female_child]#孩子。'
        project = self.create(text)
        bindings = {'male_elder': first['id'], 'female_child': first['id']}
        self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                                          'voice_bindings': bindings})
        project, unused = self.generate(project)
        old_ids = [segment['id'] for segment in project['segments']]
        bindings['male_elder'] = replacement['id']
        project = self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                                                     'voice_bindings': bindings})
        self.assertEqual([segment['status'] for segment in project['segments']], ['done', 'pending', 'done'])
        unchanged = self.request('api/project/source', {'id': project['id'], 'text': text})
        self.assertEqual([segment['id'] for segment in unchanged['segments']], old_ids)
        self.assertEqual([segment['status'] for segment in unchanged['segments']], ['done', 'pending', 'done'])
        final = self.request('api/project/voices', {'id': project['id'], 'default_role_id': replacement['id'],
                                                   'voice_bindings': bindings})
        self.assertEqual([segment['status'] for segment in final['segments']], ['pending', 'pending', 'done'])

    def test_role_profile_is_display_only_and_images_are_registered_and_validated(self):
        image = a.DATA / 'avatar.png'
        image.write_bytes(__import__('base64').b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5WQAAAAASUVORK5CYII='))
        profile = {'description': '温柔的旁白', 'tags': ['温柔', '中性'], 'color': '#abcdef', 'avatar': str(image)}
        role = self.role('完整角色', profile=profile)
        self.assertEqual(role['profile']['description'], profile['description'])
        url = 'http://127.0.0.1:%d/api/role-image?preset_id=%s&kind=avatar' % (self.server.server_port, role['id'])
        with urlopen(url) as response:
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.read(), image.read_bytes())
        with self.assertRaises(HTTPError):
            urlopen(url.replace(role['id'], 'unknown'))
        before = (a.DATA / 'voice_presets.json').read_bytes()
        with self.assertRaises(HTTPError):
            self.request('api/preset', {'voice': role['voice'], 'preset_id': role['id'],
                                       'profile': dict(profile, avatar=str(a.DATA / 'secret.txt'))})
        self.assertEqual((a.DATA / 'voice_presets.json').read_bytes(), before)
        project = self.create('你好。', 'txt')
        self.request('api/project/voices', {'id': project['id'], 'default_role_id': role['id'], 'voice_bindings': {}})
        project, unused = self.generate(project)
        self.request('api/preset', {'voice': role['voice'], 'preset_id': role['id'],
                                   'profile': dict(profile, description='新描述')})
        saved = self.request('api/project/voices', {'id': project['id'], 'default_role_id': role['id'], 'voice_bindings': {}})
        self.assertEqual(saved['segments'][0]['status'], 'done')

    def test_pcs_rate_overrides_bound_role_and_single_regeneration_resolves_it(self):
        role = self.role('老人', 1.2)
        project = self.create('#[voice:male_elder]#[rate:0.8]#正文。')
        self.request('api/project/voices', {'id': project['id'], 'voice_bindings': {'male_elder': role['id']}})
        project, payloads = self.generate(project)
        self.assertEqual(payloads[0]['speed_factor'], .8)
        segment = project['segments'][0]
        a.mutate_project(project, 'edit-segment', {'segment': segment['id'], 'text': segment['text'],
                                                  'overrides': {'seed': 123}})
        a.save(project)
        project, payloads = self.generate(project)
        self.assertEqual(payloads[0]['seed'], 123)
        self.assertEqual(payloads[0]['ref_audio_path'], role['voice']['reference'])

    def test_partial_model_switch_failure_reloads_prior_role_before_next_speech(self):
        first, broken = self.role('模型A'), self.role('模型B')
        project = self.create('第一段A。#[voice:broken]#失败的B。#[voice:normal]#最后A。')
        self.request('api/project/voices', {'id': project['id'], 'default_role_id': first['id'],
                    'voice_bindings': {'broken': broken['id'], 'normal': first['id']}})
        engine, spoken, attempted_sovits = {}, [], []
        def rpc(path, payload=None, timeout=600):
            query = a.parse_qs(a.urlparse(path).query)
            if path.startswith('/set_gpt_weights?'):
                engine['gpt'] = query['weights_path'][0]
            elif path.startswith('/set_sovits_weights?'):
                value = query['weights_path'][0]
                attempted_sovits.append(value)
                if value == broken['voice']['sovits']:
                    raise ValueError('模拟 SoVITS 切换失败，GPT 已经切换')
                engine['sovits'] = value
            if payload:
                spoken.append((payload['text'], copy.deepcopy(engine)))
                return audio_bytes()
            return b'{}'
        with patch.object(a, 'rpc', side_effect=rpc), patch.object(a.STOP, 'wait', return_value=False):
            a.worker(project['id'])
        self.assertEqual([text for text, unused in spoken], ['第一段A。', '最后A。'])
        for unused, models in spoken:
            self.assertEqual(models, {'gpt': first['voice']['gpt'], 'sovits': first['voice']['sovits']})
        self.assertEqual(attempted_sovits.count(broken['voice']['sovits']), 3)
        self.assertEqual(attempted_sovits.count(first['voice']['sovits']), 2)
        self.assertEqual([segment['status'] for segment in a.read(project['id'])['segments']],
                         ['done', 'failed', 'done'])

    def test_binding_preserves_other_snapshots_and_refresh_only_updates_requested_roles(self):
        narrator, elder, child, replacement = [self.role(name) for name in ('旁白', '老人', '孩子', '替换老人')]
        project = self.create('旁白。#[voice:elder]#老人。#[voice:child]#孩子。')
        bindings = {'elder': elder['id'], 'child': child['id']}
        self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                                          'voice_bindings': bindings})
        project, unused = self.generate(project)
        changed = dict(child['voice'], speed=.7)
        self.request('api/preset', {'preset_id': child['id'], 'voice': changed})
        bindings['elder'] = replacement['id']
        unchanged = self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                                                     'voice_bindings': bindings})
        self.assertEqual(unchanged['role_snapshots'][child['id']]['voice']['speed'], 1)
        self.assertEqual([segment['status'] for segment in unchanged['segments']], ['done', 'pending', 'done'])
        refreshed = self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                    'voice_bindings': bindings, 'refresh_role_ids': [child['id']]})
        self.assertEqual(refreshed['role_snapshots'][child['id']]['voice']['speed'], .7)
        self.assertEqual([segment['status'] for segment in refreshed['segments']], ['done', 'pending', 'pending'])
        self.request('api/delete-preset', {'preset_id': child['id']})
        before = (a.project_dir(project['id']) / 'project.json').read_bytes()
        with self.assertRaises(HTTPError):
            self.request('api/project/voices', {'id': project['id'], 'default_role_id': narrator['id'],
                        'voice_bindings': bindings, 'refresh_role_ids': [child['id']]})
        self.assertEqual((a.project_dir(project['id']) / 'project.json').read_bytes(), before)

    def test_unused_pcs_voice_control_and_txt_voice_literals_need_no_binding(self):
        unused = self.create('正文。#[voice:unused]#')
        self.assertEqual(unused['voice_labels'], [])
        self.assertEqual(unused['voice_binding_errors'], [])
        literal = self.create('#[voice:literal]#正文。', 'txt')
        self.assertEqual(literal['voice_labels'], [])
        self.assertEqual(literal['voice_binding_errors'], [])
        self.assertIsNone(literal['segments'][0]['voice_label'])

    def test_role_identity_changes_clear_exports_and_keep_identical_audio(self):
        first = self.role('同声角色A')
        second = self.request('api/preset', {'voice': dict(first['voice'], name='同声角色B')})
        project = self.create('正文。', 'txt')
        self.request('api/project/voices', {'id': project['id'], 'default_role_id': first['id'], 'voice_bindings': {}})
        project, unused = self.generate(project)
        version = project['segments'][0]['audio_version']
        self.request('api/export-all', {'id': project['id']})
        switched = self.request('api/project/voices', {'id': project['id'], 'default_role_id': second['id'],
                                                     'voice_bindings': {}})
        self.assertEqual(switched['exports'], [])
        self.assertEqual(switched['segments'][0]['status'], 'done')
        self.assertEqual(switched['segments'][0]['audio_version'], version)
        exported = self.request('api/export-kson', {'id': project['id']})
        kson = self.request('download?id=%s&file=%s' % (project['id'], exported['exports'][0]))
        self.assertEqual(kson['segments'][0]['role_id'], second['id'])
        self.assertEqual(kson['segments'][0]['role_name'], second['name'])
        self.request('api/preset', {'preset_id': second['id'], 'voice': dict(second['voice'], name='角色B新名称')})
        renamed = self.request('api/project/voices', {'id': project['id'], 'default_role_id': second['id'],
                               'voice_bindings': {}, 'refresh_role_ids': [second['id']]})
        self.assertEqual(renamed['exports'], [])
        self.assertEqual(renamed['segments'][0]['status'], 'done')
        self.assertEqual(renamed['segments'][0]['audio_version'], version)

    def test_http_parse_compile_and_no_tts_side_effect(self):
        with patch.object(a, 'rpc') as rpc:
            parsed = self.request('api/pcs/parse', {'text': SCRIPT, 'source_format': 'pcs'})
            compiled = self.request('api/pcs/compile', {'text': SCRIPT, 'limit': 160, 'source_format': 'pcs'})
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

    def test_txt_is_literal_source_spans_and_exact_download(self):
        project = self.create('  #[p:1]#你好。\r\n', 'txt')
        self.assertEqual(project['source_format'], 'txt')
        self.assertEqual(project['source_text'], '  #[p:1]#你好。\r\n')
        self.assertEqual(project['segments'][0]['text'], '#[p:1]#你好。')
        self.assertEqual(project['timeline']['events'], [])
        self.assertIsNone(project['segments'][0]['page'])
        plain = self.create('hello', 'txt')
        self.assertEqual(plain['source_format'], 'txt')
        self.assertEqual(plain['timeline']['timing_status'], 'estimated')
        with self.assertRaises(HTTPError):
            self.request('api/export-kson', {'id': plain['id']})
        project, payloads = self.generate(project)
        self.assertEqual(payloads[0]['text'], '#[p:1]#你好。')
        result = self.request('api/export-kson', {'id': project['id']})
        exported = self.request('download?id=%s&file=%s' % (project['id'], result['exports'][0]))
        self.assertEqual(exported['format'], 'kson')

    def test_http_defaults_to_txt_and_switches_only_on_explicit_format(self):
        text = 'A#[pause:800]#B'
        parsed = self.request('api/pcs/parse', {'text': text})
        self.assertEqual(parsed['source_format'], 'txt')
        self.assertEqual(parsed['ast'][0]['text'], text)
        for mode in ('txt', 'pcs'):
            compiled = self.request('api/pcs/compile', {'text': text, 'source_format': mode})
            self.assertEqual(compiled['source_format'], mode)
            self.assertEqual(len(compiled['segments']), 1 if mode == 'txt' else 2)
        project = self.request('api/create', {'text': '#[p:abc]#正文'})
        self.assertEqual(project['source_format'], 'txt')
        self.assertEqual(project['diagnostics'], [])
        updated = self.request('api/project/source', {'id': project['id'], 'text': text})
        self.assertEqual(updated['source_format'], 'txt')
        switched = self.request('api/project/source', {'id': project['id'], 'text': text, 'source_format': 'pcs'})
        self.assertEqual(switched['source_format'], 'pcs')
        kept = self.request('api/project/source', {'id': project['id'], 'text': text})
        self.assertEqual(kept['source_format'], 'pcs')


if __name__ == '__main__':
    unittest.main()
