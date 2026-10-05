"""Build a clean distributable with an explicit allowlist; never copy user data."""
from pathlib import Path
import hashlib
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import build_i18n

VERSION = (ROOT / 'VERSION.txt').read_text(encoding='utf-8').strip()
FILES = ['app.py', 'engine_runner.py', 'training_runner.py', 'index.html',
         'studio.js', 'pcs-editor.js', 'segmentation.js', 'library.js', 'kudio.js', 'assets.js', 'rebuild.js', 'i18n.js', 'i18n-catalog.js', 'studio.css', 'launch.ps1',
         '启动Kudio.bat', '配置引擎.bat', '使用说明.txt', 'VERSION.txt', 'README.md', 'docs/PCS_KSON.md']
FILES += ['kudio/' + name for name in ('__init__.py', 'server.py', 'source.py', 'projects.py', 'models.py',
          'pcs.py', 'compiler.py', 'text.py', 'tts.py', 'timeline.py', 'exporters.py', 'roles.py')]
FILES += ['docs/UPDATE_%s.md' % VERSION, 'skills/README.md',
          'skills/kudio-pcs-authoring/SKILL.md',
          'skills/kudio-pcs-authoring/references/pcs-v0.1.md',
          'skills/kudio-pcs-authoring/references/kudio-compatibility.md',
          'skills/kudio-pcs-authoring/examples/basic.pcs']

def build():
    build_i18n.build()
    folder = ROOT / 'dist'
    folder.mkdir(exist_ok=True)
    target = folder / ('Kudio-%s-windows.zip' % VERSION)
    prefix = 'Kudio-%s/' % VERSION
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(ROOT / name, prefix + name)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n', encoding='ascii')
    print(target)
    return target

if __name__ == '__main__': build()
