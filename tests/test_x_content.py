"""Unit tests for srr-x's content pipeline, driven by a nitter.net RSS
snapshot (tests/fixture_x_nasa.rss, @NASA — structure mirrors a real nitter
capture from 2026-07-04, content fictionalized): every
nitter artifact must be rewritten back to canonical X / Twitter-CDN URLs so
the stored items never depend on the instance staying alive. Also covers
--no-retweets, --selfhost downloads (marker, cache reuse, fallback, latch)
and the etag/last-modified cursor.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_x_content.py
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-x")

loader = importlib.machinery.SourceFileLoader("srr_x", SCRIPT)
spec = importlib.util.spec_from_loader("srr_x", loader)
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


def reset_modes():
    mod.SELFHOST = False
    mod.NO_RETWEETS = False
    mod.NO_REPLIES = False
    mod.INSTANCE = "https://nitter.net"


with open(os.path.join(HERE, "fixture_x_nasa.rss"), "rb") as fh:
    FIXTURE = fh.read()


def by_guid(items, tweet_id):
    return next(i for i in items if i["guid"] == mod.fnv1a32(tweet_id))


# --- the full feed, default (hotlink) mode -------------------------------------

reset_modes()
items = mod.build_items(FIXTURE, "", 0)
check("fixture: all 19 items parsed", len(items), 19)
check_true("fixture: no nitter URL survives anywhere",
           "nitter.net" not in json.dumps(items))
check_true("fixture: no #m fragment survives",
           not any(i["link"].endswith("#m") for i in items))
check_true("fixture: nitter's max-width style is stripped",
           not any("style=" in i["content"] for i in items))
check_true("fixture: titles capped like srr-telegram's",
           all(len(i["title"]) <= 118 for i in items))

it = by_guid(items, "2073206182781612352")     # RT of @NASA's own tweet
check("item: link is the canonical x.com status URL",
      it["link"], "https://x.com/NASA/status/2073206182781612352")
check("item: pubDate becomes RFC 3339",
      it["published"], "2026-07-04T00:44:06+00:00")
check("item: title is the tweet text",
      it["title"], "RT by @NASA: LIFTOFF! WE HAVE A LIFTOFF...!!!")

it = by_guid(items, "2073358761142432031")     # RT with a photo (@NASAHubble)
check_true("item: /pic/ image rewritten to pbs.twimg.com",
           '<img src="https://pbs.twimg.com/media/HMYMzfnWEAAHqhT.jpg"' in it["content"],
           it["content"])
check_true("item: retweets are attributed to the original author",
           it["content"].startswith(
               '<p>RT <a href="https://x.com/NASAHubble">@NASAHubble</a></p>'),
           it["content"])

it = by_guid(items, "2073121959144014277")     # RT with a video (@NASA_Stennis)
check_true("item: video poster rewritten to pbs.twimg.com",
           '<img src="https://pbs.twimg.com/amplify_video_thumb/'
           '2073121879032758272/img/bPVz8Fu9EPaxLt1u.jpg"' in it["content"],
           it["content"])
check_true("item: video placeholder links to the tweet on x.com",
           '<a href="https://x.com/NASA_Stennis/status/2073121959144014277">'
           in it["content"], it["content"])

it = by_guid(items, "2073373486597239210")     # RT with a mention + quote (@NASAEarth)
check_true("item: mention links point at x.com",
           '<a href="https://x.com/NASA"' in it["content"], it["content"])
check_true("item: quoted tweet's blockquote survives",
           "<blockquote>" in it["content"], it["content"])
check_true("item: quote citation TEXT (not just href) is the x.com URL",
           ">https://x.com/NASAWebb/status/2073093707872584191</a>"
           in it["content"], it["content"])

check_true("fixture: external links are left untouched",
           any("http://lc.cx/CT8Isx" in i["content"] for i in items))

mod.NO_RETWEETS = True
check("--no-retweets: the fixture (all RTs) yields nothing",
      len(mod.build_items(FIXTURE, "", 0)), 0)
reset_modes()
mod.NO_REPLIES = True
check("--no-replies: does not touch retweets (fixture intact)",
      len(mod.build_items(FIXTURE, "", 0)), 19)
reset_modes()

# --- synthetic feed: the shapes the fixture doesn't exercise --------------------
#
# Title prefixes per nitter's rss.nimf: "Pinned: " / "RT by @x: " / "R to @x: ",
# mutually exclusive (if/elif), in that precedence order.

SYNTH = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><channel>
<title>User / @user</title><link>https://nitter.net/user</link>
<item>
  <title>Plain tweet with a hashtag</title>
  <dc:creator>@user</dc:creator>
  <description>&lt;p&gt;Hola &lt;a href="https://nitter.net/search?q=%23Artemis"&gt;#Artemis&lt;/a&gt;&lt;/p&gt;
&lt;img src="https://nitter.net/pic/orig/media%2FAAA.jpg" style="max-width:250px;" /&gt;
&lt;img src="https://nitter.net/pic/pbs.twimg.com%2Fprofile_images%2F123%2Fx.jpg" /&gt;</description>
  <pubDate>Wed, 01 Jul 2026 10:00:00 GMT</pubDate>
  <guid isPermaLink="false">111</guid>
  <link>https://nitter.net/user/status/111#m</link>
</item>
<item>
  <title>R to @SpaceX: Yes, exactly.</title>
  <dc:creator>@user</dc:creator>
  <description>&lt;p&gt;Yes, exactly.&lt;/p&gt;</description>
  <pubDate>Wed, 01 Jul 2026 11:00:00 GMT</pubDate>
  <guid isPermaLink="false">222</guid>
  <link>https://nitter.net/user/status/222#m</link>
</item>
<item>
  <title>Pinned: Big announcement</title>
  <dc:creator>@user</dc:creator>
  <description>&lt;p&gt;Big announcement&lt;/p&gt;</description>
  <pubDate>Mon, 01 Jan 2024 09:00:00 GMT</pubDate>
  <guid isPermaLink="false">333</guid>
  <link>https://nitter.net/user/status/333#m</link>
</item>
</channel></rss>"""

