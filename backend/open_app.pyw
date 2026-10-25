"""Open the health web app without any console window.

Run with the backend venv's pythonw.exe (the "Health App" shortcut does this):
starts the local API hidden in the background if it isn't running yet, then
opens http://127.0.0.1:8765/ in the default browser. The API keeps running
after this script ends; its output goes to Health\\data\\logs\\backend.log.
"""

import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE.parent / "data" / "logs" / "backend.log"
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"


def api_up():
    try:
        with urllib.request.urlopen(URL + "status", timeout=3):
            return True
    except OSError:
        return False


def main():
    if not api_up():
        # pythonw, not a detached python.exe: the venv launcher would hand a
        # detached console child a new (visible) console window
        python = HERE / ".venv" / "Scripts" / "pythonw.exe"
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        with open(LOG, "ab") as log:
            subprocess.Popen([str(python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT)],
                             cwd=HERE, creationflags=flags, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             close_fds=True)
        for _ in range(40):
            if api_up():
                break
            time.sleep(0.5)
    if "--no-browser" not in sys.argv:
        webbrowser.open(URL)


if __name__ == "__main__":
    main()
