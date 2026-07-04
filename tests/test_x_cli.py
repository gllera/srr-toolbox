"""Unit tests for srr-x's CLI request building: mode-flag extraction
(--selfhost / --no-retweets / --instance), username parsing out of every
accepted subscription form, and the parameters-instead-of-stdin manual mode.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_x_cli.py
"""
import importlib.machinery
import importlib.util
import os

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


def check_exit(name, fn, *args):
    try:
        fn(*args)
    except SystemExit:
        print("PASS", name)
        return
    print("FAIL", name)
    print("    expected SystemExit for", args)
    failures.append(name)


def reset_modes():
    mod.SELFHOST = False
    mod.NO_RETWEETS = False
    mod.NO_REPLIES = False
    mod.INSTANCE = "https://nitter.net"


ACCOUNT = "https://x.com/NASA"

# --- mode defaults ------------------------------------------------------------

check_true("media download is opt-in (--selfhost)", mod.SELFHOST is False)
check_true("retweets are kept by default", mod.NO_RETWEETS is False)
check_true("replies are kept by default", mod.NO_REPLIES is False)
check("default instance", mod.INSTANCE, "https://nitter.net")

# --- mode-flag extraction -----------------------------------------------------

reset_modes()
check("mode flags are stripped from argv",
      mod.strip_mode_flags(["--selfhost", "--no-retweets", "--no-replies",
                            "--instance", "https://nitter.example/", ACCOUNT]),
      [ACCOUNT])
check_true("--selfhost sets the mode", mod.SELFHOST is True)
check_true("--no-retweets sets the mode", mod.NO_RETWEETS is True)
check_true("--no-replies sets the mode", mod.NO_REPLIES is True)
check("--instance overrides (trailing slash stripped)",
      mod.INSTANCE, "https://nitter.example")

reset_modes()
check("no mode flags: argv untouched, stdin mode possible",
      mod.strip_mode_flags([]), [])
check_exit("--instance without a value rejected",
           mod.strip_mode_flags, ["--instance"])

# --- username extraction ------------------------------------------------------

reset_modes()
for raw, want in [
    ("https://x.com/NASA", "NASA"),
    ("https://twitter.com/NASA/", "NASA"),
    ("https://x.com/NASA?s=21", "NASA"),
    ("https://x.com/@NASA", "NASA"),
    ("https://nitter.net/NASA", "NASA"),
    ("https://nitter.net/NASA/rss", "NASA"),
    ("x.com/NASA", "NASA"),
    ("@NASA", "NASA"),
    ("NASA", "NASA"),
]:
    check("username from %r" % raw, mod.username_from_url(raw), want)

check_exit("account URL without a username rejected",
           mod.username_from_url, "https://x.com/")
check_exit("empty account rejected", mod.username_from_url, "")
check_exit("username with invalid chars rejected",
           mod.username_from_url, "not a user!")
check_exit("over-long username rejected", mod.username_from_url, "a" * 16)

# --- feed URL -----------------------------------------------------------------

reset_modes()
check("feed URL on the default instance",
      mod.feed_url(ACCOUNT), "https://nitter.net/NASA/rss")
mod.INSTANCE = "https://nitter.example"
check("feed URL follows --instance",
      mod.feed_url("@NASA"), "https://nitter.example/NASA/rss")
reset_modes()

# --- manual-run request building (must match srrb's stdin JSON) ----------------

check("account only", mod.request_from_argv([ACCOUNT]), {"url": ACCOUNT})
check("all params",
      mod.request_from_argv([ACCOUNT, "--etag", 'W/"abc"',
                             "--last-modified", "Wed, 01 Jul 2026 10:00:00 GMT",
                             "--asset-dir", "/tmp/store",
                             "--max-asset-size", "1000000"]),
      {"url": ACCOUNT, "etag": 'W/"abc"',
       "last_modified": "Wed, 01 Jul 2026 10:00:00 GMT",
       "asset_dir": "/tmp/store", "max_asset_size": 1000000})
check("params may precede the account",
      mod.request_from_argv(["--etag", "x", ACCOUNT]),
      {"url": ACCOUNT, "etag": "x"})

check_exit("unknown option rejected",
           mod.request_from_argv, [ACCOUNT, "--frobnicate"])
check_exit("single-dash option rejected", mod.request_from_argv, [ACCOUNT, "-x"])
check_exit("missing value rejected", mod.request_from_argv, [ACCOUNT, "--etag"])
check_exit("second account rejected", mod.request_from_argv, [ACCOUNT, ACCOUNT])
check_exit("missing account rejected", mod.request_from_argv, ["--etag", "5"])
check_exit("non-numeric --max-asset-size rejected",
           mod.request_from_argv, [ACCOUNT, "--max-asset-size", "big"])

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
