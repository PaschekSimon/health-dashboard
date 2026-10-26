"""Starting child processes the same way on Windows and Linux.

On Windows every child gets CREATE_NO_WINDOW, and long-lived background ones
run under pythonw.exe: a detached python.exe from a venv is a launcher that
starts the real interpreter as a child, and a detached console child gets a
fresh console window (Windows Terminal pops up). On Linux there are no
console windows; a background child gets its own session, so it outlives the
API or MCP server that started it.
"""

import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"


def python(background=False):
    """This environment's Python; pythonw.exe for background work on Windows."""
    if background and WINDOWS:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable


def module_cmd(module, *args, background=False):
    """Command line to run `python -m module args` in this environment. -P keeps
    the working directory off sys.path, so nothing there can shadow a module."""
    return [python(background), "-P", "-m", module, *map(str, args)]


def tool(name):
    """A console script installed next to this Python (e.g. google-health-mcp)."""
    exe = Path(sys.executable).with_name(name + (".exe" if WINDOWS else ""))
    return str(exe)


def hidden():
    """Popen/run kwargs for a child that shows no window."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}


def detached():
    """Popen kwargs for a background child that outlives its parent."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def spawn(cmd, log=None, env=None):
    """Start cmd in the background; output goes to `log` (an open file) or nowhere."""
    out = log if log is not None else subprocess.DEVNULL
    return subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                            close_fds=True, **detached())
