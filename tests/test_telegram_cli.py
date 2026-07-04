"""Unit tests for srr-telegram's CLI request building (parameters instead of
the stdin JSON that srrb normally sends).

Run with the script's deps available:
    uv run --with "telethon>=1.43,<2" --with cryptg python tests/test_telegram_cli.py
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


def check_exit(name, argv):
    try:
        mod.request_from_argv(argv)
    except SystemExit as e:
        print("PASS", name)
        return str(e)
    print("FAIL", name)
    print("    expected SystemExit for", argv)
    failures.append(name)


# The request built from parameters must match what srrb sends on stdin.
check("url only", mod.request_from_argv(["https://t.me/s/durov"]),
      {"url": "https://t.me/s/durov"})
check("all params",
      mod.request_from_argv(["https://t.me/s/durov", "--etag", "517",
                             "--asset-dir", "/tmp/store",
                             "--max-asset-size", "1000000"]),
      {"url": "https://t.me/s/durov", "etag": "517", "asset_dir": "/tmp/store",
       "max_asset_size": 1000000})
check("params may precede the url",
      mod.request_from_argv(["--etag", "9", "@durov"]),
      {"url": "@durov", "etag": "9"})

# Bare numeric channel ids (account mode) start with "-" but are channels.
check("bare numeric id is a URL, not an option",
      mod.request_from_argv(["-1001234567890"]), {"url": "-1001234567890"})

check_exit("unknown option rejected", ["https://t.me/s/durov", "--frobnicate"])
check_exit("missing value rejected", ["https://t.me/s/durov", "--etag"])
check_exit("second URL rejected", ["@durov", "@telegram"])
check_exit("missing URL rejected", ["--etag", "5"])
check_exit("non-numeric --max-asset-size rejected",
           ["@durov", "--max-asset-size", "big"])

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
