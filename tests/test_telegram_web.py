"""Unit tests for srr-telegram's web (no-account) mode: URL/username parsing,
rich-text cleaning, and t.me/s widget parsing.

Run with the script's deps available:
    uv run --with "telethon>=1.43,<2" --with cryptg --with beautifulsoup4 \
        python tests/test_telegram_web.py
"""
import importlib.machinery
import importlib.util
import os

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
