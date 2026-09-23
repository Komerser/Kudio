"""Run the unmodified upstream API, with a diagnostic for slow startup."""
import faulthandler
import os
from pathlib import Path
import runpy
import sys
import threading
import time
from urllib.request import urlopen


def watch_startup():
    while True:
        time.sleep(3)
        try:
            with urlopen('http://127.0.0.1:9880/openapi.json', timeout=2):
                faulthandler.cancel_dump_traceback_later()
                return
        except Exception:
            pass


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    cache = Path(__file__).resolve().parent / 'data' / 'cache'
    cache.mkdir(parents=True, exist_ok=True)
    for name in ('numba', 'temp'):
        (cache / name).mkdir(exist_ok=True)
    os.environ['NUMBA_CACHE_DIR'] = str(cache / 'numba')
    os.environ['TEMP'] = os.environ['TMP'] = str(cache / 'temp')
    sys.dont_write_bytecode = True
    print('Loading GPT-SoVITS. A startup diagnostic will be recorded if loading exceeds 60 seconds.', flush=True)
    faulthandler.enable()
    faulthandler.dump_traceback_later(60, repeat=False)
    threading.Thread(target=watch_startup, daemon=True).start()
    sys.argv = ['api_v2.py'] + sys.argv[1:]
    runpy.run_path('api_v2.py', run_name='__main__')
