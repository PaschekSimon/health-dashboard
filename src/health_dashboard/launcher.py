"""Open the health web app without any console window.

    pythonw -m health_dashboard open [--no-browser]

Starts the local API hidden in the background if it isn't running yet, then
opens http://127.0.0.1:8765/ in the default browser (the "Health App" and
"Health Backend" shortcuts on Windows run this with pythonw.exe). The API
keeps running after this ends; its output goes to data/logs/backend.log.
"""

import argparse
import time
import urllib.request
import webbrowser

from . import procs
from .config import child_env, get_paths

PORT = 8765


def api_up(url):
    try:
        with urllib.request.urlopen(url + "status", timeout=3):
            return True
    except OSError:
        return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-browser", action="store_true", help="only make sure the API runs")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args(argv)
    url = f"http://127.0.0.1:{args.port}/"
    if not api_up(url):
        paths = get_paths()
        paths.logs.mkdir(parents=True, exist_ok=True)
        with open(paths.logs / "backend.log", "ab") as log:
            procs.spawn(procs.module_cmd("health_dashboard", "serve", "--port", args.port, background=True),
                        log=log, env=child_env(paths))
        for _ in range(40):
            if api_up(url):
                break
            time.sleep(0.5)
    if not args.no_browser:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
