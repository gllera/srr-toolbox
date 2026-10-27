"""Unit tests for srr-telegram's account-mode body rendering: message text plus
its entities (hidden links, formatting, blocks) and polls. Constructed Telethon
objects only; nothing here connects to Telegram.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_telegram_entities.py
"""
import datetime
import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-telegram")

loader = importlib.machinery.SourceFileLoader("srr_telegram", SCRIPT)
spec = importlib.util.spec_from_loader("srr_telegram", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
T = mod.types
from telethon.tl import custom

failures = []


def check(name, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        print("   want:", repr(want))
        print("   got :", repr(got))
        failures.append(name)


def render(text, *ents):
    return mod.message_to_html(text, list(ents))


# A link hidden behind text (MessageEntityTextUrl): the visible text stays, the
# URL becomes the href.
check("text url",
      render("Read the report now", T.MessageEntityTextUrl(9, 6, "https://example.com/r?a=1&b=2")),
      '<p>Read the <a href="https://example.com/r?a=1&amp;b=2">report</a> now</p>')

# A URL entity: the text is the link; no scheme gets https://.
check("url entity",
      render("see example.com/x ok", T.MessageEntityUrl(4, 13)),
      '<p>see <a href="https://example.com/x">example.com/x</a> ok</p>')
check("url entity with scheme",
      render("http://example.com/x", T.MessageEntityUrl(0, 20)),
      '<p><a href="http://example.com/x">http://example.com/x</a></p>')

# A bare URL no entity covers is still linkified (the old behaviour).
check("bare url, no entities", mod.message_to_html("go https://example.com/a", []),
      mod.text_to_html("go https://example.com/a"))
check("bare url, entities None", mod.message_to_html("a\n\nb", None), "<p>a</p><p>b</p>")

# Bold, italic, underline, strike, code.
check("bold italic",
      render("a bold and italic z", T.MessageEntityBold(2, 4), T.MessageEntityItalic(11, 6)),
      "<p>a <strong>bold</strong> and <em>italic</em> z</p>")
check("underline strike code",
      render("u s c", T.MessageEntityUnderline(0, 1), T.MessageEntityStrike(2, 1),
             T.MessageEntityCode(4, 1)),
      "<p><u>u</u> <del>s</del> <code>c</code></p>")

# Overlapping entities stay well nested.
check("overlap",
      render("abcd", T.MessageEntityBold(0, 3), T.MessageEntityItalic(2, 2)),
      "<p><strong>ab</strong><strong><em>c</em></strong><em>d</em></p>")

# Bold across a paragraph break: each paragraph carries its own tags.
check("bold over paragraph break",
      render("ab\n\ncd", T.MessageEntityBold(0, 6)),
      "<p><strong>ab</strong></p><p><strong>cd</strong></p>")
check("bold over soft break",
      render("ab\ncd", T.MessageEntityBold(0, 5)),
      "<p><strong>ab</strong><br><strong>cd</strong></p>")

# Bold link.
check("bold link",
      render("go here", T.MessageEntityBold(3, 4), T.MessageEntityTextUrl(3, 4, "https://example.com/")),
      '<p>go <a href="https://example.com/"><strong>here</strong></a></p>')

# Offsets are UTF-16 units: an emoji outside the BMP is two.
check("utf16 offsets",
      render("\U0001F600 bold", T.MessageEntityBold(3, 4)),
      "<p>\U0001F600 <strong>bold</strong></p>")

# Spoilers, custom emoji, mentions, hashtags, unsafe links drop to plain text.
check("spoiler plain", render("a secret b", T.MessageEntitySpoiler(2, 6)), "<p>a secret b</p>")
check("custom emoji plain", render("x \U0001F600 y", T.MessageEntityCustomEmoji(2, 2, 5)),
      "<p>x \U0001F600 y</p>")
check("mention name plain", render("hi Bob", T.MessageEntityMentionName(3, 3, 42)), "<p>hi Bob</p>")
check("hashtag plain", render("#tag", T.MessageEntityHashtag(0, 4)), "<p>#tag</p>")
check("javascript link dropped",
      render("click", T.MessageEntityTextUrl(0, 5, "javascript:alert(1)")), "<p>click</p>")
check("tg link dropped",
      render("click", T.MessageEntityTextUrl(0, 5, "tg://resolve?domain=x")), "<p>click</p>")
check("email", render("a@example.com", T.MessageEntityEmail(0, 13)),
      '<p><a href="mailto:a@example.com">a@example.com</a></p>')

# Blocks.
check("pre",
      render("before\n\nx = 1 < 2\ny = 3\n\nafter", T.MessageEntityPre(8, 15, "python")),
      "<p>before</p><pre><code>x = 1 &lt; 2\ny = 3</code></pre><p>after</p>")
check("blockquote",
      render("q1\nq2\n\nafter", T.MessageEntityBlockquote(0, 5), T.MessageEntityBold(0, 2)),
      "<blockquote><p><strong>q1</strong><br>q2</p></blockquote><p>after</p>")

# Escaping and degenerate input.
check("escaping", render("a < b & c", T.MessageEntityBold(0, 1)),
      "<p><strong>a</strong> &lt; b &amp; c</p>")
check("zero length ignored", render("abc", T.MessageEntityBold(1, 0)), "<p>abc</p>")
check("out of range clipped", render("abc", T.MessageEntityBold(1, 50)), "<p>a<strong>bc</strong></p>")
check("empty", render(""), "")

# --- polls ---------------------------------------------------------------------


def poll_msg(results=None, plain=False):
    def twe(s):
        return s if plain else T.TextWithEntities(s, [])
    poll = T.Poll(id=1, question=twe("Best colour?"),
                  answers=[T.PollAnswer(twe("Red"), b"0"), T.PollAnswer(twe("Blue & green"), b"1")],
                  hash=0)
    media = T.MessageMediaPoll(poll=poll, results=results or T.PollResults())
    return custom.Message(id=7, peer_id=T.PeerChannel(1), message="", media=media,
                              date=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc))


check("poll, no results", mod.poll_html(poll_msg()),
      "<p>Best colour?</p><ul><li>Red</li><li>Blue &amp; green</li></ul>")
check("poll, plain-str layer", mod.poll_html(poll_msg(plain=True)),
      "<p>Best colour?</p><ul><li>Red</li><li>Blue &amp; green</li></ul>")
res = T.PollResults(results=[T.PollAnswerVoters(b"0", voters=1), T.PollAnswerVoters(b"1", voters=2)],
                    total_voters=3)
check("poll, results", mod.poll_html(poll_msg(res)),
      "<p>Best colour?</p><ul><li>Red (33%)</li><li>Blue &amp; green (67%)</li></ul>")
res0 = T.PollResults(results=[T.PollAnswerVoters(b"0", voters=0), T.PollAnswerVoters(b"1", voters=0)],
                     total_voters=0)
check("poll, zero votes", mod.poll_html(poll_msg(res0)),
      "<p>Best colour?</p><ul><li>Red</li><li>Blue &amp; green</li></ul>")
check("not a poll", mod.poll_html(custom.Message(id=1, peer_id=T.PeerChannel(1), message="x")), "")

# A poll is not downloadable media; its title is still the caption path.
check("poll classify_media", mod.classify_media(poll_msg()), None)
check("make_title ignores entities", mod.make_title("Read the report now", "Chan", "2026-01-01T00:00:00"),
      "Read the report now")

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
