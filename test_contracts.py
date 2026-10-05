"""Cross-layer PCS/project/export contracts; synthetic PCM in temporary storage only."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kudio import compiler, projects
from kudio.exporters import build_kson, merge_wav, write_kson, write_srt
from kudio.models import defaults
from kudio.pcs import COMMANDS, PCS_VERSION, parse_source
from kudio.timeline import build_timeline


SCRIPT = ('#[p:1]#[section:intro]#[voice:narrator]#[rate:0.9]#A'
          '#[p:2]#[voice:elder]#B#[pause:50]#')


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def project(self, source=SCRIPT, source_format='pcs'):
        project = {'id': 'c' * 32, 'title': '协议回归', 'segments': [],
                   'voice': defaults(), 'groups': [], 'exports': [],
                   'default_role_id': 'role-narrator',
                   'voice_bindings': {'narrator': 'role-narrator', 'elder': 'role-elder'},
                   'role_snapshots': {
                       'role-narrator': {'id': 'role-narrator', 'name': '旁白',
                                         'voice': dict(defaults(), speed=1.2)},
                       'role-elder': {'id': 'role-elder', 'name': '老人',
                                     'voice': dict(defaults(), speed=.8)}}}
        compiled = compiler.compile_source(source, source_format=source_format, voice=project)
        self.assertTrue(compiled['valid'])
        projects.apply_compilation(project, compiled, source)
        return project

    def complete(self, project):
        for index, segment in enumerate(project['segments']):
            segment.update(status='done', duration=999, audio_version='audio-%d' % index,
                           role='display-role', emotion='display-emotion')
            frames = 2400 if index == 0 else 4801
            with wave.open(str(self.folder / (segment['id'] + '.wav')), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(b'\0\0' * frames)
        return project

    def exact_exports(self, project, prefix='contract'):
        projects.require_compiled(project)
        timeline = build_timeline(project, self.folder, exact=True)
        merge_wav(self.folder / (prefix + '.wav'), timeline, self.folder)
        write_srt(self.folder / (prefix + '.srt'), timeline)
        write_kson(self.folder / (prefix + '.kson'), project, timeline)
        return timeline, json.loads((self.folder / (prefix + '.kson')).read_text(encoding='utf-8'))

    def test_canonical_pcs_uses_one_exact_clock_and_frozen_kson_01(self):
        project = self.complete(self.project())
        self.assertEqual(COMMANDS, ('p', 'pause', 'rate', 'section', 'voice'))
        self.assertEqual(PCS_VERSION, '0.1')
        controls = [node for node in project['ast'] if node['type'] == 'control']
        self.assertEqual(len(controls), 7)
        for node in controls:
            self.assertTrue({'command', 'value', 'raw_value', 'valid',
                             'source_start', 'source_end'} <= set(node))
        timeline, kson = self.exact_exports(project)
        self.assertEqual(set(kson), {'format', 'version', 'timebase', 'generator', 'project',
                                    'source', 'timing_status', 'duration_ms', 'segments', 'events'})
        self.assertEqual((kson['format'], kson['version'], kson['timebase'], kson['timing_status']),
                         ('kson', '0.1', 'ms', 'exact'))
        digest = hashlib.sha256(SCRIPT.encode('utf-8')).hexdigest()
        self.assertIs(compiler.source_hash, projects.source_hash)
        self.assertEqual(kson['source'], {'format': 'pcs', 'pcs_version': '0.1', 'hash': digest})
        self.assertEqual(kson['segments'], timeline['segments'])
        self.assertEqual(kson['events'], timeline['events'])
        self.assertEqual(timeline['total_frames'], 2400 + 7200 + 4801 + 1200)
        self.assertEqual(kson['duration_ms'], 650)
        self.assertEqual([(s['start_ms'], s['end_ms']) for s in kson['segments']], [(0, 100), (400, 600)])
        boundary = [e for e in kson['events'] if e['type'] == 'page' and e['page'] == 2][0]
        elder = [e for e in kson['events'] if e['type'] == 'voice' and e['label'] == 'elder'][0]
        self.assertEqual(boundary['time_ms'], 100)
        self.assertEqual(elder['time_ms'], 100)
        self.assertEqual(kson['events'][-1]['start_ms'], 600)
        self.assertEqual(kson['events'][-1]['end_ms'], 650)
        for segment, page, label, role_id, role_name in zip(
                kson['segments'], (1, 2), ('narrator', 'elder'),
                ('role-narrator', 'role-elder'), ('旁白', '老人')):
            self.assertEqual(segment['page'], page)
            self.assertEqual(segment['section'], 'intro')
            self.assertEqual(segment['speech'], {'rate': .9})
            self.assertEqual((segment['voice_label'], segment['role_id'], segment['role_name']),
                             (label, role_id, role_name))
            self.assertIn('source_start', segment)
            self.assertIn('source_end', segment)
        with wave.open(str(self.folder / 'contract.wav'), 'rb') as output:
            self.assertEqual(output.getnframes(), timeline['total_frames'])
        subtitle = (self.folder / 'contract.srt').read_text(encoding='utf-8-sig')
        self.assertIn('00:00:00,000 --> 00:00:00,100\nA', subtitle)
        self.assertIn('00:00:00,400 --> 00:00:00,600\nB', subtitle)

    def test_recompile_preserves_wav_state_and_kson_event_references(self):
        project = self.complete(self.project())
        first_timeline, first_kson = self.exact_exports(project, 'before')
        previous = copy.deepcopy(project['segments'])
        audio = {s['id']: (self.folder / (s['id'] + '.wav')).read_bytes() for s in previous}
        compiled = compiler.compile_source(SCRIPT, source_format='pcs', voice=project,
                                           previous_segments=project['segments'])
        projects.apply_compilation(project, compiled, SCRIPT)
        for old, new in zip(previous, project['segments']):
            for field in ('id', 'status', 'duration', 'audio_version', 'fingerprint',
                          'overrides', 'page', 'section', 'rate', 'voice_label', 'role', 'emotion'):
                self.assertEqual(new[field], old[field], field)
            self.assertEqual((self.folder / (new['id'] + '.wav')).read_bytes(), audio[old['id']])
        next_timeline, next_kson = self.exact_exports(project, 'after')
        self.assertEqual(next_timeline, first_timeline)
        self.assertEqual(next_kson, first_kson)
        self.assertEqual((self.folder / 'before.wav').read_bytes(), (self.folder / 'after.wav').read_bytes())
        self.assertEqual((self.folder / 'before.srt').read_bytes(), (self.folder / 'after.srt').read_bytes())

    def test_old_schema2_random_event_ids_remain_valid_for_exact_exports(self):
        project = self.complete(self.project())
        project.pop('parse_cache', None)
        project['ast'], project['diagnostics'] = [], [{'level': 'error', 'message': 'old cache'}]
        for index, item in enumerate(project['execution_plan']):
            if item['kind'] == 'event':
                item['id'] = '%032x' % (index + 1)
        plan, segments = copy.deepcopy(project['execution_plan']), copy.deepcopy(project['segments'])
        projects.migrate_project(project)
        self.assertEqual(project['ast'], parse_source(SCRIPT, 'pcs')['ast'])
        self.assertEqual(project['diagnostics'], [])
        self.assertEqual(project['execution_plan'], plan)
        self.assertEqual(project['segments'], segments)
        unused, kson = self.exact_exports(project, 'old-schema2')
        self.assertEqual([e['id'] for e in kson['events']],
                         [e['id'] for e in plan if e['kind'] == 'event'])

    def test_changed_source_refreshes_parse_cache_without_committing_old_media(self):
        project = self.complete(self.project())
        old_hash = project['source_hash']
        old_plan, old_segments = copy.deepcopy(project['execution_plan']), copy.deepcopy(project['segments'])
        audio = {s['id']: (self.folder / (s['id'] + '.wav')).read_bytes() for s in old_segments}
        project['source_text'] = '#[p:9]#[voice:narrator]#新版。'
        projects.migrate_project(project)
        parsed = parse_source(project['source_text'], 'pcs')
        self.assertEqual(project['ast'], parsed['ast'])
        self.assertEqual(project['diagnostics'], parsed['diagnostics'])
        self.assertEqual(project['source_hash'], old_hash)
        self.assertEqual(project['execution_plan'], old_plan)
        self.assertEqual(project['segments'], old_segments)
        with self.assertRaises(ValueError):
            projects.require_compiled(project)
        for sid, content in audio.items():
            self.assertEqual((self.folder / (sid + '.wav')).read_bytes(), content)

    def test_plan_event_semantics_and_order_cannot_diverge_from_source(self):
        original = self.complete(self.project())
        for mutation in ('page', 'voice', 'pause', 'span', 'remove', 'duplicate', 'order', 'boundary'):
            with self.subTest(mutation=mutation):
                project = copy.deepcopy(original)
                plan = project['execution_plan']
                if mutation == 'page':
                    next(e for e in plan if e.get('type') == 'page')['page'] = 99
                elif mutation == 'voice':
                    next(e for e in plan if e.get('type') == 'voice')['label'] = 'wrong'
                elif mutation == 'pause':
                    next(e for e in plan if e.get('type') == 'pause')['duration_ms'] = 0
                elif mutation == 'span':
                    plan[0]['source_start'] += 1
                elif mutation == 'remove':
                    plan.pop(1)
                elif mutation == 'duplicate':
                    plan.insert(1, copy.deepcopy(plan[0]))
                elif mutation == 'order':
                    plan[0], plan[1] = plan[1], plan[0]
                else:
                    # Both the filtered event list and filtered speech list stay
                    # unchanged, but the page would move from 100 ms to 0 ms.
                    page_index = next(i for i, e in enumerate(plan)
                                      if e.get('type') == 'page' and e.get('page') == 2)
                    event = plan.pop(page_index)
                    speech_index = next(i for i, e in enumerate(plan) if e['kind'] == 'speech')
                    plan.insert(speech_index, event)
                with self.assertRaises(ValueError):
                    projects.require_compiled(project)

    def test_explicit_txt_exports_voice_syntax_as_literal_with_no_voice_event(self):
        text = '#[voice:test]#普通正文。'
        project = self.complete(self.project(text, 'txt'))
        timeline, kson = self.exact_exports(project, 'literal-txt')
        self.assertEqual(kson['events'], [])
        self.assertEqual(kson['segments'][0]['text'], text)
        self.assertNotIn('voice_label', kson['segments'][0])
        self.assertNotIn('pcs_version', kson['source'])
        self.assertEqual(kson['source']['hash'], hashlib.sha256(text.encode('utf-8')).hexdigest())
        self.assertEqual(timeline['duration_ms'], 100)

    def test_project_view_marks_stale_source_without_mutating_saved_audio_or_project(self):
        # The HTTP adapter is imported only inside a temporary-DATA fixture.
        # No initialize(), worker, network call or engine process is invoked.
        from kudio import server
        project = self.complete(self.project())
        with patch.object(server, 'DATA', self.folder):
            server.save(project)
            folder = server.project_dir(project['id'])
            for segment in project['segments']:
                (folder / (segment['id'] + '.wav')).write_bytes(
                    (self.folder / (segment['id'] + '.wav')).read_bytes())
            saved = (folder / 'project.json').read_bytes()
            audio = {s['id']: (folder / (s['id'] + '.wav')).read_bytes()
                     for s in project['segments']}
            before = copy.deepcopy(project)
            current = server.project_view(project)
            self.assertFalse(current['compilation_stale'])
            self.assertEqual(current['kson']['timing_status'], 'exact')
            self.assertEqual(project, before)
            for mutation in ('source', 'event'):
                with self.subTest(mutation=mutation):
                    draft = copy.deepcopy(project)
                    if mutation == 'source':
                        draft['source_text'] = '#[p:9]#[voice:narrator]#新版。'
                    else:
                        draft['execution_plan'][0]['page'] = 99
                    original = copy.deepcopy(draft)
                    view = server.project_view(draft)
                    self.assertTrue(view['compilation_stale'])
                    self.assertTrue(view['compilation_error'])
                    self.assertTrue(view['timeline_estimated'])
                    self.assertIsNone(view['timeline'])
                    self.assertIsNone(view['kson'])
                    self.assertTrue(all('timeline' not in s for s in view['segments']))
                    self.assertEqual(view['ast'], parse_source(draft['source_text'], 'pcs')['ast'])
                    self.assertEqual(view['execution_plan'], original['execution_plan'])
                    self.assertEqual(draft, original)
                    self.assertEqual((folder / 'project.json').read_bytes(), saved)
                    for sid, content in audio.items():
                        self.assertEqual((folder / (sid + '.wav')).read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
