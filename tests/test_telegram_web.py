"""Unit tests for srr-telegram's web (no-account) mode: URL/username parsing,
rich-text cleaning, and t.me/s widget parsing.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_telegram_web.py
"""
import http.client
import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-telegram")

loader = importlib.machinery.SourceFileLoader("srr_telegram", SCRIPT)
spec = importlib.util.spec_from_loader("srr_telegram", loader)
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


# --- URL forms ---------------------------------------------------------------

# Public-username forms resolve; private/invite/numeric forms must NOT.
for url, want in [
    ("https://t.me/s/durov", "durov"),
    ("http://telegram.me/s/durov", "durov"),
    ("https://t.me/durov", "durov"),
    ("t.me/durov?foo=1", "durov"),
    ("@durov", "durov"),
    ("https://t.me/c/1234567890", None),      # private-id form
    ("https://t.me/+AbCdEf123", None),        # invite link
    ("https://t.me/joinchat/AbCdEf123", None),
    ("-1001234567890", None),                 # bare numeric id
    ("https://example.com/whatever", None),
]:
    check("web_username(%r)" % url, mod.web_username(url), want)

check_true("account (MTProto) is the default mode (--no-auth opts into web)",
           mod.AUTH_MODE is True)
check_true("media download is opt-in (--selfhost)", mod.SELFHOST is False)


# --- rich-text cleaning ------------------------------------------------------

def text_el(inner):
    soup = mod.BeautifulSoup(
        '<div class="tgme_widget_message_text js-message_text" dir="auto">'
        + inner + "</div>", "html.parser")
    return soup.div


# Real t.me shape: tg-emoji wrappers collapse to the bare emoji, <br/><br/>
# separates paragraphs, a lone <br/> is a soft break, hrefs are absolutized
# and only allowlisted tags survive.
real = (
    '<tg-emoji emoji-id="5"><i class="emoji" style="background-image:url(\'x\')">'
    "<b>\U0001f30d</b></i></tg-emoji> With 400 validators.<br/><br/>"
    'Boost <a href="/durov?boost" onclick="im_malicious()">here</a> and '
    '<a href="https://e.com/a?b=1&amp;c=2"><b>bold link</b></a>.<br/>soft line'
)
check(
    "real widget text -> clean paragraphs",
    mod.widget_text_html(text_el(real)),
    "<p>\U0001f30d With 400 validators.</p>"
    '<p>Boost <a href="https://t.me/durov?boost">here</a> and '
    '<a href="https://e.com/a?b=1&amp;c=2"><b>bold link</b></a>.<br>soft line</p>',
)

# Unknown wrappers unwrap, text is escaped, no attributes leak through.
check("unwrap + escape",
      mod.widget_text_html(text_el('<span class="x">a &lt; b</span> &amp; c')),
      "<p>a &lt; b &amp; c</p>")

check("empty div -> empty", mod.widget_text_html(text_el("")), "")
check("no text element -> empty", mod.widget_text_html(None), "")

# Trailing/leading <br> runs never produce empty paragraphs or <br><br>.
for inner in ["a<br/><br/>b", "a<br/><br/><br/>b", "<br/>a<br/><br/>b<br/>",
              "one<br/>two<br/><br/>three"]:
    out = mod.widget_text_html(text_el(inner))
    check_true("no <br><br> in %r" % inner, "<br><br>" not in out, out)
    check_true("no empty <p> in %r" % inner, "<p></p>" not in out, out)

# Captions (for titles) are the plain text of the cleaned markup.
check("caption strips markup",
      mod.widget_caption(text_el("<b>Title line</b><br/><br/>body")),
      "Title line\n\nbody")


# --- widget parsing ----------------------------------------------------------

