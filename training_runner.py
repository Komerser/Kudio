"""Launch the installed upstream training UI on loopback only."""
import os
import sys
import runpy
from pathlib import Path

root = Path(__file__).resolve().parent
cache = root / 'data' / 'cache'
for name in ('temp', 'numba'):
    (cache / name).mkdir(parents=True, exist_ok=True)
os.environ['TEMP'] = os.environ['TMP'] = str(cache / 'temp')
os.environ['NUMBA_CACHE_DIR'] = str(cache / 'numba')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.getcwd())
print('正在载入官方训练面板，准备完成后访问 http://127.0.0.1:9874', flush=True)
import gradio
original = gradio.Blocks.launch
def local_launch(self, *args, **kwargs):
    kwargs.update(server_name='127.0.0.1', share=False, inbrowser=False)
    return original(self, *args, **kwargs)
gradio.Blocks.launch = local_launch
sys.argv = ['webui.py', 'zh_CN']
# The upstream UI cleans its TEMP directory at boot. Give this instance its
# own directory so opening the panel cannot remove another session's files.
training_temp = cache / 'training-temp'
training_temp.mkdir(exist_ok=True)
source = Path('webui.py').read_text(encoding='utf-8')
source = source.replace('tmp = os.path.join(now_dir, "TEMP")', 'tmp = ' + repr(str(training_temp)))
paths = [os.getcwd()] + [str(Path.cwd() / name) for name in ('GPT_SoVITS/BigVGAN', 'tools', 'tools/asr', 'GPT_SoVITS', 'tools/uvr5')]
sys.path.extend(paths)
os.environ['PYTHONPATH'] = os.pathsep.join(paths)
source = source.replace('"%s/users.pth" % (site_packages_root)', repr(str(cache / 'users.pth')))
exec(compile(source, 'webui.py', 'exec'), {'__name__': '__main__', '__file__': str(Path('webui.py').resolve())})
