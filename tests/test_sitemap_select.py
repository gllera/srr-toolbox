"""Unit tests for srr-sitemap's listing side: sitemap / sitemap-index parsing,
lastmod parsing, --match / --sitemap-match, the --since horizon, --limit, the
watermark, not_modified, and gzip-encoded sitemaps.

Run in the repo's uv project venv (plain python3 works too — the script
under test has no third-party deps):
    uv run tests/test_sitemap_select.py
"""
import gzip
import importlib.machinery
import importlib.util
import os
from datetime import datetime, timezone

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


def check_true(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        print("   ", detail)
        failures.append(name)


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def reset_opts():
    mod.OPTS.update({"match": None, "sitemap_match": None, "limit": 30, "since": 7,
                     "ua": mod.DEFAULT_UA})


NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'

URLSET = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset {NS}>
  <url><loc>https://example.com/posts/newest-123/</loc><lastmod>2026-10-18T23:40:01.244Z</lastmod></url>
  <url><loc>https://example.com/posts/older-122/</loc><lastmod>2026-10-18T10:00:00+02:00</lastmod></url>
  <url><loc>https://example.com/pages/about/</loc><lastmod>2026-10-18T12:00:00Z</lastmod></url>
  <url><loc>https://example.com/posts/dateless/</loc></url>
  <url><loc>https://example.com/posts/ancient-1/</loc><lastmod>2016-10-31</lastmod></url>
  <url><loc>https://example.com/posts/older-122/</loc><lastmod>2026-10-17T10:00:00Z</lastmod></url>
</urlset>
"""

INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex {NS}>
  <sitemap><loc>https://example.com/sitemaps/posts-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemaps/pages-1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemaps/broken.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemaps/nested.xml</loc></sitemap>
</sitemapindex>
"""

PAGES = f"""<urlset {NS}><url><loc>https://example.com/pages/contact/</loc><lastmod>2026-10-18T13:00:00Z</lastmod></url></urlset>"""

NOW = utc(2026, 10, 19, 6, 0, 0)

# ---- lastmod / instant parsing

check("instant: Z with millis", mod.parse_instant("2026-10-18T23:40:01.244Z"),
      utc(2026, 10, 18, 23, 40, 1, 244000))
check("instant: offset normalised to UTC", mod.parse_instant("2026-10-18T10:00:00+02:00"),
      utc(2026, 10, 18, 8, 0, 0))
check("instant: bare date is midnight UTC", mod.parse_instant("2016-10-31"), utc(2016, 10, 31))
check("instant: naive is UTC", mod.parse_instant("2026-10-18T12:00:00"), utc(2026, 10, 18, 12))
check("instant: odd fraction width", mod.parse_instant("2026-10-18T12:00:00.1Z"),
      utc(2026, 10, 18, 12, 0, 0, 100000))
check("instant: garbage is None", mod.parse_instant("yesterday"), None)
check("instant: empty is None", mod.parse_instant(""), None)
check("rfc3339 round trip", mod.rfc3339(utc(2026, 10, 18, 23, 40, 1, 244000)),
      "2026-10-18T23:40:01.244Z")

# ---- parsing

kind, entries = mod.parse_sitemap(URLSET.encode())
check("urlset kind", kind, "urlset")
check("urlset entries (dateless kept as None, duplicates kept)",
      [(l.rsplit("/", 2)[1], d) for l, d in entries],
      [("newest-123", utc(2026, 10, 18, 23, 40, 1, 244000)),
       ("older-122", utc(2026, 10, 18, 8)),
       ("about", utc(2026, 10, 18, 12)),
       ("dateless", None),
       ("ancient-1", utc(2016, 10, 31)),
       ("older-122", utc(2026, 10, 17, 10))])
kind, children = mod.parse_sitemap(INDEX.encode())
check("index kind", kind, "index")
check("index children", children, ["https://example.com/sitemaps/posts-1.xml",
                                   "https://example.com/sitemaps/pages-1.xml",
                                   "https://example.com/sitemaps/broken.xml",
                                   "https://example.com/sitemaps/nested.xml"])
try:
    mod.parse_sitemap(b"<html><body>not a sitemap</body></html>")
    check_true("non-sitemap root rejected", False)
except ValueError as e:
    check_true("non-sitemap root rejected", "not a sitemap" in str(e), str(e))

# ---- selection

reset_opts()
sel = mod.select_entries(entries, None, NOW)
check("select: dated, in horizon, newest first then reversed to oldest-first, dedup by loc",
      [l.rsplit("/", 2)[1] for l, _ in sel], ["older-122", "about", "newest-123"])
check("select: duplicate loc keeps its newest lastmod", sel[0][1], utc(2026, 10, 18, 8))

mod.OPTS["match"] = "/posts/"
check("select: --match filters locs",
      [l.rsplit("/", 2)[1] for l, _ in mod.select_entries(entries, None, NOW)],
      ["older-122", "newest-123"])
mod.OPTS["limit"] = 1
check("select: --limit keeps the newest",
      [l.rsplit("/", 2)[1] for l, _ in mod.select_entries(entries, None, NOW)], ["newest-123"])
reset_opts()
mod.OPTS["since"] = 1
check("select: --since horizon (strictly newer than now - days)",
      [l.rsplit("/", 2)[1] for l, _ in mod.select_entries(entries, None, utc(2026, 10, 19, 13))],
      ["newest-123"])
reset_opts()
check("select: watermark is exclusive",
      [l.rsplit("/", 2)[1] for l, _ in mod.select_entries(entries, utc(2026, 10, 18, 12), NOW)],
      ["newest-123"])
check("select: nothing newer than the watermark",
      mod.select_entries(entries, utc(2026, 10, 18, 23, 40, 1, 244000), NOW), [])

# ---- collect_entries over an index, with a fake fetcher

reset_opts()
FETCHES = []


def fake_fetch(url, cap):
    FETCHES.append(url)
    body = {
        "https://example.com/sitemap.xml": INDEX,
        "https://example.com/sitemaps/posts-1.xml": URLSET,
        "https://example.com/sitemaps/pages-1.xml": PAGES,
        "https://example.com/sitemaps/nested.xml": INDEX,
    }.get(url)
    if body is None:
        raise OSError("connection refused")
    return body.encode(), url


real_fetch = mod.fetch_bytes
mod.fetch_bytes = fake_fetch
got = mod.collect_entries("https://example.com/sitemap.xml", 5_000_000)
check("index: every child read, broken skipped, nested index skipped",
      len(got), len(entries) + 1)
check("index: fetch order", FETCHES, ["https://example.com/sitemap.xml",
                                      "https://example.com/sitemaps/posts-1.xml",
                                      "https://example.com/sitemaps/pages-1.xml",
                                      "https://example.com/sitemaps/broken.xml",
                                      "https://example.com/sitemaps/nested.xml"])
FETCHES.clear()
mod.OPTS["sitemap_match"] = "posts"
got = mod.collect_entries("https://example.com/sitemap.xml", 5_000_000)
check("index: --sitemap-match reads only matching children",
      FETCHES, ["https://example.com/sitemap.xml", "https://example.com/sitemaps/posts-1.xml"])
check("index: matched child's entries", len(got), len(entries))
FETCHES.clear()
got = mod.collect_entries("https://example.com/sitemaps/posts-1.xml", 5_000_000)
check("plain urlset: one fetch", FETCHES, ["https://example.com/sitemaps/posts-1.xml"])
mod.fetch_bytes = real_fetch
reset_opts()

# ---- gzip decoding in fetch_bytes (through a fake urlopen)


class FakeResp:
    def __init__(self, data, headers):
        self._d, self.headers = data, headers

    def read(self, n=-1):
        return self._d if n < 0 else self._d[:n]

    def geturl(self):
        return "https://example.com/sitemap.xml.gz"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


SEEN_HEADERS = {}


def fake_urlopen(req, timeout=0):
    SEEN_HEADERS.update({k.lower(): v for k, v in req.header_items()})
    return FakeResp(gzip.compress(URLSET.encode()), {"Content-Encoding": "gzip"})


real_urlopen = mod.urllib.request.urlopen
mod.urllib.request.urlopen = fake_urlopen
body, final = mod.fetch_bytes("https://example.com/sitemap.xml.gz", 5_000_000)
check("gzip: Content-Encoding body decoded", body, URLSET.encode())
check("gzip: final url", final, "https://example.com/sitemap.xml.gz")
check_true("gzip: requested", SEEN_HEADERS.get("accept-encoding") == "gzip", str(SEEN_HEADERS))
check_true("ua: the default is sent", SEEN_HEADERS.get("user-agent") == mod.DEFAULT_UA,
           str(SEEN_HEADERS))


def fake_urlopen_magic(req, timeout=0):
    return FakeResp(gzip.compress(URLSET.encode()), {})


mod.urllib.request.urlopen = fake_urlopen_magic
body, _ = mod.fetch_bytes("https://example.com/sitemap.xml.gz", 5_000_000)
check("gzip: magic bytes decoded without a Content-Encoding", body, URLSET.encode())
packed = gzip.compress(URLSET.encode())
body, _ = mod.fetch_bytes("https://example.com/sitemap.xml.gz", len(packed))
check_true("gzip: cap applies to the decoded bytes too",
           len(body) == len(packed) < len(URLSET), f"{len(body)} {len(packed)} {len(URLSET)}")
mod.urllib.request.urlopen = real_urlopen

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
