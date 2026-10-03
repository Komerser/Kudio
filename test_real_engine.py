"""Opt-in local GPT-SoVITS smoke test; uses isolated project data and a saved voice.

Run after configuring an existing voice preset: python test_real_engine.py
No model, source, audio, configuration or telemetry is uploaded by this test.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import wave
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kudio import server as a

SCRIPT = '#[p:1]#[section:smoke]#这是脚本测试。#[pause:200]#[p:2]#[rate:0.9]#语速稍慢，测试完成。'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preset', help='Saved voice preset ID; default is the first usable preset')
    args = parser.parse_args()
    presets = a.read_presets()
    selected = next((p for p in presets if p['id'] == args.preset), None) if args.preset else next(
        (p for p in presets if all(Path(p['voice'].get(key, '')).is_file()
                                  for key in ('gpt', 'sovits', 'reference'))), None)
    if selected is None:
        raise ValueError('请先在 Kudio 保存一个可用的音色预设')
    a.validate_voice(selected['voice'])
    original_data = a.DATA
    started_engine = False
    with tempfile.TemporaryDirectory(prefix='pcs-smoke-', dir=str(a.ROOT)) as temporary:
        a.DATA = Path(temporary)
        a.initialize()
        server = a.LocalServer(('127.0.0.1', 0), a.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def api(route, data=None):
            request = Request('http://127.0.0.1:%d/api/%s' % (server.server_port, route),
                              data=None if data is None else json.dumps(data).encode('utf-8'),
                              headers={'Content-Type': 'application/json'})
            with urlopen(request, timeout=600) as response:
                return json.load(response)
        try:
            try:
                a.rpc('/openapi.json', timeout=2)
            except Exception:
                print(api('engine', {})['message'], flush=True)
                started_engine = a.PROCESS is not None
            deadline = time.monotonic() + 360
            last_message = 0
            while True:
                try:
                    a.rpc('/openapi.json', timeout=2)
                    break
                except Exception:
                    if a.PROCESS is not None and a.PROCESS.poll() is not None:
                        raise RuntimeError('真实引擎启动失败：' + a.tail_log(a.DATA / 'engine.log', 3000))
                    if time.monotonic() > deadline:
                        raise RuntimeError('引擎加载超时：' + a.tail_log(a.DATA / 'engine.log', 3000))
                    if time.monotonic() - last_message >= 30:
                        print('等待本机 GPT-SoVITS 加载…', flush=True)
                        last_message = time.monotonic()
                    time.sleep(2)
            print('真实引擎已就绪，开始两段 PCS 推理。', flush=True)
            project = api('create', {'title': 'PCS真实引擎测试', 'text': SCRIPT, 'source_format': 'pcs', 'limit': 160})
            assert len(project['segments']) == 2
            api('voice', {'id': project['id'], 'voice': selected['voice']})
            api('start', {'id': project['id']})
            last_status = None
            while True:
                state = api('project?id=' + project['id'])
                project = state['project']
                status = tuple(s['status'] for s in project['segments'])
                if status != last_status:
                    print('推理状态：' + ', '.join(status), flush=True)
                    last_status = status
                if state['active'] is None:
                    break
                time.sleep(1)
            assert all(s['status'] == 'done' for s in project['segments']), project.get('error') or [s['error'] for s in project['segments']]
            assert project['timeline']['timing_status'] == 'exact'
            result = api('export-all', {'id': project['id']})
            folder = a.project_dir(project['id']) / 'exports'
            out = a.ROOT / 'dist' / 'pcs-smoke'
            out.mkdir(parents=True, exist_ok=True)
            for name in result['exports']:
                shutil.copyfile(str(folder / name), str(out / ('pcs-smoke' + Path(name).suffix)))
            kson = json.loads((out / 'pcs-smoke.kson').read_text(encoding='utf-8'))
            with wave.open(str(out / 'pcs-smoke.wav'), 'rb') as audio:
                assert abs(kson['duration_ms'] - audio.getnframes() * 1000 / audio.getframerate()) <= 1
            assert [e['page'] for e in kson['events'] if e['type'] == 'page'] == [1, 2]
            assert next(e for e in kson['events'] if e['type'] == 'pause')['duration_ms'] == 200
            print('PASS: 真实 PCS 推理 → Exact Timeline → WAV / SRT / KSON。', flush=True)
            print(str(out), flush=True)
        finally:
            if started_engine:
                a.STOP.set()
                while a.ACTIVE is not None:
                    time.sleep(.2)
                a.stop_engine()
            server.shutdown()
            server.server_close()
            thread.join()
            a.DATA = original_data


if __name__ == '__main__':
    main()
