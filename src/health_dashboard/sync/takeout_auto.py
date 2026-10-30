"""Monthly Google Health Takeout, with as little clicking as possible.

Google Takeout cannot export monthly on its own (its schedule is every two
months) and every download needs your passkey, so this does the rest around
those two clicks. A scheduled task ("Health Takeout" on Windows; a systemd
timer or cron elsewhere) runs it every 30 minutes and at logon:

  1. From the 1st of each month (09:00-22:00, once per month) it opens the
     Takeout page with only Google Health preselected. You click
     "Nächster Schritt" and "Export erstellen".
  2. When Google's "ready" email comes, you click Download on this PC and
     confirm the passkey; the ZIP lands in Downloads.
  3. The next run finds the new takeout-*.zip, extracts only Takeout/Google
     Health into a fresh folder, runs takeout_import.py on it (idempotent),
     swaps it in as data/takeout, keeps the ZIP in data/takeout_zips (only
     the newest one) and shows a toast with what came in.

If no new export has arrived by the 4th, a toast reminds you once; clicking it
opens the same page, which also shows the download button of a ready export.
Toasts are Windows only; elsewhere they go to the log (and notify-send, if
installed). State lives in data/.takeout_state.json, actions are logged to
data/logs/takeout.log.

    pythonw -m health_dashboard takeout-auto
    python -m health_dashboard takeout-auto --downloads DIR --no-remind   # testing
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import locks, procs
from ..config import get_paths

DOWNLOADS = Path.home() / "Downloads"

TAKEOUT_URL = "https://takeout.google.com/settings/takeout/custom/fitbit"
PREFIX = "Takeout/Google Health/"
ZIP_NAME = re.compile(r"takeout-(\d{8}T\d{6}Z)-.*\.zip$", re.I)
REMIND_HOURS = range(9, 22)
NUDGE_DAY = 4
SETTLE = 5 * 60  # a ZIP untouched this long is a finished download
STALE_LOCK = 30 * 60


@dataclass(frozen=True)
class Files:
    """Where this script keeps things, all under the data folder."""
    state: Path
    log: Path
    lock: Path       # .sync.lock, shared with sync-on-access and the backend jobs
    takeout: Path
    incoming: Path
    zips: Path

    @classmethod
    def of(cls, paths):
        d = paths.data
        return cls(state=d / ".takeout_state.json", log=paths.logs / "takeout.log", lock=paths.sync_lock,
                   takeout=d / "takeout", incoming=d / "takeout_incoming", zips=d / "takeout_zips")


def log(f: Files, msg: str) -> None:
    f.log.parent.mkdir(parents=True, exist_ok=True)
    with open(f.log, "a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def load_state(f: Files) -> dict:
    try:
        return json.loads(f.state.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_state(f: Files, state: dict) -> None:
    f.state.write_text(json.dumps(state, indent=2), encoding="utf-8")


def toast(f: Files, title: str, text: str, url: str | None = None) -> None:
    """Windows toast through PowerShell's WinRT bridge; a click opens url.
    Elsewhere: a log line, and notify-send if it's installed."""
    if not procs.WINDOWS:
        log(f, f"notice: {title}: {text}")
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", title, text + (f" {url}" if url else "")], timeout=60, check=False)
        return

    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "''"))
    launch = f' activationType="protocol" launch="{esc(url)}"' if url else ""
    xml = (f'<toast{launch}><visual><binding template="ToastGeneric">'
           f"<text>{esc(title)}</text><text>{esc(text)}</text></binding></visual></toast>")
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
        f"$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml('{xml}');"
        "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id)"
        ".Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   timeout=60, **procs.hidden())


def month_start() -> str:
    """First of this month as a Takeout stamp prefix (UTC, like the file names)."""
    return datetime.now(timezone.utc).strftime("%Y%m01T000000Z")


def remind(f: Files, state: dict) -> None:
    now = datetime.now()
    month = now.strftime("%Y-%m")
    if state.get("last_stamp", "") >= month_start():
        return  # this month's export is already in
    if state.get("opened_month") != month and now.hour in REMIND_HOURS:
        webbrowser.open(TAKEOUT_URL)
        state["opened_month"] = month
        save_state(f, state)
        log(f, "opened the Takeout page for this month's export")
    elif (state.get("opened_month") == month and state.get("nudged_month") != month
          and now.day >= NUDGE_DAY and now.hour in REMIND_HOURS):
        toast(f, "Health export missing",
              "No new Google Health export yet. Click to request it, or download the "
              "ready one on this PC.", TAKEOUT_URL)
        state["nudged_month"] = month
        save_state(f, state)
        log(f, "reminded about the missing export")


