"""Tests for srr-xstatus: the syndication token, finding the stubs (a blockquote,
a paragraph, bare top-level links; pasted tweets and other links untouched),
the rendering of a photo, a video and a text post (recorded or hand-built
syndication JSON in tests/fixtures_xstatus/, handles anonymised), and the
protocol path against a local http.server: fail open, exit 0, always.

Run in the repo's uv project venv:
    uv run tests/test_xstatus.py
"""
import http.server
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-xstatus")
FIX = os.path.join(HERE, "fixtures_xstatus")

loader = importlib.machinery.SourceFileLoader("srr_xstatus", SCRIPT)
spec = importlib.util.spec_from_loader("srr_xstatus", loader)
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


def fixture(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


# --- the token ----------------------------------------------------------------
# Pairs computed with node: ((Number(id)/1e15)*Math.PI).toString(36).replace(/(0+|\.)/g,"")
check("token: id 20", mod.token(20), "6dq1a2xwd93")
check("token: a 2026 id", mod.token(2114419878342046044), "54in95qpl69")
check_true("token: shape (base 36, no dot or zero)",
          all(c in mod.DIGITS and c != "0" for c in mod.token(1850000000000000000)))

# --- status_id -----------------------------------------------------------------
for u in ("https://x.com/u/status/123", "http://twitter.com/u/status/123/",
          "https://mobile.twitter.com/u/status/123?ref_src=twsrc%5Etfw",
          "https://twitter.com/u/status/123?ref_src=twsrc%5Etfw#x"):
    check("status id " + u, mod.status_id(u), "123")
for u in ("https://x.com/u", "https://x.com/u/status/abc", "https://example.com/u/status/1",
          "https://x.com/i/web/status/1", "https://notx.com/u/status/1", "javascript:1", "", None,
          "https://x.com/u/status/1/photo/1"):
    check("not a status: %r" % u, mod.status_id(u), None)

# --- finding the stubs ---------------------------------------------------------
A = '<a href="https://twitter.com/u/status/123?ref_src=twsrc%5Etfw"><b>u</b></a>'


def found(content):
    soup = mod.BeautifulSoup(content, "html.parser")
    return [(n.name, k) for n, _, k in mod.candidates(soup)]


check("stub: twitter-tweet blockquote with brackets",
      found('<blockquote class="twitter-tweet">[%s]</blockquote>' % A), [("blockquote", "block")])
check("stub: blockquote whose child is a p",
      found('<blockquote class="twitter-tweet"><p>[%s]</p></blockquote>' % A), [("blockquote", "block")])
check("stub: a plain p", found("<p>[%s]</p>" % A), [("p", "block")])
check("stub: bare top-level link", found("[%s]" % A), [("a", "bare")])
check("stub: bare after a paragraph", found("<p>Caption</p>\n[%s]\n" % A), [("a", "bare")])
check("untouched: embed markup with the post's text and a date link",
      found('<blockquote class="twitter-tweet"><p lang="es">Texto del post</p>&mdash; U (@u) '
            '<a href="https://twitter.com/u/status/123?ref_src=twsrc%5Etfw">October 25, 2026</a></blockquote>'), [])
check("untouched: blockquote with an image", found("<blockquote>[%s]<img src='https://e.com/a.jpg'></blockquote>" % A), [])
check("untouched: a pasted tweet with text", found("<blockquote>Look at this great thing %s</blockquote>" % A), [])
check("untouched: paragraph with prose around the link", found("<p>Mira esto: %s ahora</p>" % A), [])
check("untouched: bare link inside prose", found("Mira esto: %s" % A), [])
check("untouched: a non-status link",
      found('<blockquote>[<a href="https://x.com/u"><b>u</b></a>]</blockquote><p><a href="https://example.com/a">a</a></p>'), [])
check("untouched: two different statuses in one block",
      found('<p>%s <a href="https://x.com/u/status/9">x</a></p>' % A), [])
check("untouched: nested in a list item", found("<ul><li>[%s]</li></ul>" % A), [])

# --- rendering -----------------------------------------------------------------
photo = json.loads(fixture("photo.json"))
r = mod.render(photo)
check_true("photo: author linked, name escaped",
           '<a href="https://x.com/exampleuser/status/111"><b>Example &lt;User&gt;</b> @exampleuser</a>' in r, r)
check_true("photo: t.co expanded, media link and text escaped",
           '<p>Read this: <a href="https://example.com/post">example.com/post</a> &lt;b&gt; &amp; more</p>' in r, r)
check_true("photo: no t.co left", "t.co" not in r, r)
check_true("photo: both images, alt escaped",
           '<img src="https://pbs.twimg.com/media/AAAA.jpg" alt="a &quot;cat&quot;">' in r
           and '<img src="https://pbs.twimg.com/media/BBBB.jpg" alt="">' in r, r)
check_true("photo: no video", "<video" not in r, r)

video = json.loads(fixture("video.json"))
r = mod.render(video)
check_true("video: best mp4 (highest bitrate)",
           '<video controls src="https://video.twimg.com/amplify_video/2114419845630418944/vid/avc1/1080x1920/m2j6zA1BomBuTrO2.mp4">' in r, r)
check_true("video: poster linked to the post",
           '<a href="https://x.com/exampleuser/status/2114419878342046044"><img src="https://pbs.twimg.com/amplify_video_thumb/' in r, r)
check_true("video: the media t.co is gone from the text", "t.co" not in r and "Es mi nuevo video favorito" in r, r)
check_true("video: no m3u8", "m3u8" not in r, r)

text = json.loads(fixture("text.json"))
r = mod.render(text)
check_true("text: paragraphs and breaks, entities unescaped once",
           "<p>First line &amp; more</p><p>Second para<br>with a break</p>" in r, r)
check_true("text: quoted post is a nested blockquote, text only, escaped",
           "<blockquote><p><a href=\"https://x.com/quoted/status/333\"><b>Q</b> @quoted</a></p>"
           "<p>Quoted &lt;script&gt;alert(1)&lt;/script&gt;</p></blockquote></blockquote>" in r, r)
check_true("text: quoted media not shown", "QQQQ" not in r, r)
check_true("text: no raw script", "<script" not in r, r)

# --- a local syndication server ------------------------------------------------
ROUTES = {}   # id -> (status, body)
SEEN = []


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        import urllib.parse
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
        SEEN.append(q)
        status, body = ROUTES.get(q.get("id"), (404, "{}"))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
EP = "http://127.0.0.1:%d/tweet-result" % srv.server_address[1]
CFG = {"timeout": 3.0, "endpoint": EP, "max": 10}

ROUTES["111"] = (200, fixture("photo.json"))
ROUTES["222"] = (200, fixture("text.json"))
ROUTES["500"] = (500, "boom")
ROUTES["600"] = (200, "not json {")
ROUTES["700"] = (200, '{"__typename":"TweetTombstone","tombstone":{"text":{"text":"gone"}}}')
ROUTES["800"] = (200, '{"__typename":"Tweet","text":"x","user":"nope"}')


def st(i, label="u"):
    return '<a href="https://x.com/u/status/%s?ref_src=twsrc%%5Etfw"><b>%s</b></a>' % (i, label)


content = ('<p>Intro</p><blockquote class="twitter-tweet">[%s]</blockquote>'
           '<p>[%s]</p>[%s]<blockquote>pasted text %s</blockquote>' % (st(111), st(222), st(500), st(111)))
new, n = mod.expand(content, CFG)
check("expand: replaced the two good stubs, one failed", n, 2)
check_true("expand: photo post in place of the blockquote", "twitter-tweet" not in new and "AAAA.jpg" in new, new)
check_true("expand: text post in place of the p", "First line" in new, new)
check_true("expand: the 500 stub stays, brackets and all", "[" + st(500) + "]" in new, new)
check_true("expand: the pasted tweet stays", "<blockquote>pasted text" in new, new)
check_true("expand: intro kept", new.startswith("<p>Intro</p>"), new)
check_true("expand: token sent matches the formula",
           any(q.get("token") == mod.token(int(q["id"])) and q.get("lang") == "es" for q in SEEN), SEEN)

SEEN.clear()
dup = "".join("<p>%s</p>" % st(111) for _ in range(3))
new, n = mod.expand(dup, CFG)
check("expand: the same post is fetched once", (n, len(SEEN)), (3, 1))

SEEN.clear()
many = "".join("<p>%s</p>" % st(111 if i % 2 else 222) for i in range(14))
new, n = mod.expand(many, dict(CFG, max=10))
check("expand: at most 10 per item, the rest stay stubs", (n, new.count("status/111?ref_src") + new.count("status/222?ref_src")), (10, 4))

for tid, why in (("500", "HTTP 500"), ("600", "bad JSON"), ("700", "tombstone"), ("800", "wrong shape"), ("404", "HTTP 404")):
    c = "<p>[%s]</p>" % st(tid)
    check("expand: unchanged on " + why, mod.expand(c, CFG), (c, 0))

dead = dict(CFG, endpoint="http://127.0.0.1:1/x", timeout=1.0)
check("expand: unchanged when the endpoint is down", mod.expand("<p>[%s]</p>" % st(111), dead)[1], 0)

# --- the protocol path ---------------------------------------------------------


def run(stdin, *args):
    p = subprocess.run([sys.executable, "-I", "-c",
                        "import importlib.machinery as m,sys;"
                        "l=m.SourceFileLoader('x',sys.argv[1]);mod=l.load_module();sys.argv=sys.argv[1:];mod.main()",
                        SCRIPT, "--endpoint", EP, *args],
                       input=stdin, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)))
    return p