PAGE = """
<html><head><meta property="og:title" content="Chan &amp; Title"></head><body>
<section><div class="tgme_channel_info">…</div>
<div class="tgme_widget_message text_not_supported_wrap js-widget_message"
     data-post="mychan/12">
  <a class="tgme_widget_message_photo_wrap grouped_media_wrap js-message_photo"
     style="width:225px;background-image:url('https://cdn4.telesco.pe/file/one.jpg')"></a>
  <a class="tgme_widget_message_photo_wrap grouped_media_wrap js-message_photo"
     style="background-image:url('https://cdn4.telesco.pe/file/two.jpg')"></a>
  <div class="tgme_widget_message_text js-message_text" dir="auto">Album caption</div>
  <a class="tgme_widget_message_date" href="https://t.me/mychan/12">
    <time datetime="2026-06-30T10:00:00+00:00">10:00</time></a>
</div>
<div class="tgme_widget_message js-widget_message" data-post="mychan/15">
  <a class="tgme_widget_message_video_player js-message_video_player" href="x">
    <i class="tgme_widget_message_video_thumb" style="background-image:url('t')"></i>
    <div class="tgme_widget_message_video_wrap">
      <video src="https://cdn1.telesco.pe/file/vid.mp4?token=abc" muted></video>
    </div></a>
  <a class="tgme_widget_message_date" href="https://t.me/mychan/15">
    <time datetime="2026-06-30T11:00:00+00:00">11:00</time></a>
</div>
<div class="tgme_widget_message js-widget_message" data-post="mychan/14">
  <a class="tgme_widget_message_video_player not_supported js-message_video_player"
     href="https://t.me/mychan/14">
    <i class="tgme_widget_message_video_thumb" style="background-image:url('t')"></i>
  </a>
  <div class="tgme_widget_message_link_preview">
    <a class="tgme_widget_message_photo_wrap"
       style="background-image:url('https://cdn.telesco.pe/preview-card.jpg')"></a>
    <div class="tgme_widget_message_text">preview card text, not the post</div>
  </div>
</div>
</section></body></html>
"""

title, widgets, exists = mod.parse_channel_page(PAGE)
check("page title", title, "Chan & Title")
check_true("page exists", exists)
check("widgets sorted ascending", [w["id"] for w in widgets], [12, 14, 15])

album = widgets[0]
check("album: two photos", album["media"],
      [("image", "https://cdn4.telesco.pe/file/one.jpg"),
       ("image", "https://cdn4.telesco.pe/file/two.jpg")])
check("album: date", album["date"], "2026-06-30T10:00:00+00:00")
check("album: caption", mod.widget_caption(album["text_el"]), "Album caption")

video = widgets[2]
check("video: src captured",
      video["media"], [("video", "https://cdn1.telesco.pe/file/vid.mp4?token=abc")])

big = widgets[1]
check("oversized video: src is None (placeholder link downstream)",
      big["media"], [("video", None)])
check_true("link-preview media/text not counted as post content",
           big["text_el"] is None and len(big["media"]) == 1,
           repr((big["text_el"], big["media"])))

_, none_widgets, none_exists = mod.parse_channel_page(
    "<html><body><div>nothing here</div></body></html>")
check_true("error page: no widgets, exists=False",
           none_widgets == [] and not none_exists)


# --- --selfhost gating -------------------------------------------------------

LINK = "https://t.me/mychan/12"

# Without --selfhost media is never fetched: the element is a link to the post,
# even when a store (asset_dir) is available. src is a poisoned URL on purpose —
# any download attempt would blow up instead of silently passing.
check("no --selfhost: image -> open-in-Telegram link",
      mod.web_media_element("image", "http://invalid.invalid/x.jpg", "tg/mychan/12",
                            0, "/nonexistent-store", 0, LINK),
      '<p><em>[image — <a href="%s">open in Telegram</a>]</em></p>' % LINK)
check("no --selfhost: oversized video keeps its dedicated placeholder",
      mod.web_media_element("video", None, "tg/mychan/12", 0, "/nonexistent-store",
                            0, LINK),
      '<p><em>[video too large for the public preview — '
      '<a href="%s">open in Telegram</a>]</em></p>' % LINK)

# With --selfhost but no store (srr preview), placeholders only — no download.
mod.SELFHOST = True
check("--selfhost without a store: bare placeholder",
      mod.web_media_element("image", "http://invalid.invalid/x.jpg", "tg/mychan/12",
                            0, "", 0, LINK),
      "<p><em>[image]</em></p>")
