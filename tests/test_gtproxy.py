"""Tests for srr-gtproxy: the proxy URL and its inverse, the page it hands on
(the proxy's additions removed, its links unwrapped, the site's own markup
kept), run_item with a fake fetch, and the protocol path, which never exits
non-zero and emits nothing on failure.

Run in the repo's uv project venv (the script imports bs4 and html5lib):
    uv run tests/test_gtproxy.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-gtproxy")

loader = importlib.machinery.SourceFileLoader("srr_gtproxy", SCRIPT)
spec = importlib.util.spec_from_loader("srr_gtproxy", loader)
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


Q = "_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en"
QA = Q.replace("&", "&amp;")

# --- proxy_url / unwrap_url -------------------------------------------------

check("proxy url: dots become hyphens",
      mod.proxy_url("https://www.example.com/reviews/a-b-1.shtml"),
      "https://www-example-com.translate.goog/reviews/a-b-1.shtml?" + Q)
check("proxy url: a hyphen in the host is doubled, the query kept",
      mod.proxy_url("https://news-cdn.example.com/a?x=1&y="),
      "https://news--cdn-example-com.translate.goog/a?x=1&y=&" + Q)
check("proxy url: no path is /",
      mod.proxy_url("https://example.com"),
      "https://example-com.translate.goog/?" + Q)
check("proxy url: http is refused", mod.proxy_url("http://example.com/a"), None)
check("proxy url: a port is refused", mod.proxy_url("https://example.com:8443/a"), None)
check("proxy url: no link", mod.proxy_url(""), None)

for host in ("www.example.com", "news-cdn.example.com", "xn--bcher-kva.example",
             "a-b-c.d-e.example.com"):
    check("host round trip " + host, mod.decode_host(mod.encode_host(host)), host)

check("unwrap: translate.google.com/website?u=",
      mod.unwrap_url("https://translate.google.com/website?sl=auto&tl=en&hl=en"
                     "&u=https://youtu.be/abc%3Ft%3D5"),
      "https://youtu.be/abc?t=5")
check("unwrap: a .translate.goog host loses the _x_tr_ parameters",
      mod.unwrap_url("https://news--cdn-example-com.translate.goog/p/q.jpg?w=2&" + Q + "#f"),
      "https://news-cdn.example.com/p/q.jpg?w=2#f")
check("unwrap: another URL is left alone",
      mod.unwrap_url("https://cdn.example.com/a.jpg"), "https://cdn.example.com/a.jpg")
check("unwrap: a relative URL is left alone", mod.unwrap_url("/a/b"), "/a/b")
check("unwrap: website without u is left alone",
      mod.unwrap_url("https://translate.google.com/website?sl=auto"),
      "https://translate.google.com/website?sl=auto")
check("unwrap: srcset",
      mod.unwrap_srcset("https://x-example-com.translate.goog/a.jpg?" + Q + " 1x, "
                        "https://cdn.example.com/b.jpg 2x"),
      "https://x.example.com/a.jpg 1x, https://cdn.example.com/b.jpg 2x")

# --- clean ------------------------------------------------------------------

# A page as the proxy answers it: the site's own head and body, with the
# proxy's metas, scripts, font and links mixed in, as measured on a live one.
GT = "https://www.gstatic.com/_/translate_http/_/js/k=translate_http.tr.de.x/am=A/d=1"
PAGE = """<!DOCTYPE html><html><head>
<base href="https://www.example.com/r/1.shtml">
<script async src="https://www.googletagmanager.com/gtag/js?id=G-X"></script>
<meta name="robots" content="max-image-preview:large">
<title>T</title>
<meta http-equiv="X-Translated-By" content="Google">
<meta http-equiv="X-Translated-To" content="en">
<script type="text/javascript" src=\"""" + GT + """/rs=A" data-sourceurl="https://www.example.com/r/1.shtml"></script>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">
<script type="text/javascript" src=\"""" + GT + """/exm=corsproxy" data-proxy-url="https://www-example-com.translate.goog"></script>
<meta name="robots" content="none">
</head><body>
<div class="teaser">the site's own chrome</div>
<article itemprop="reviewBody"><p class="intro">First <b>para</b>.</p>
<p>See <a href="https://translate.google.com/website?sl=auto&amp;tl=en&amp;hl=en&amp;u=https://example.org/x">this</a>
and <a href="https://www-example-com.translate.goog/other.shtml?""" + QA + """">that</a>.
<img srcset="https://cdn-example-com.translate.goog/s.jpg?""" + QA + """ 2x" src="https://news-cdn.example.com/1.jpg"></p>
</article>
<script>function gtElInit() {var lib = new google.translate.TranslateService();lib.translatePage('auto', 'en', function () {});}</script>
<script id="google-translate-element-script">(function(){ if(!window['_DumpException']) {} })();</script>
</body></html>"""