reset_modes()
items = mod.build_items(SYNTH.encode(), "", 0)
it = items[0]
check_true("synthetic: own tweets get no RT attribution",
           not it["content"].startswith("<p>RT "), it["content"])
check_true("synthetic: hashtag search link rewritten to x.com",
           '<a href="https://x.com/search?q=%23Artemis"' in it["content"],
           it["content"])
check_true("synthetic: /pic/orig/ rewritten to the full-size CDN variant",
           '<img src="https://pbs.twimg.com/media/AAA.jpg?name=orig"' in it["content"],
           it["content"])
check_true("synthetic: host-carrying /pic/ path keeps its host",
           '<img src="https://pbs.twimg.com/profile_images/123/x.jpg"' in it["content"],
           it["content"])
check("synthetic: title passes through", it["title"], "Plain tweet with a hashtag")
check("synthetic: guid hashes the tweet id", it["guid"], mod.fnv1a32("111"))

it = items[1]                                   # self-thread / reply
check("reply: nitter's 'R to @x:' jargon stripped from the title",
      it["title"], "Yes, exactly.")
check_true("reply: body attributes the replied-to account",
           it["content"].startswith(
               '<p>↩ replying to <a href="https://x.com/SpaceX">@SpaceX</a></p>'),
           it["content"])

it = items[2]                                   # pinned tweet
check("pinned: 'Pinned:' jargon stripped from the title",
      it["title"], "Big announcement")
check_true("pinned: no attribution line added",
           it["content"] == "<p>Big announcement</p>", it["content"])

mod.NO_REPLIES = True
check("--no-replies: reply dropped, plain and pinned kept",
      [i["guid"] for i in mod.build_items(SYNTH.encode(), "", 0)],
      [mod.fnv1a32("111"), mod.fnv1a32("333")])
reset_modes()
mod.NO_RETWEETS = True
check("--no-retweets: does not touch replies",
      len(mod.build_items(SYNTH.encode(), "", 0)), 3)
reset_modes()

# rss.xcancel.com pads its XML with leading whitespace, which a bare
# ET.fromstring rejects ("XML declaration not at start of entity").
try:
    n = len(mod.build_items(b"  \n  " + SYNTH.encode(), "", 0))
except ET.ParseError:
    n = "ParseError"
check("whitespace-padded XML (rss.xcancel.com style) parses", n, 3)

# --- --selfhost: download media into the store ----------------------------------
#
# fake pbs.twimg.com: url -> ("body", bytes) | "404" | "neterr"; unrouted 404.

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
    CALLS.append((req_obj.get_method(), url, dict(req_obj.headers)))
    r = ROUTES.get(url, "404")
    if r == "404":
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    if r == "neterr":
        raise urllib.error.URLError("connection reset")
    return FakeResp(r[1], {"Content-Length": str(len(r[1]))})


mod.urllib.request.urlopen = fake_urlopen


def reset(routes, selfhost):
    CALLS.clear()
    ROUTES.clear()
    ROUTES.update(routes)
    mod.probing = True
    reset_modes()
    mod.SELFHOST = selfhost


PIC = "https://pbs.twimg.com/media/HMYMzfnWEAAHqhT.jpg"
MARKER = "#/" + mod.asset_rel(PIC)

STORE = tempfile.mkdtemp(prefix="srr-x-test-")


def store():
    shutil.rmtree(STORE, ignore_errors=True)
    os.makedirs(STORE)
    return STORE


def dest():
    return os.path.join(STORE, mod.asset_rel(PIC))


check("asset names: deterministic hash of the CDN URL, extension kept",
      mod.asset_rel(PIC), "x/%08x.jpg" % mod.fnv1a32(PIC))

