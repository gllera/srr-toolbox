"""Unit tests for srr-telegram's text_to_html / linkify.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_telegram_text.py
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
text_to_html = mod.text_to_html

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


# A blank line separates paragraphs -> distinct <p>, never <br><br>.
check("blank line -> two paragraphs", text_to_html("a\n\nb"), "<p>a</p><p>b</p>")

# A lone newline is a soft break inside one paragraph -> single <br>.
check("single newline -> one <br>", text_to_html("a\nb"), "<p>a<br>b</p>")

# Three+ newlines still mean one paragraph break, no empty <p>, no <br>.
check("triple newline -> two paragraphs", text_to_html("a\n\n\nb"), "<p>a</p><p>b</p>")

# Trailing spaces around a blank line must not leak into the markup.
check("blank line w/ trailing spaces", text_to_html("a.  \n\nb"), "<p>a.</p><p>b</p>")

# URLs are linkified and escaped; the real-world shape from t.me/elliberal.
real = text_to_html(
    "Buenos días:\n\nLa directora siguió.\n\n"
    "https://theobjective.com/x/y/")
check(
    "real telegram message -> clean paragraphs + link",
    real,
    "<p>Buenos días:</p><p>La directora siguió.</p>"
    '<p><a href="https://theobjective.com/x/y/">'
    "https://theobjective.com/x/y/</a></p>",
)

# HTML metacharacters in plain text are escaped.
check("escaping", text_to_html("a < b & c"), "<p>a &lt; b &amp; c</p>")

check("empty -> empty", text_to_html(""), "")
check("only whitespace -> empty", text_to_html("\n\n  \n"), "")

# The invariant the bug report is about: output never has duplicated <br>.
samples = [
    "a\n\nb", "x\n\n\n\ny", "one\ntwo\n\nthree", "line  \n\n  next",
    "Buenos días:\n\nNoticia.\n\nhttps://e.com/a",
]
for s in samples:
    out = text_to_html(s)
    check_true("no <br><br> in %r" % s, "<br><br>" not in out, out)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