got = mod.clean(PAGE)
check_true("clean: a page comes back", got is not None, got)
got = got or ""
check_true("clean: the whole page, the site's chrome and the article",
           "the site's own chrome" in got and 'itemprop="reviewBody"' in got
           and "<title>T</title>" in got, got)
check_true("clean: no proxy URL or script survives",
           "translate.goog" not in got and "translate.google" not in got
           and "translate_http" not in got and "TranslateService" not in got
           and "google-translate-element-script" not in got, got)
check_true("clean: the proxy's metas and font go",
           "X-Translated" not in got and "Material+Symbols" not in got
           and 'content="none"' not in got, got)
check_true("clean: the site's own script and robots meta stay",
           "googletagmanager.com/gtag" in got and "max-image-preview:large" in got, got)
check_true("clean: the base href (the original URL) stays",
           '<base href="https://www.example.com/r/1.shtml"' in got, got)
check_true("clean: website?u= link unwrapped", 'href="https://example.org/x"' in got, got)
check_true("clean: translate.goog link unwrapped",
           'href="https://www.example.com/other.shtml"' in got, got)
check_true("clean: srcset unwrapped", 'srcset="https://cdn.example.com/s.jpg 2x"' in got, got)
check_true("clean: a direct URL is kept", 'src="https://news-cdn.example.com/1.jpg"' in got, got)
check_true("clean: the doctype is kept", got.lower().startswith("<!doctype html>"), got[:40])
check("clean: a page with no text is none",
      mod.clean("<html><head><title>T</title></head><body> <img src=x> </body></html>"), None)
check_true("clean: bytes with a declared charset",
           "café" in (mod.clean("<p>café</p>".encode("latin-1"), charset="iso-8859-1") or ""))

# --- run_item with a fake fetch ----------------------------------------------

calls = []


def fake_fetch(url, cfg):
    calls.append(url)
    return PAGE.encode("utf-8"), "utf-8"


real_fetch = mod.fetch
mod.fetch = fake_fetch
cfg, url, errors = mod.parse_argv([])
check("parse: no flags is no error", errors, [])
item = {"guid": 7, "title": "t", "content": "<p>teaser</p>",
        "link": "https://www.example.com/r/1.shtml", "published": None}
out = mod.run_item(dict(item), cfg)
check("run_item: asks the proxy for the link", calls,
      ["https://www-example-com.translate.goog/r/1.shtml?" + Q])
check("run_item: the content is the cleaned page", out["content"], mod.clean(PAGE))
check("run_item: guid, title, link, published unchanged",
      [out[k] for k in ("guid", "title", "link", "published")],
      [item[k] for k in ("guid", "title", "link", "published")])
try:
    mod.run_item(dict(item, link="http://www.example.com/r/1.shtml"), cfg)
    check_true("run_item: http link raises", False)
except ValueError:
    check_true("run_item: http link raises", True)
mod.fetch = lambda url, cfg: (b"<html><body></body></html>", "utf-8")
try:
    mod.run_item(dict(item), cfg)
    check_true("run_item: an empty page raises", False)
except ValueError:
    check_true("run_item: an empty page raises", True)
mod.fetch = real_fetch

# --- fetch: a busy proxy is asked again --------------------------------------

import http.server
import threading


class Busy(http.server.BaseHTTPRequestHandler):
    """Answers 429 (with Retry-After) or 503 `busy` times, then the page."""
    busy, codes, seen = 0, (429,), []

    def do_GET(self):
        Busy.seen.append(self.path)
        if Busy.busy > 0:
            code = Busy.codes[(len(Busy.seen) - 1) % len(Busy.codes)]
            Busy.busy -= 1
            self.send_response(code)
            if code == 429:
                self.send_header("Retry-After", "30")
            self.end_headers()
            return
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Busy)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d/p" % srv.server_address[1]
waits = []
real_sleep = mod.sleep
mod.sleep = waits.append
cfg, _, _ = mod.parse_argv([])

