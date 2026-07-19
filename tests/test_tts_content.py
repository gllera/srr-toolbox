"""Unit tests for srr-tts's pure helpers: extract_text / truncate / audio_name.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_tts_content.py
"""
import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-tts")

loader = importlib.machinery.SourceFileLoader("srr_tts", SCRIPT)
spec = importlib.util.spec_from_loader("srr_tts", loader)
tts = importlib.util.module_from_spec(spec)
loader.exec_module(tts)

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


# --- extract_text ---------------------------------------------------------
check("title + paragraphs",
      tts.extract_text("Headline", "<p>One.</p><p>Two.</p>"),
      "Headline\n\nOne.\n\nTwo.")

check("inline tags flow into the sentence",
      tts.extract_text("", '<p>See <a href="https://e.com/x">the report</a> now.</p>'),
      "See the report now.")

check("script/style never narrated",
      tts.extract_text("", "<style>p{}</style><p>Body.</p><script>x()</script>"),
      "Body.")

check("br is a line break",
      tts.extract_text("", "<p>a<br>b</p>"),
      "a\nb")

check_true("attribute text (alt/src) never narrated",
           "poster" not in tts.extract_text(
               "", '<p><img src="x.jpg" alt="poster"> Caption text.</p>'),
           )

check("entities decoded", tts.extract_text("", "<p>a &amp; b</p>"), "a & b")

check("audio/video subtrees dropped (idempotence guard)",
      tts.extract_text("", '<p><audio src="#/tts/x.wav"></audio></p><p>Hi.</p>'),
      "Hi.")

check("empty content -> empty", tts.extract_text("", "<p>  </p>"), "")
check("title only", tts.extract_text("Just a title", ""), "Just a title")

# A feed title is NOT plain text, and srr only unescapes/sanitizes it after
# the pipeline — so this step sees the raw form. Narrated verbatim, piper
# reads the entity aloud ("ampersand a m p semicolon").
check("title entities decoded",
      tts.extract_text("Tom &amp; Jerry", "<p>Body.</p>"), "Tom & Jerry\n\nBody.")
check("title numeric entities decoded",
      tts.extract_text("It&#8217;s here", ""), "It’s here")
check("title markup stripped",
      tts.extract_text("<b>Breaking</b>: news", ""), "Breaking: news")
check("title collapses to one line",
      tts.extract_text("<p>Two</p><p>parts</p>", "<p>Body.</p>"),
      "Two parts\n\nBody.")
check("title drop-tags dropped",
      tts.extract_text("Real<script>evil()</script>", ""), "Real")

check("table cells separated",
      tts.extract_text("", "<table><tr><td>Total</td><td>5</td></tr></table>"),
      "Total\n\n5")

check("definition list separated",
      tts.extract_text("", "<dl><dt>Term</dt><dd>Definition.</dd></dl>"),
      "Term\n\nDefinition.")

# --- truncate -------------------------------------------------------------
check("under cap untouched", tts.truncate("Short. Text.", 100), ("Short. Text.", False))

long = ("One sentence here. " * 10).strip()          # 189 chars
cut, flagged = tts.truncate(long, 100)
check_true("over cap flagged", flagged)
check_true("cut at a sentence boundary", cut.endswith("."), repr(cut))
check_true("cut within cap", len(cut) <= 100, len(cut))

nosent = "x" * 200
cut, flagged = tts.truncate(nosent, 100)
check("no boundary -> hard cut at cap", (len(cut), flagged), (100, True))

cut, flagged = tts.truncate("Hi. " + "x" * 200, 100)
check("early boundary -> hard cut, not premature cut", (len(cut), flagged), (100, True))

# --- audio_name -----------------------------------------------------------
a = tts.audio_name("en_US-lessac-medium", "hello world")
check("stable name", a, tts.audio_name("en_US-lessac-medium", "hello world"))
check_true("tts/ prefix + .wav", a.startswith("tts/") and a.endswith(".wav"), a)
check_true("voice changes the name",
           a != tts.audio_name("es_ES-davefx-medium", "hello world"))
check_true("text changes the name",
           a != tts.audio_name("en_US-lessac-medium", "hello world!"))

# --- fuzz: the invariants above, brute-forced over random markup ------------
# extract_text feeds a text-to-speech engine, so a leak is audible: attribute
# text (alt=, href=) and raw markup/entities must never reach the narration,
# and truncate must respect its cap for any input.
import random

FRAGMENTS = ['<p>', '</p>', '<div>', '</div>', '<script>x</script>', '</audio>',
             '<audio src="#/a.wav">', '<br>', '<img alt="LEAK">', '&amp;',
             '<table><tr><td>', '</td></tr></table>', '&#x1F600;', '<h1>',
             '<svg><style>a{}', 'Hola. ', 'Text! ', '...', '\x00', '</h1>',
             '<!-- c -->', '<a href="http://e">link</a>', '<b>', '</b>',
             '<pre>', '</pre>', '\n', '\t', '<template>', '</template>',
             '<!DOCTYPE html>', '<html><head>']

random.seed(11)
fuzz_bad = []
for _ in range(3000):
    html = "".join(random.choice(FRAGMENTS) for _ in range(random.randint(0, 25)))
    title = "".join(random.choice(FRAGMENTS) for _ in range(random.randint(0, 3)))
    try:
        text = tts.extract_text(title, html)
        if "LEAK" in text:
            fuzz_bad.append(("attribute text narrated", title, html))
        elif "<" in text or "&amp;" in text:
            fuzz_bad.append(("markup narrated", title, html))
        for cap in (0, 1, 7, 100, 3000):
            out, flagged = tts.truncate(text, cap)
            if cap > 0 and len(out) > cap:
                fuzz_bad.append(("truncate exceeded cap", cap, text))
            if flagged != (cap > 0 and len(text) > cap):
                fuzz_bad.append(("truncate mis-flagged", cap, text))
    except Exception as e:                                   # noqa: BLE001
        fuzz_bad.append((title, html, repr(e)))
check("fuzz: no leak, no crash, cap respected (3000 documents)",
      fuzz_bad[:3], [])

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
