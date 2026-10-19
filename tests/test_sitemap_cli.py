"""Unit tests for srr-sitemap's CLI: the strategy's own options (--match,
--sitemap-match, --limit, --since, --ua) and the request built from the
remaining parameters (instead of the stdin JSON that srr normally sends).

Run in the repo's uv project venv (plain python3 works too — the script
under test has no third-party deps):
    uv run tests/test_sitemap_cli.py
"""
import importlib.machinery
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-sitemap")

loader = importlib.machinery.SourceFileLoader("srr_sitemap", SCRIPT)
spec = importlib.util.spec_from_loader("srr_sitemap", loader)
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


def check_exit(name, fn, argv):
    try:
        fn(argv)
    except SystemExit as e:
        print("PASS", name)
        return str(e)
    print("FAIL", name)
    print("    expected SystemExit for", argv)
    failures.append(name)


URL = "https://example.com/sitemap.xml"

check("defaults", dict(mod.OPTS), {"match": None, "sitemap_match": None, "limit": 30,
                                   "since": 7, "ua": mod.DEFAULT_UA})

rest = mod.parse_options(["--match", "/posts/", URL, "--limit", "5", "--since", "2",
                          "--sitemap-match", "posts", "--ua", "bot/1", "--etag", "x"])
check("options parsed into OPTS", dict(mod.OPTS),
      {"match": "/posts/", "sitemap_match": "posts", "limit": 5, "since": 2, "ua": "bot/1"})
check("the rest is left for the request", rest, [URL, "--etag", "x"])

# The request built from parameters must match what srr sends on stdin.
check("url only", mod.request_from_argv([URL]), {"url": URL})
check("all params",
      mod.request_from_argv([URL, "--etag", "2026-10-18T04:00:00.000Z",
                             "--last-modified", "x",
                             "--asset-dir", "/tmp/store",
                             "--max-asset-size", "1000000"]),
      {"url": URL, "etag": "2026-10-18T04:00:00.000Z", "last_modified": "x",
       "asset_dir": "/tmp/store", "max_asset_size": 1000000})
check("params may precede the url", mod.request_from_argv(["--etag", "x", URL]),
      {"url": URL, "etag": "x"})

check_exit("--limit must be a positive integer", mod.parse_options, [URL, "--limit", "0"])
check_exit("--limit non-numeric", mod.parse_options, [URL, "--limit", "many"])
check_exit("--since must be a positive integer", mod.parse_options, [URL, "--since", "-1"])
check_exit("--match must compile", mod.parse_options, [URL, "--match", "("])
check_exit("--ua must be non-empty", mod.parse_options, [URL, "--ua", ""])
check_exit("option missing its value", mod.parse_options, [URL, "--match"])
check_exit("unknown option rejected", mod.request_from_argv, [URL, "--frobnicate"])
check_exit("missing value rejected", mod.request_from_argv, [URL, "--etag"])
check_exit("second URL rejected", mod.request_from_argv, [URL, URL])
check_exit("missing URL rejected", mod.request_from_argv, ["--etag", "5"])
check_exit("non-numeric --max-asset-size rejected",
           mod.request_from_argv, [URL, "--max-asset-size", "big"])

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