Busy.busy, Busy.codes, Busy.seen = 2, (429, 503), []
body, charset = mod.fetch(base, cfg)
check("fetch: a 429 and a 503 are asked again, then the page", (len(Busy.seen), charset), (3, "utf-8"))
check("fetch: waits the schedule, a longer Retry-After instead", waits, [30, mod.RETRY_WAITS[1]])
check_true("fetch: the page after the retries", b"reviewBody" in body)

waits.clear()
Busy.busy, Busy.codes, Busy.seen = 99, (429,), []
try:
    mod.fetch(base, cfg)
    check_true("fetch: a proxy busy past the schedule raises", False)
except OSError as e:
    check_true("fetch: a proxy busy past the schedule raises", "429" in str(e), e)
check("fetch: one try per wait, plus the first", len(Busy.seen), len(mod.RETRY_WAITS) + 1)
# The longest run: every wait stretched to RETRY_AFTER_MAX and every request
# at its timeout, against drr's cmd-timeout of 10 minutes.
longest = (sum(max(w, mod.RETRY_AFTER_MAX) for w in mod.RETRY_WAITS)
           + (len(mod.RETRY_WAITS) + 1) * mod.DEFAULT_TIMEOUT)
check_true("fetch: the longest run stays minutes under drr's cmd-timeout", longest <= 7 * 60, longest)

waits.clear()
Busy.busy, Busy.codes, Busy.seen = 1, (404,), []
try:
    mod.fetch(base, cfg)
    check_true("fetch: another status is not asked again", False)
except OSError:
    check("fetch: another status is not asked again", (len(Busy.seen), waits), (1, []))
mod.sleep = real_sleep
srv.shutdown()

# --- parse_argv --------------------------------------------------------------

check_true("parse: --selector is gone (drr's #readability picks the body)",
           mod.parse_argv(["--selector", "p"])[2] != [])
check_true("parse: --drop is gone (drr's #strip removes)",
           mod.parse_argv(["--drop", "p"])[2] != [])
check_true("parse: bad timeout", mod.parse_argv(["--timeout", "0"])[2] != [])
check_true("parse: a flag without its value", mod.parse_argv(["--ua"])[2] != [])
check("parse: a URL is a manual run",
      mod.parse_argv(["https://example.com/a"])[1], "https://example.com/a")
check_true("parse: two URLs", mod.parse_argv(["https://example.com/a", "https://example.com/b"])[2] != [])

# --- the protocol path, as drr runs it ---------------------------------------

ITEM = json.dumps(dict(item, raw={"k": "v"}))


def run(args, stdin):
    return subprocess.run([sys.executable, SCRIPT] + args, input=stdin,
                          capture_output=True, text=True, timeout=60)


p = run([], ITEM.replace("https://www", "http://www"))
check("protocol: a failing item exits 0", p.returncode, 0)
check("protocol: and emits nothing", p.stdout, "")
check_true("protocol: and says why on stderr", "https" in p.stderr, p.stderr)
# The flags of srr-gtproxy 1.x, still in a feed's pipe: never a manual run,
# whose non-zero exit would make drr drop the item for good.
p = run(["--selector", "article[itemprop=reviewBody], article[itemprop=articleBody]",
         "--drop", ".galart2_last, .galart2_img.hidden"], ITEM)
check("protocol: 1.x flags exit 0 with nothing on stdout", (p.returncode, p.stdout), (0, ""))
check_true("protocol: and name the old flag", "--selector" in p.stderr, p.stderr)
check_true("parse: a value that is no URL is no manual run",
           mod.parse_argv(["--selector", "p"])[1] is None)
p = run([], "{not json")
check("protocol: bad JSON exits 0 with nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run(["--help"], ITEM)
check_true("protocol: --help on stderr only", p.stdout == "" and "KNOBS" in p.stderr)
p = run(["--version"], ITEM)
check("protocol: --version on stderr only", (p.stdout, p.stderr.split()[:2]), ("", ["srr-gtproxy", "2.1"]))
p = run(["http://example.com/a"], "")
check_true("manual: a failure exits non-zero", p.returncode != 0 and "https" in p.stderr, p.stderr)

if failures:
    print("\n%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nall passed")
