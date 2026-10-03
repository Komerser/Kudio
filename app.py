"""Kudio entry point. Also exposes the server module for existing import users."""
import sys
from pathlib import Path

# The Windows launcher uses Python -I, which omits the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kudio import server

if __name__ == '__main__':
    server.main()
else:
    sys.modules[__name__] = server
