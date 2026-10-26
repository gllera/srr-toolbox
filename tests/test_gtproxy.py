"""Tests for srr-gtproxy: the proxy URL and its inverse, the body it keeps
(selector, nesting, --drop, unwrapped links), run_item with a fake fetch, and
the protocol path, which never exits non-zero and emits nothing on failure.

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

# --- extract ----------------------------------------------------------------

PAGE = """<!doctype html><html><head><title>T</title></head><body>
<div class="teaser">not this</div>
<article itemprop="reviewBody"><p class="intro">First <b>para</b>.</p>
<div class="gallery"><img src="https://cdn.example.com/1.jpg">
<div class="more">+4<span>more</span></div>
<div class="slide hidden"><img src="https://cdn.example.com/2.jpg"></div></div>
<p>See <a href="https://translate.google.com/website?sl=auto&amp;tl=en&amp;hl=en&amp;u=https://example.org/x">this</a>
and <a href="https://www-example-com.translate.goog/other.shtml?""" + Q.replace("&", "&amp;") + """">that</a>.
<img srcset="https://cdn-example-com.translate.goog/s.jpg?""" + Q.replace("&", "&amp;") + """ 2x"></p>
<article itemprop="reviewBody"><p>nested</p></article>
</article>
<article itemprop="articleBody"><p>Second body.</p></article>
</body></html>"""
SEL = "article[itemprop=reviewBody], article[itemprop=articleBody]"

got = mod.extract(PAGE, SEL, ".more, .slide.hidden")
check_true("extract: the bodies, not the teaser", got is not None and "not this" not in got, got)
check_true("extract: both bodies in document order",
           got is not None and got.index("First") < got.index("Second body."), got)
check("extract: a nested match is kept once", got.count("nested"), 1)
check_true("extract: --drop removes the tile and the hidden slide",
           "+4" not in got and "2.jpg" not in got and "1.jpg" in got, got)
check_true("extract: website?u= link unwrapped", 'href="https://example.org/x"' in got, got)
check_true("extract: translate.goog link unwrapped",
           'href="https://www.example.com/other.shtml"' in got, got)
check_true("extract: srcset unwrapped",
           'srcset="https://cdn.example.com/s.jpg 2x"' in got, got)
check_true("extract: no proxy URL survives",
           "translate.goog" not in got and "translate.google" not in got, got)
check_true("extract: inner HTML, a match's own tag is not kept",
           not got.lstrip().startswith("<article") and 'itemprop="articleBody"' not in got, got)
check("extract: no match", mod.extract(PAGE, "main.nothing"), None)
check("extract: a match with only whitespace",
      mod.extract("<article class=b> <img src=x> </article>", "article.b"), None)
check_true("extract: bytes with a declared charset",
           "café" in (mod.extract("<p class=x>café</p>".encode("latin-1"), "p.x",
                                  charset="iso-8859-1") or ""))

# --- run_item with a fake fetch ----------------------------------------------

calls = []


def fake_fetch(url, cfg):
    calls.append(url)
    return PAGE.encode("utf-8"), "utf-8"


real_fetch = mod.fetch
mod.fetch = fake_fetch
cfg, url, errors = mod.parse_argv(["--selector", SEL])
check("parse: no errors", errors, [])
item = {"guid": 7, "title": "t", "content": "<p>teaser</p>",
        "link": "https://www.example.com/r/1.shtml", "published": None}
out = mod.run_item(dict(item), cfg)
check("run_item: asks the proxy for the link", calls,
      ["https://www-example-com.translate.goog/r/1.shtml?" + Q])
check_true("run_item: content replaced", "First" in out["content"] and "teaser" not in out["content"])
check("run_item: guid, title, link, published unchanged",
      [out[k] for k in ("guid", "title", "link", "published")],
      [item[k] for k in ("guid", "title", "link", "published")])
try:
    mod.run_item(dict(item, link="http://www.example.com/r/1.shtml"), cfg)
    check_true("run_item: http link raises", False)
except ValueError:
    check_true("run_item: http link raises", True)
cfg2, _, _ = mod.parse_argv(["--selector", "main.nothing"])
try:
    mod.run_item(dict(item), cfg2)
    check_true("run_item: no match raises", False)
except ValueError:
    check_true("run_item: no match raises", True)
mod.fetch = real_fetch

# --- parse_argv --------------------------------------------------------------

check_true("parse: --selector is required", mod.parse_argv([])[2] != [])
check_true("parse: unknown option", mod.parse_argv(["--selector", "p", "--sel", "x"])[2] != [])
check_true("parse: bad timeout", mod.parse_argv(["--selector", "p", "--timeout", "0"])[2] != [])
check_true("parse: bad selector", mod.parse_argv(["--selector", "p[["])[2] != [])
check("parse: a URL is a manual run",
      mod.parse_argv(["--selector", "p", "https://example.com/a"])[1], "https://example.com/a")

# --- the protocol path, as drr runs it ---------------------------------------

ITEM = json.dumps(dict(item, raw={"k": "v"}))


def run(args, stdin):
    return subprocess.run([sys.executable, SCRIPT] + args, input=stdin,
                          capture_output=True, text=True, timeout=60)


p = run(["--selector", SEL], ITEM.replace("https://www", "http://www"))
check("protocol: a failing item exits 0", p.returncode, 0)
check("protocol: and emits nothing", p.stdout, "")
check_true("protocol: and says why on stderr", "https" in p.stderr, p.stderr)
p = run(["--selector"], ITEM)
check("protocol: a broken flag exits 0 with nothing on stdout",
      (p.returncode, p.stdout), (0, ""))
p = run(["--selector", SEL], "{not json")
check("protocol: bad JSON exits 0 with nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run(["--help"], ITEM)
check_true("protocol: --help on stderr only", p.stdout == "" and "KNOBS" in p.stderr)
p = run(["--version"], ITEM)
check("protocol: --version on stderr only", (p.stdout, p.stderr.split()[0]), ("", "srr-gtproxy"))
p = run(["--selector", SEL, "http://example.com/a"], "")
check_true("manual: a failure exits non-zero", p.returncode != 0 and "https" in p.stderr, p.stderr)

if failures:
    print("\n%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nall passed")
