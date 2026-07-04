"""Unit tests for srr-youtube's CLI request building (parameters instead of
the stdin JSON that srrb normally sends).

Run in the repo's uv project venv (plain python3 works too — the script
under test has no third-party deps):
    uv run tests/test_youtube_cli.py
"""
import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-youtube")

loader = importlib.machinery.SourceFileLoader("srr_youtube", SCRIPT)
spec = importlib.util.spec_from_loader("srr_youtube", loader)
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


def check_exit(name, argv):
    try:
        mod.request_from_argv(argv)
    except SystemExit as e:
        print("PASS", name)
        return str(e)
    print("FAIL", name)
    print("    expected SystemExit for", argv)
    failures.append(name)


FEED = "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"

check_true("thumbnail download is opt-in (--selfhost)", mod.SELFHOST is False)

# The request built from parameters must match what srrb sends on stdin.
check("url only", mod.request_from_argv([FEED]), {"url": FEED})
check("all params",
      mod.request_from_argv([FEED, "--etag", 'W/"abc"',
                             "--last-modified", "Wed, 01 Jul 2026 10:00:00 GMT",
                             "--asset-dir", "/tmp/store",
                             "--max-asset-size", "1000000"]),
      {"url": FEED, "etag": 'W/"abc"',
       "last_modified": "Wed, 01 Jul 2026 10:00:00 GMT",
       "asset_dir": "/tmp/store", "max_asset_size": 1000000})
check("params may precede the url",
      mod.request_from_argv(["--etag", "x", FEED]),
      {"url": FEED, "etag": "x"})

check_exit("unknown option rejected", [FEED, "--frobnicate"])
check_exit("single-dash option rejected", [FEED, "-x"])
check_exit("missing value rejected", [FEED, "--etag"])
check_exit("second URL rejected", [FEED, FEED])
check_exit("missing URL rejected", ["--etag", "5"])
check_exit("non-numeric --max-asset-size rejected",
           [FEED, "--max-asset-size", "big"])

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
