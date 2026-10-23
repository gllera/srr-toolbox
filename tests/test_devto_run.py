"""End-to-end tests for srr-devto: run_ingest over a fake fetch_json, and the CLI argv paths.

Run: python3 tests/test_devto_run.py
"""
import json
import importlib.machinery
import importlib.util
import os
import urllib.error
import urllib.request
import gzip
import io
import subprocess
import sys
import zlib
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-devto")

loader = importlib.machinery.SourceFileLoader("srr_devto", SCRIPT)
spec = importlib.util.spec_from_loader("srr_devto", loader)
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


NOW = 20000 * 86400 + 5
TODAY = 20000
BASE = "https://dev.to/api/articles"


def reset_opts():
    mod.OPTS.update({"lang": None, "top": 500, "ua": mod.DEFAULT_UA})


def http_error(code):
    return urllib.error.HTTPError("https://dev.to/x", code, "err", {}, None)


class Fake:
    """Serves the listing and the bodies; bodies[id] may be an HTTPError to raise."""

    def __init__(self, listing, bodies=None, on_body=None):
        self.listing = listing
        self.bodies = bodies if bodies is not None else {
            a["id"]: {"id": a["id"], "title": a["title"], "url": a["url"],
                      "body_html": f"<p>body {a['id']}</p>", "language": a.get("language"),
                      "published_at": "2026-10-01T00:00:00Z"} for a in listing}
        self.urls = []
        self.on_body = on_body

    def __call__(self, url, cap):
        self.urls.append(url)
        if url.startswith(BASE + "?"):
            return self.listing
        i = int(url.rsplit("/", 1)[1])
        if self.on_body:
            self.on_body(i)
        b = self.bodies[i]
        if isinstance(b, Exception):
            raise b
        return b


def lst(*ids, lang="en"):
    return [{"id": i, "title": f"T{i}", "url": f"https://dev.to/x/{i}", "language": lang,
             "published_at": "2026-10-01T00:00:00Z"} for i in ids]


def run(fake, etag="", clock=None, **extra):
    mod.fetch_json = fake
    c = [0]

    def tick():
        c[0] += 1
        return c[0]
    req = {"url": "https://dev.to/", "etag": etag}
    req.update(extra)
    return mod.run_ingest(req, now=NOW, clock=clock or tick, sleep=lambda s: None)


def ids_in(etag):
    return sorted(json.loads(etag)["sent"], key=int)


FETCH_JSON = mod.fetch_json
reset_opts()
f = Fake(lst(3, 1, 2))
r = run(f)
check("first run: items in ranking order", [i["title"] for i in r["items"]], ["T3", "T1", "T2"])
check("first run: etag ids", ids_in(r["etag"]), ["1", "2", "3"])
check("first run: requests", f.urls, [BASE + "?top=30&per_page=500", "https://dev.to/api/articles/3",
                                       "https://dev.to/api/articles/1", "https://dev.to/api/articles/2"])
f = Fake(lst(3, 1, 2))
check("second run: not_modified", run(f, r["etag"]), {"not_modified": True})
check("second run: one request", len(f.urls), 1)

# budget stop then resume
state = {"t": 0.0, "bodies": 0}


def on_body(i):
    state["bodies"] += 1
    if state["bodies"] == 2:
        state["t"] = 301.0


f = Fake(lst(1, 2, 3), on_body=on_body)
r = run(f, clock=lambda: state["t"])
check("budget: two items", [i["title"] for i in r["items"]], ["T1", "T2"])
check("budget: state holds those", ids_in(r["etag"]), ["1", "2"])
f2 = Fake(lst(1, 2, 3))
r2 = run(f2, r["etag"])
check("budget resume: the rest only", [i["title"] for i in r2["items"]], ["T3"])
check("budget resume: state", ids_in(r2["etag"]), ["1", "2", "3"])