mod.SELFHOST = False


# --- --selfhost downloads: cache reuse + degrade paths -------------------------

STORE = tempfile.mkdtemp(prefix="srr-tg-web-test-")
mod.SELFHOST = True

# Cache hit: the stored file is reused without any request, and its mtime is
# refreshed — srr's age-based cache sweep must never delete a file a live feed
# still consumes (doubly so here: the preview CDN's signed URLs expire, so a
# swept file may be unrecoverable).
dest = os.path.join(STORE, "tg", "mychan", "12.jpg")
os.makedirs(os.path.dirname(dest))
with open(dest, "wb") as fh:
    fh.write(b"CACHED")
os.utime(dest, (1, 1))


def must_not_download(req_obj, timeout=None):
    raise AssertionError("download attempted — valid cache must be reused")


mod.urllib.request.urlopen = must_not_download
check("selfhost cache hit -> marker, no download",
      mod.web_media_element("image", "https://cdn.example/x.jpg", "tg/mychan/12",
                            0, STORE, 0, LINK),
      '<p><img src="#/tg/mychan/12.jpg" alt=""></p>')
check_true("selfhost cache hit refreshes mtime", os.path.getmtime(dest) > 1,
           "mtime=%r" % os.path.getmtime(dest))


# Download error: the item publishes and the cursor advances either way, so a
# silently dropped element is gone for good — degrade to the same
# open-in-Telegram link the no-download mode emits.
def raise_urlerror(req_obj, timeout=None):
    raise urllib.error.URLError("connection reset")


mod.urllib.request.urlopen = raise_urlerror
check("selfhost download error -> open-in-Telegram placeholder, not silence",
      mod.web_media_element("image", "https://cdn.example/gone.jpg", "tg/mychan/13",
                            0, STORE, 0, LINK),
      '<p><em>[image — <a href="%s">open in Telegram</a>]</em></p>' % LINK)


# A body shorter than its Content-Length is a truncated download — 'error',
# nothing lands at dest (same discipline as the account mode's .part guard).
class LyingResp:
    headers = {"Content-Length": "100"}

    def __init__(self):
        self._done = False

    def read(self, n=-1):
        if self._done:
            return b""
        self._done = True
        return b"HALF"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


mod.urllib.request.urlopen = lambda req_obj, timeout=None: LyingResp()
t_dest = os.path.join(STORE, "trunc.jpg")
check("web_download: body short of Content-Length -> error",
      mod.web_download("https://cdn.example/t.jpg", t_dest, 0), "error")
check_true("web_download: truncation leaves no file, no .part",
           not os.path.exists(t_dest) and not os.path.exists(t_dest + ".part"))


# A server closing early raises http.client.IncompleteRead (an HTTPException,
# NOT an OSError) mid-read — it must degrade, not blow up the cycle.
class IncompleteResp:
    headers = {"Content-Length": "100"}

    def read(self, n=-1):
        raise http.client.IncompleteRead(b"x")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


mod.urllib.request.urlopen = lambda req_obj, timeout=None: IncompleteResp()
try:
    got = mod.web_download("https://cdn.example/i.jpg",
                           os.path.join(STORE, "inc.jpg"), 0)
except Exception as e:
    got = "raised: %r" % (e,)
check("web_download: truncated stream (IncompleteRead) -> error, not a traceback",
      got, "error")

mod.SELFHOST = False
shutil.rmtree(STORE, ignore_errors=True)


# --- misc helpers ------------------------------------------------------------

check("_url_ext keeps extension",
      mod._url_ext("https://c.co/v.mp4?token=zz", ".bin"), ".mp4")
check("_url_ext falls back", mod._url_ext("https://c.co/file/abcdef", ".jpg"), ".jpg")

# guid keyspaces: account mode hashes "<numeric>:<id>", web mode
# "<username>:<id>" — usernames can't be all-digits, so never equal.
check_true("guid keyspace disjoint",
           mod.fnv1a32("1103848749:14444") != mod.fnv1a32("mychan:14444"))

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