reset({PIC: ("body", b"IMG-BYTES")}, selfhost=True)
it = by_guid(mod.build_items(FIXTURE, store(), 0), "2073358761142432031")
check_true("selfhost: content uses the store marker",
           ('<img src="%s"' % MARKER) in it["content"], it["content"])
check_true("selfhost: downloads straight from the CDN, not via nitter",
           all(u.startswith("https://pbs.twimg.com/") for _, u, _ in CALLS),
           CALLS)
with open(dest(), "rb") as fh:
    check("selfhost: CDN bytes stored", fh.read(), b"IMG-BYTES")

# Cache: a stored file is reused without any request, and its mtime refreshed
# (srr's age-based cache sweep must never delete a file a live feed consumes).
os.utime(dest(), (1, 1))
CALLS.clear()
check("selfhost: cache hit -> marker, no request",
      (mod.selfhost_media(PIC, STORE, 0), CALLS), (MARKER, []))
check_true("selfhost: cache hit refreshes mtime", os.path.getmtime(dest()) > 1)

reset({}, selfhost=True)                        # 404: pbs dropped the media
it = by_guid(mod.build_items(FIXTURE, store(), 0), "2073358761142432031")
check_true("selfhost: missing media falls back to the hotlink",
           ('<img src="%s"' % PIC) in it["content"], it["content"])
check_true("selfhost: 404 does not latch probing off", mod.probing is True)

reset({PIC: "neterr"}, selfhost=True)           # network trouble latches off
mod.build_items(FIXTURE, store(), 0)
check_true("selfhost: network error latches downloads off", mod.probing is False)
CALLS.clear()
check("selfhost: latched off -> hotlink, no request",
      (mod.selfhost_media(PIC, STORE, 0), CALLS), (None, []))

reset({PIC: ("body", b"IMG")}, selfhost=True)   # preview sends no asset_dir
it = by_guid(mod.build_items(FIXTURE, "", 0), "2073358761142432031")
check("selfhost without a store: hotlinks (preview), no requests",
      (('<img src="%s"' % PIC) in it["content"], CALLS), (True, []))

# --- fetch: feed URL, cursor headers, 304, 429, redirects -------------------------
#
# The feed fetch rides httpx over HTTP/2 (nitter.poast.org 403s HTTP/1.1);
# tests swap the module client for one on a MockTransport.

SEEN = []


def use_transport(handler):
    SEEN.clear()
    mod.HTTP = mod.httpx.Client(transport=mod.httpx.MockTransport(handler),
                                follow_redirects=True)


def serve_fixture(request):
    SEEN.append(request)
    return mod.httpx.Response(200, content=FIXTURE,
                              headers={"ETag": 'W/"served"',
                                       "Last-Modified": "lm"})


reset_modes()
use_transport(serve_fixture)
got = mod.fetch_feed({"url": "https://x.com/NASA", "etag": 'W/"e"'})
check("fetch: GETs <instance>/<user>/rss",
      str(SEEN[0].url), "https://nitter.net/NASA/rss")
check("fetch: cursor sent as If-None-Match",
      SEEN[0].headers.get("If-None-Match"), 'W/"e"')
check("fetch: body handed to the parser", got[0], FIXTURE)
check("fetch: response cursor kept for the next cycle",
      (got[1], got[2]), ('W/"served"', "lm"))

use_transport(lambda req: mod.httpx.Response(304))
check("fetch: 304 -> None (not_modified)",
      mod.fetch_feed({"url": "@NASA", "etag": "e"}), None)
check("run_ingest: 304 -> not_modified response",
      mod.run_ingest({"url": "@NASA", "etag": "e"}), {"not_modified": True})


# xcancel.com/…/rss 302s to its dedicated rss.xcancel.com host: follow it.
def redirect_once(request):
    SEEN.append(request)
    if request.url.host == "nitter.net":
        return mod.httpx.Response(
            302, headers={"Location": "https://rss.example.com/NASA/rss"})
    return mod.httpx.Response(200, content=FIXTURE)


use_transport(redirect_once)
check("fetch: redirects followed (the xcancel.com pattern)",
      mod.fetch_feed({"url": "@NASA"})[0], FIXTURE)

# Rate limits deserve a diagnosis, not a stack trace: nitter's Retry-After
# header is untrustworthy (measured: says 1s, refills ~40s), so the only
# useful advice is to wait or switch instances.
use_transport(lambda req: mod.httpx.Response(429, headers={"Retry-After": "1"}))
try:
    mod.fetch_feed({"url": "@NASA"})
    got = "no exit"
except SystemExit as e:
    got = str(e)
check_true("fetch: 429 -> clear rate-limit exit suggesting --instance",
           "429" in got and "--instance" in got, got)

shutil.rmtree(STORE, ignore_errors=True)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