# 404 / 410 skipped for good
f = Fake(lst(1, 2), bodies={1: http_error(404), 2: {"id": 2, "title": "T2", "url": "u", "body_html": "b"}})
r = run(f)
check("404: skipped", [i["title"] for i in r["items"]], ["T2"])
check("404: recorded", ids_in(r["etag"]), ["1", "2"])
f = Fake(lst(1, 2))
check("404: never requested again", run(f, r["etag"]), {"not_modified": True})
f = Fake(lst(1), bodies={1: http_error(410)})
r = run(f)
check("410: empty items with state", (r["items"], ids_in(r["etag"])), ([], ["1"]))

# other failures
f = Fake(lst(1, 2), bodies={1: {"id": 1, "title": "T1", "url": "u", "body_html": "b"},
                            2: http_error(500)})
r = run(f)
check("500 after success: one item", [i["title"] for i in r["items"]], ["T1"])
check("500 after success: state without the failed id", ids_in(r["etag"]), ["1"])
f = Fake(lst(1, 2), bodies={1: http_error(500), 2: {}})
try:
    run(f)
    check("500 first: raises", "returned", "raised")
except mod.BatchStopped as e:
    check_true("500 first: raises BatchStopped", "500" in str(e), str(e))
f = Fake(lst(1), bodies={1: urllib.error.URLError("boom")})
try:
    run(f)
    check("URLError first: raises", "returned", "raised")
except mod.BatchStopped:
    check("URLError first: raises", True, True)

f = Fake(lst(1, 2), bodies={1: http_error(404), 2: http_error(500)})
try:
    run(f)
    check("404 then 500: raises", "returned", "raised")
except mod.BatchStopped:
    check("404 then 500: raises", True, True)
f = Fake(lst(1), bodies={1: http_error(500)})
try:
    run(f, "2026-10-01T00:00:00Z")
    check("foreign etag + 500 first: raises", "returned", "raised")
except mod.BatchStopped:
    check("foreign etag + 500 first: raises", True, True)

# foreign etags
f = Fake(lst(1))
r = run(f, "2026-10-01T00:00:00Z")
check("foreign etag: normal first run", ids_in(r["etag"]), ["1"])
r = run(Fake(lst(1)), "{bad")
check("garbage etag: normal first run", ids_in(r["etag"]), ["1"])

# --lang drops null-language articles
mod.OPTS["lang"] = {"en"}
listing = lst(1) + [{"id": 2, "title": "T2", "url": "u", "language": None},
                    {"id": 3, "title": "T3", "url": "u"}] + lst(4, lang="es")
f = Fake(listing)
r = run(f)
check("lang: only en", [i["title"] for i in r["items"]], ["T1"])
check("lang: dropped never requested",
      [u for u in f.urls if not u.startswith(BASE + "?")], ["https://dev.to/api/articles/1"])
check("lang: dropped not in state", ids_in(r["etag"]), ["1"])
reset_opts()

# empty listing / short listing
check("empty listing", run(Fake([])), {"not_modified": True})
r = run(Fake(lst(1, 2)))
check("short listing", len(r["items"]), 2)
f = Fake(lst(1))
run(f)
check_true("top is sent", f.urls[0].endswith("per_page=500"), f.urls[0])
mod.OPTS["top"] = 7
f = Fake(lst(1))
run(f)
check("top=7 listing url", f.urls[0], BASE + "?top=30&per_page=7")
reset_opts()

# pruning with nothing new
old = mod.encode_state({9: TODAY - 40, 1: TODAY - 1})
r = run(Fake(lst(1)), old)
check("prune: items empty, pruned state", r, {"items": [], "etag": mod.encode_state({1: TODAY - 1})})

# refused URL
try:
    mod.fetch_json = Fake([])
    mod.run_ingest({"url": "https://example.com/"}, now=NOW)
    check("refused url", "returned", "raised")
except ValueError as e:
    check("refused url", str(e), "the feed URL must be https://dev.to/ (got https://example.com/)")

# listing failure propagates
mod.fetch_json = lambda url, cap: (_ for _ in ()).throw(http_error(503))
try:
    mod.run_ingest({"url": "https://dev.to/"}, now=NOW)
    check("listing failure", "returned", "raised")