item = {"guid": 7, "title": "t", "content": "<p>[%s]</p>" % st(222), "link": "https://example.com/a",
        "published": None, "raw": "RAW"}
p = run(json.dumps(item))
check("protocol: exit 0", p.returncode, 0)
out = json.loads(p.stdout)
check_true("protocol: content replaced, raw not echoed, other fields kept",
           "First line" in out["content"] and "raw" not in out and out["guid"] == 7 and out["title"] == "t", p.stdout)

p = run(json.dumps(dict(item, content="<p>[%s]</p>" % st(500))))
check("protocol: HTTP error -> exit 0, nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run(json.dumps(dict(item, content="<p>[%s]</p>" % st(600))))
check("protocol: bad JSON answer -> exit 0, nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run(json.dumps(dict(item, content="<p>no stub</p>")))
check("protocol: no stub -> nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run("not json")
check("protocol: bad stdin -> exit 0, nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run(json.dumps(item), "--bogus")
check("protocol: bad flag -> exit 0, nothing on stdout", (p.returncode, p.stdout), (0, ""))
p = run(json.dumps(dict(item, content=None)))
check("protocol: null content -> exit 0, nothing on stdout", (p.returncode, p.stdout), (0, ""))

# A manual run is loud.
p = run("", "https://x.com/u/status/500")
check_true("manual: failure exits non-zero", p.returncode != 0, p.stderr)
p = run("", "https://x.com/u/status/222")
check_true("manual: prints the item", p.returncode == 0 and "First line" in json.loads(p.stdout)["content"], p.stderr)

srv.shutdown()
if failures:
    print("\n%d FAILED: %s" % (len(failures), ", ".join(failures)))
    sys.exit(1)
print("\nall passed")
