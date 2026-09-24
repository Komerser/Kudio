"""Build a clean distributable with an explicit allowlist; never copy user data."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / 'VERSION.txt').read_text(encoding='utf-8').strip()
FILES = ['app.py', 'engine_runner.py', 'training_runner.py', 'index.html',
         'studio.js', 'segmentation.js', 'library.js', 'kudio.js', 'assets.js', 'studio.css', 'launch.ps1',
         '启动Kudio.bat', '配置引擎.bat', '使用说明.txt', 'VERSION.txt']

def build():
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
