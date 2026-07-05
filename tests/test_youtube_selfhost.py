"""Unit tests for srr-youtube's thumbnail handling: the hotlink quality ladder,
--selfhost downloads into the store (partial-download protection, asset-size
cap, cache reuse), and the fallback-to-hotlink rules.

Run in the repo's uv project venv (plain python3 works too — the script
under test has no third-party deps):
    uv run tests/test_youtube_selfhost.py
"""
import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-youtube")

loader = importlib.machinery.SourceFileLoader("srr_youtube", SCRIPT)
spec = importlib.util.spec_from_loader("srr_youtube", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

failures = []


def check(name, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        print("   want:", repr(want))
        print("   got :", repr(got))
        failures.append(name)


def check_true(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        print("   ", detail)
        failures.append(name)


# --- fake i.ytimg.com --------------------------------------------------------
#
# routes: url -> ("body", bytes)              200, Content-Length = len(bytes)
#                ("clen", bytes, "<n>")       200 with a lying Content-Length
#                ("nolen", bytes)             200 without Content-Length
#                "404" | "neterr"
# Unrouted URLs 404. Every request is recorded as (method, url).

CALLS = []
ROUTES = {}


class FakeResp:
    def __init__(self, body, headers):
        self._body, self._pos, self.headers = body, 0, headers

    def read(self, n=-1):
        if n is None or n < 0:
            n = len(self._body) - self._pos
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req_obj, timeout=None):
    url = req_obj.full_url
    CALLS.append((req_obj.get_method(), url))
    r = ROUTES.get(url, "404")
    if r == "404":
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    if r == "neterr":
        raise urllib.error.URLError("connection reset")
    kind, body = r[0], r[1]
    if kind == "body":
        return FakeResp(body, {"Content-Length": str(len(body))})
    if kind == "clen":
        return FakeResp(body, {"Content-Length": r[2]})
    return FakeResp(body, {})


mod.urllib.request.urlopen = fake_urlopen


def reset(routes, selfhost):
    CALLS.clear()
    ROUTES.clear()
    ROUTES.update(routes)
    mod.probing = True
    mod.SELFHOST = selfhost


VID = "vidA0000001"
MAXRES = f"https://i.ytimg.com/vi/{VID}/maxresdefault.jpg"
SD = f"https://i.ytimg.com/vi/{VID}/sddefault.jpg"
HQ = f"https://i.ytimg.com/vi/{VID}/hqdefault.jpg"   # what the feed declares
MARKER = f"#/yt/{VID}.jpg"

STORE = tempfile.mkdtemp(prefix="srr-youtube-test-")


def store():
    shutil.rmtree(STORE, ignore_errors=True)
    os.makedirs(STORE)
    return STORE


def dest():
    return os.path.join(STORE, "yt", VID + ".jpg")


# --- hotlink mode (the default): HEAD ladder, no bytes downloaded -------------

reset({MAXRES: ("body", b"x")}, selfhost=False)
check("hotlink: maxres exists -> maxres URL",
      mod.thumb_src(VID, HQ, store(), 0), MAXRES)
check("hotlink: only HEAD requests", [m for m, _ in CALLS], ["HEAD"])
check_true("hotlink: nothing written to the store",
           not os.path.exists(os.path.join(STORE, "yt")))

reset({SD: ("body", b"x")}, selfhost=False)
check("hotlink: maxres 404 -> sd", mod.thumb_src(VID, HQ, "", 0), SD)

reset({}, selfhost=False)
check("hotlink: no variants -> feed URL", mod.thumb_src(VID, HQ, "", 0), HQ)

reset({MAXRES: "neterr"}, selfhost=False)
check("hotlink: network error -> feed URL", mod.thumb_src(VID, HQ, "", 0), HQ)
CALLS.clear()
check("hotlink: probing latched off after network error",
      (mod.thumb_src(VID, HQ, "", 0), CALLS), (HQ, []))


# --- --selfhost: download into the store --------------------------------------

reset({MAXRES: ("body", b"MAXRES-BYTES")}, selfhost=True)
check("selfhost: marker returned", mod.thumb_src(VID, HQ, store(), 0), MARKER)
check("selfhost: GET, not HEAD", [m for m, _ in CALLS], ["GET"])
with open(dest(), "rb") as fh:
    check("selfhost: best variant stored", fh.read(), b"MAXRES-BYTES")
check_true("selfhost: no .part left behind", not os.path.exists(dest() + ".part"))

# Cache: a stored file is reused without any request, and its mtime refreshed
# (srr's age-based cache sweep must never delete a file a live feed consumes).
os.utime(dest(), (1, 1))
CALLS.clear()
check("selfhost: cache hit -> marker, no request",
      (mod.thumb_src(VID, HQ, STORE, 0), CALLS), (MARKER, []))
check_true("selfhost: cache hit refreshes mtime", os.path.getmtime(dest()) > 1)

reset({SD: ("body", b"SD")}, selfhost=True)
check("selfhost: maxres 404 -> sd downloaded",
      mod.thumb_src(VID, HQ, store(), 0), MARKER)
with open(dest(), "rb") as fh:
    check("selfhost: sd bytes stored", fh.read(), b"SD")

reset({HQ: ("body", b"HQ")}, selfhost=True)
check("selfhost: all variants 404 -> feed URL downloaded",
      mod.thumb_src(VID, HQ, store(), 0), MARKER)
with open(dest(), "rb") as fh:
    check("selfhost: feed-declared bytes stored", fh.read(), b"HQ")

# Asset cap: an over-cap variant is skipped, a smaller one may still fit; when
# nothing fits, the remote URL is left in place (the external-ingest contract).
reset({MAXRES: ("clen", b"BIG", "9999"), SD: ("body", b"SD-FITS")}, selfhost=True)
check("selfhost: over-cap maxres -> sd fits",
      mod.thumb_src(VID, HQ, store(), 100), MARKER)
with open(dest(), "rb") as fh:
    check("selfhost: under-cap bytes stored", fh.read(), b"SD-FITS")

reset({MAXRES: ("clen", b"BIG", "9999"), SD: ("nolen", b"Z" * 300),
       HQ: ("clen", b"BIG", "9999")}, selfhost=True)
check("selfhost: everything over cap (header or streamed) -> hotlink fallback "
      "(best existing variant)",
      mod.thumb_src(VID, HQ, store(), 100), MAXRES)
check_true("selfhost: over-cap leaves no file, no .part",
           not os.path.exists(dest()) and not os.path.exists(dest() + ".part"))

# Partial-download protection: a truncated body (fewer bytes than advertised)
# must never land in the store — fall back to the hotlink instead.
reset({MAXRES: ("clen", b"HALF", "100")}, selfhost=True)
check("selfhost: truncated download -> hotlink fallback",
      mod.thumb_src(VID, HQ, store(), 0), HQ)
check_true("selfhost: truncation leaves no file, no .part",
           not os.path.exists(dest()) and not os.path.exists(dest() + ".part"))

# Network trouble latches downloads off for the rest of the run: the next item
# hotlinks without a single request.
reset({MAXRES: "neterr"}, selfhost=True)
check("selfhost: network error -> hotlink fallback",
      mod.thumb_src(VID, HQ, store(), 0), HQ)
CALLS.clear()
check("selfhost: latched off after network error",
      (mod.thumb_src("otherVid0002", HQ, STORE, 0), CALLS), (HQ, []))

# No store (srr preview sends no asset_dir) and no video id: hotlink path.
reset({MAXRES: ("body", b"x")}, selfhost=True)
check("selfhost without a store: hotlinks (preview)",
      mod.thumb_src(VID, HQ, "", 0), MAXRES)
check("selfhost without a store: probes, never GETs", [m for m, _ in CALLS], ["HEAD"])
reset({}, selfhost=True)
check("selfhost without a video id: feed URL, no requests",
      (mod.thumb_src("", HQ, STORE, 0), CALLS), (HQ, []))


# --- build_items end-to-end ----------------------------------------------------

ATOM = f"""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>{VID}</yt:videoId>
    <title>Video &amp; One</title>
    <published>2026-07-01T10:00:00+00:00</published>
    <media:group>
      <media:thumbnail url="{HQ}"/>
      <media:description>line one
line two</media:description>
    </media:group>
  </entry>
</feed>"""

reset({MAXRES: ("body", b"IMG")}, selfhost=True)
items = mod.build_items(ATOM.encode(), store(), 0)
check("items: one entry", len(items), 1)
it = items[0]
check("items: guid hashes the video id", it["guid"], mod.fnv1a32(VID))
check("items: link", it["link"], f"https://www.youtube.com/watch?v={VID}")
check("items: published", it["published"], "2026-07-01T10:00:00+00:00")
check("items: title", it["title"], "Video & One")
check("items: selfhost content uses the store marker", it["content"],
      f'<p><a href="https://www.youtube.com/watch?v={VID}">'
      f'<img src="{MARKER}" alt="Video &amp; One"></a></p>'
      f'<p>line one<br>line two</p>')

reset({MAXRES: ("body", b"IMG")}, selfhost=False)
it = mod.build_items(ATOM.encode(), "", 0)[0]
check_true("items: default mode hotlinks", f'<img src="{MAXRES}"' in it["content"],
           it["content"])


# --- fetch_feed cursor ----------------------------------------------------------

def raise_304(req_obj, timeout=None):
    raise urllib.error.HTTPError(req_obj.full_url, 304, "Not Modified", {}, None)


mod.urllib.request.urlopen = raise_304
check("fetch_feed: 304 -> None (not_modified)",
      mod.fetch_feed({"url": "https://x", "etag": "e"}), None)
check("run_ingest: 304 -> not_modified response",
      mod.run_ingest({"url": "https://x", "etag": "e"}), {"not_modified": True})

shutil.rmtree(STORE, ignore_errors=True)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
