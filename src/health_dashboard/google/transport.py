"""How this process talks to Google: gzip, and requests spaced to fit the quota."""

import gzip
import io
import threading
import time
import urllib.request
import urllib.response

# Google allows an unverified app 2.5 requests a second per user, so request
# starts are spaced this far apart however many run at once.
MIN_REQUEST_GAP = 0.4


class GoogleTransport(urllib.request.BaseHandler):
    """Every HTTPS request this process sends: asks for gzip (a page of heart
    rate is about 3 MB of JSON and 50 kB gzipped) and starts at most one
    request per `gap` seconds across all threads. Installed as urllib's opener
    (see `install`), so api.google_get, which calls urlopen, goes through it."""

    handler_order = 400  # before urllib's own: our User-Agent stays, error bodies arrive unzipped

    def __init__(self, gap: float = MIN_REQUEST_GAP):
        self.gap = gap
        self._lock = threading.Lock()
        self._next = 0.0

    def https_request(self, req):
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.gap
        if start > now:
            time.sleep(start - now)
        req.add_header("Accept-Encoding", "gzip")
        req.add_header("User-Agent", "health-dashboard (gzip)")  # Google wants "gzip" in it too
        return req

    def https_response(self, req, resp):
        if resp.headers.get("Content-Encoding") != "gzip":
            return resp
        try:
            body = gzip.decompress(resp.read())
        finally:
            resp.close()
        unzipped = urllib.response.addinfourl(io.BytesIO(body), resp.headers, resp.url, resp.code)
        unzipped.msg = resp.msg
        return unzipped


def install() -> None:
    """Route this process's urlopen calls through one GoogleTransport."""
    urllib.request.install_opener(urllib.request.build_opener(GoogleTransport()))