def new_exports(downloads: Path, state: dict) -> dict[str, list[Path]]:
    """Finished takeout ZIPs newer than the last import, grouped by export stamp."""
    groups: dict[str, list[Path]] = {}
    if not downloads.is_dir():
        return groups
    names = os.listdir(downloads)
    for name in names:
        m = ZIP_NAME.match(name)
        if not m or m[1] <= state.get("last_stamp", ""):
            continue
        path = downloads / name
        if any(n.startswith(f"takeout-{m[1]}") and n.endswith(".crdownload") for n in names):
            continue  # a part is still downloading
        if time.time() - path.stat().st_mtime < SETTLE:
            continue
        groups.setdefault(m[1], []).append(path)
    return groups


def take_lock(f: Files) -> bool:
    return locks.take(f.lock, STALE_LOCK)


def extract(parts: list[Path], dest: Path) -> int:
    """Copy only the Google Health members into dest; returns the file count.
    Member paths are checked so nothing can land outside dest."""
    root = dest.resolve()
    count = 0
    for part in sorted(parts):
        with zipfile.ZipFile(part) as zf:
            for info in zf.infolist():
                if info.is_dir() or not info.filename.startswith(PREFIX):
                    continue
                target = (root / info.filename).resolve()
                if root not in target.parents:
                    raise ValueError(f"unsafe path in {part.name}: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
                count += 1
    return count


def run_import(f: Files, folder: Path) -> tuple[int, str]:
    """takeout_import.py in a child, keeping .sync.lock fresh while it runs."""
    with open(f.log, "a", encoding="utf-8") as fh:
        proc = subprocess.Popen(procs.module_cmd("health_dashboard", "takeout-import", folder),
                                stdout=subprocess.PIPE, stderr=fh, text=True, **procs.hidden())
        while proc.poll() is None:
            os.utime(f.lock)
            time.sleep(30)
        out = proc.stdout.read()
        fh.write(out)
    return proc.returncode, out


def import_export(f: Files, stamp: str, parts: list[Path], state: dict) -> None:
    log(f, f"found export {stamp}: {', '.join(p.name for p in parts)}")
    shutil.rmtree(f.incoming, ignore_errors=True)
    f.incoming.mkdir()
    try:
        n = extract(parts, f.incoming)
        if not n:
            log(f, "no Google Health folder in it, skipped")
            toast(f, "Health export skipped", f"takeout-{stamp} has no Google Health data.")
            state["last_stamp"] = stamp
            save_state(f, state)
            return
        code, out = run_import(f, f.incoming / PREFIX)
        if code:
            raise RuntimeError(f"takeout_import.py exit {code}")
    except Exception as exc:  # leave the ZIP in Downloads, retry on the next run
        log(f, f"import failed: {exc!r}")
        if state.get("failed_stamp") != stamp:  # one toast per export, not per retry
            toast(f, "Health export import failed", f"{exc}. Details in data/logs/takeout.log.")
            state["failed_stamp"] = stamp
            save_state(f, state)
        shutil.rmtree(f.incoming, ignore_errors=True)
        return

    # The new export holds the full history, so it replaces the previous copy.
    shutil.rmtree(f.takeout, ignore_errors=True)
    f.incoming.rename(f.takeout)
    f.zips.mkdir(exist_ok=True)
    for old in f.zips.glob("takeout-*.zip"):
        old.unlink()
    for p in parts:
        shutil.move(p, f.zips / p.name)
    state["last_stamp"] = stamp
    state["last_import"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_state(f, state)
    readiness = re.search(r"takeout_daily_readiness\s+\d+\s+\S+ \.\. (\S+)", out)
    through = f" through {readiness[1]}" if readiness else ""
    log(f, f"imported export {stamp}{through}")
    toast(f, "Health export imported", f"Google Health Takeout from {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]} "
          f"is in health.db{through}.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--downloads", type=Path, default=DOWNLOADS)
    ap.add_argument("--no-remind", action="store_true")
    args = ap.parse_args(argv)

    f = Files.of(get_paths())
    state = load_state(f)
    exports = new_exports(args.downloads, state)
    if exports:
        if not take_lock(f):
            return 0  # a sync is running; the next run picks the ZIP up
        try:
            for stamp in sorted(exports):
                import_export(f, stamp, exports[stamp], state)
        finally:
            f.lock.unlink(missing_ok=True)
    if not args.no_remind:
        remind(f, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