except urllib.error.HTTPError:
    check("listing failure", True, True)

# CLI
reset_opts()
rest = mod.parse_options(["--lang", "EN,es,", "--top", "50", "https://dev.to/"])
check("cli: rest", rest, ["https://dev.to/"])
check("cli: lang", mod.OPTS["lang"], {"en", "es"})
check("cli: top", mod.OPTS["top"], 50)
reset_opts()
for bad in (["--top", "0"], ["--top", "1001"], ["--top", "x"], ["--lang", ","], ["--lang"]):
    try:
        mod.parse_options(bad)
        check("cli: refuses " + " ".join(bad), "accepted", "exit")
    except SystemExit:
        check("cli: refuses " + " ".join(bad), True, True)
    reset_opts()
check("cli: request_from_argv", mod.request_from_argv(["--etag", "x", "https://dev.to/"]),
      {"etag": "x", "url": "https://dev.to/"})
for bad in ([], ["a", "b"], ["--nope", "https://dev.to/"]):
    try:
        mod.request_from_argv(bad)
        check("cli: request refuses %r" % bad, "accepted", "exit")
    except SystemExit:
        check("cli: request refuses %r" % bad, True, True)

# ---- item 1: a request without asset_dir is a probe, at most PROBE_BODIES bodies
reset_opts()
f = Fake(lst(1, 2, 3, 4, 5, 6, 7, 8))
r = run(f)
check("probe: 5 bodies fetched", len([u for u in f.urls if not u.startswith(BASE + "?")]), 5)
check("probe: 5 items", len(r["items"]), 5)
f = Fake(lst(1, 2, 3, 4, 5, 6, 7, 8))
r = run(f, asset_dir="")
check("probe: empty asset_dir too", len(r["items"]), 5)
f = Fake(lst(1, 2, 3, 4, 5, 6, 7, 8))
r = run(f, asset_dir="/x/assets")
check("asset_dir set: all 8 bodies", len(r["items"]), 8)

# ---- item 4: listing errors say which request failed
def listing_raises(exc):
    def fn(url, cap):
        raise exc
    return fn


mod.fetch_json = listing_raises(ValueError("Expecting value: line 1 column 1 (char 0)"))
try:
    mod.run_ingest({"url": "https://dev.to/"}, now=NOW)
    check("listing not JSON", "returned", "raised")
except ValueError as e:
    check("listing not JSON: prefixed", str(e), "listing: Expecting value: line 1 column 1 (char 0)")
mod.fetch_json = listing_raises(http_error(503))
cap_err = io.StringIO()
try:
    mod.cmd_ingest({"url": "https://dev.to/"})
    check("listing HTTP error exits", "returned", "exit")
except SystemExit as e:
    check_true("listing HTTP error: prefixed", "listing" in str(e.code) and "503" in str(e.code),
               str(e.code))
mod.fetch_json = lambda url, cap: {"not": "a list"}
try:
    mod.run_ingest({"url": "https://dev.to/"}, now=NOW)
except ValueError as e:
    check("listing not an array", str(e), "listing: the article listing is not a JSON array")

# ---- item 5: a corrupt gzip body is a stopped batch, not a traceback
f = Fake(lst(1), bodies={1: zlib.error("Error -3 while decompressing data")})
try:
    run(f)
    check("zlib.error first: raises", "returned", "raised")
except mod.BatchStopped as e:
    check_true("zlib.error first: BatchStopped", "article 1" in str(e), str(e))
f = Fake(lst(1, 2), bodies={1: {"id": 1, "title": "T1", "url": "u", "body_html": "b"},
                            2: zlib.error("bad")})
r = run(f)
check("zlib.error after success: kept", ids_in(r["etag"]), ["1"])
mod.fetch_json = lambda url, cap: (_ for _ in ()).throw(zlib.error("bad listing"))
try:
    mod.cmd_ingest({"url": "https://dev.to/"})
    check("zlib.error listing: exits", "returned", "exit")
except SystemExit as e:
    check_true("zlib.error listing: message", str(e.code).startswith("srr-devto: "), str(e.code))

# ---- item 6: an id still in the listing is never pruned by age
OLD = TODAY - 40
etag = mod.encode_state({1: OLD, 9: OLD, 2: TODAY - 1})
f = Fake(lst(1, 2))
r = run(f, etag)
check("prune: ranked old id not re-fetched", [u for u in f.urls if not u.startswith(BASE + "?")], [])
check("prune: ranked old id kept, absent old id pruned",
      r, {"items": [], "etag": mod.encode_state({1: OLD, 2: TODAY - 1})})

# ---- item 9: fetch_json's gzip decode and size cap, through a fake urlopen
class FakeResp:
    def __init__(self, data, headers):
        self._d, self.headers = data, headers

    def read(self, n=-1):
        return self._d if n < 0 else self._d[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SEEN = {}
PAYLOAD = json.dumps([{"id": 1, "title": "caf\u00e9"}]).encode()
mode = {"resp": None}


def fake_urlopen(req, timeout=0):
    SEEN.update({k.lower(): v for k, v in req.header_items()})
    SEEN["timeout"] = timeout
    return mode["resp"]


real_urlopen = urllib.request.urlopen
real_fetch_json = FETCH_JSON
urllib.request.urlopen = fake_urlopen
mod.fetch_json = real_fetch_json
mode["resp"] = FakeResp(gzip.compress(PAYLOAD), {"Content-Encoding": "gzip"})
check("fetch_json: gzip by header", mod.fetch_json("https://dev.to/api/articles", 5_000_000),
      [{"id": 1, "title": "caf\u00e9"}])
check_true("fetch_json: asks for gzip", SEEN.get("accept-encoding") == "gzip", str(SEEN))
check("fetch_json: ua", SEEN.get("user-agent"), mod.DEFAULT_UA)
check("fetch_json: timeout", SEEN.get("timeout"), mod.TIMEOUT)
mode["resp"] = FakeResp(gzip.compress(PAYLOAD), {})
check("fetch_json: gzip by magic bytes", mod.fetch_json("https://dev.to/x", 5_000_000)[0]["id"], 1)
mode["resp"] = FakeResp(PAYLOAD, {})
check("fetch_json: plain", mod.fetch_json("https://dev.to/x", 5_000_000)[0]["id"], 1)
mode["resp"] = FakeResp(PAYLOAD, {})
try:
    mod.fetch_json("https://dev.to/x", 10)
    check("fetch_json: cap cuts a plain body", "returned", "raised")
except ValueError:
    check("fetch_json: cap cuts a plain body", True, True)
big = json.dumps(["x" * 5000]).encode()
mode["resp"] = FakeResp(gzip.compress(big), {"Content-Encoding": "gzip"})
try:
    mod.fetch_json("https://dev.to/x", 100)
    check("fetch_json: cap on decoded gzip", "returned", "raised")
except (ValueError, zlib.error):
    check("fetch_json: cap on decoded gzip", True, True)
urllib.request.urlopen = real_urlopen

# ---- item 9: PAUSE_S between body requests, not before the first
slept = []
mod.fetch_json = Fake(lst(1, 2, 3))
mod.run_ingest({"url": "https://dev.to/", "asset_dir": "/a"}, now=NOW, sleep=slept.append)
check("pause: twice for three bodies, PAUSE_S each", slept, [mod.PAUSE_S, mod.PAUSE_S])
slept = []
mod.fetch_json = Fake(lst(1))
mod.run_ingest({"url": "https://dev.to/", "asset_dir": "/a"}, now=NOW, sleep=slept.append)
check("pause: none before the first body", slept, [])
check("pause: PAUSE_S is 0.2", mod.PAUSE_S, 0.2)

# ---- item 9: --help and --version
out = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True)
check("help: exit 0", out.returncode, 0)
check("help: prints the docstring", out.stdout, mod.__doc__ + "\n")
out = subprocess.run([sys.executable, SCRIPT, "--version"], capture_output=True, text=True)
check("version: prints", out.stdout, "srr-devto 1.0\n")

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
