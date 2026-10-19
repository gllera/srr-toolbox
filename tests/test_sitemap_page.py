"""Unit tests for srr-sitemap's page side: head-metadata extraction, the item
it builds, and run_ingest's batch semantics — the watermark, a 404 skipped
for good, another failure stopping the batch at the last success, and a run
that emits nothing because of one failing loudly.

Run in the repo's uv project venv (plain python3 works too — the script
under test has no third-party deps):
    uv run tests/test_sitemap_page.py
"""
import importlib.machinery
import importlib.util
import os
import urllib.error
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


LOC = "https://example.com/posts/a-fine-post-2036028/"
LASTMOD = utc(2026, 10, 18, 23, 40, 1)

FULL = """<!doctype html><html><head>
<meta charset="utf-8">
<title>A fine post &middot; Example Site</title>
<meta name="description" content="Meta  description,
 folded.">
<meta property="og:title" content="A fine post">
<meta property="og:description" content="The &quot;og&quot; description.">
<meta property="og:site_name" content="Example Site">
<meta property="og:image" content="https://cdn.example.com/a.jpg">
<meta property="og:image" content="https://cdn.example.com/second.jpg">
<meta property="article:published_time" content="2026-10-18T20:07:25.000Z">
<script type="application/ld+json">{"@type":"BlogPosting","datePublished":"2026-01-01T00:00:00Z"}</script>
</head><body><h1>Body headline</h1><p>text</p></body></html>"""

LD_ONLY = """<html><head><title>Headline | Example Site</title>
<meta property="og:site_name" content="Example Site">
<script type="application/ld+json">{"@graph":[{"@type":"Organization"},
 {"@type":"BlogPosting","datePublished":"2026-10-18T20:07:25+02:00"}]}</script>
</head><body><h1>Only an h1</h1></body></html>"""

BARE = b"<html><body><p>nothing to see</p></body></html>"

# ---- parse_page

p = mod.parse_page(FULL.encode(), LOC, LASTMOD)
check("og:title wins", p["title"], "A fine post")
check("article:published_time wins over JSON-LD", p["published"], utc(2026, 10, 18, 20, 7, 25))
check("first og:image wins", p["image"], "https://cdn.example.com/a.jpg")
check("og:description wins, entities decoded", p["description"], 'The "og" description.')
check("site name", p["site"], "Example Site")

p = mod.parse_page(LD_ONLY.encode(), LOC, LASTMOD)
check("h1 beats <title>", p["title"], "Only an h1")
check("JSON-LD datePublished inside @graph, normalised to UTC",
      p["published"], utc(2026, 10, 18, 18, 7, 25))
check("no image", p["image"], "")
check("no description", p["description"], "")

p = mod.parse_page(b"<html><head><title>Headline | Example Site</title>"
                   b'<meta property="og:site_name" content="Example Site"></head></html>',
                   LOC, LASTMOD)
check("<title> with the site name suffix stripped", p["title"], "Headline")

p = mod.parse_page(BARE, LOC, LASTMOD)
check("slug title fallback drops the trailing id", p["title"], "A fine post")
check("lastmod is the published fallback", p["published"], LASTMOD)
check("slug title: file extension dropped",
      mod.slug_title("https://example.com/blog/hello-world.html"), "Hello world")
check("slug title: bare host", mod.slug_title("https://example.com/"), "Example")

p = mod.parse_page(b'<html><head><meta property="og:image" content="/relative.jpg"></head></html>',
                   LOC, LASTMOD)
check("relative og:image ignored", p["image"], "")

# ---- build_item

item = mod.build_item(LOC, mod.parse_page(FULL.encode(), LOC, LASTMOD))
check("item guid is fnv1a32(loc)", item["guid"], mod.fnv1a32(LOC))
check("item link is the loc", item["link"], LOC)
check("item published RFC 3339", item["published"], "2026-10-18T20:07:25.000Z")
check("item content: linked cover + description",
      item["content"],
      '<p><a href="https://example.com/posts/a-fine-post-2036028/">'
      '<img src="https://cdn.example.com/a.jpg" alt="A fine post"></a></p>'
      '<p>The &quot;og&quot; description.</p>')
item = mod.build_item(LOC, mod.parse_page(BARE, LOC, LASTMOD))
check("item content with nothing to show: the link",
      item["content"], f'<p><a href="{LOC}">{LOC}</a></p>')
check("dateless item", mod.build_item(LOC, {"title": "t", "published": None, "image": "",
                                            "description": "", "site": ""})["published"], None)

# ---- run_ingest batch semantics, with fakes

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
SITEMAP = f"""<urlset {NS}>
<url><loc>https://example.com/posts/p1/</loc><lastmod>2026-10-18T01:00:00Z</lastmod></url>
<url><loc>https://example.com/posts/p2/</loc><lastmod>2026-10-18T02:00:00Z</lastmod></url>
<url><loc>https://example.com/posts/p3/</loc><lastmod>2026-10-18T03:00:00Z</lastmod></url>
<url><loc>https://example.com/posts/p4/</loc><lastmod>2026-10-18T04:00:00Z</lastmod></url>
</urlset>"""
PAGE_STATUS = {}
FETCHED = []


def fake_fetch(url, cap):
    FETCHED.append(url)
    if url.endswith("sitemap.xml"):
        return SITEMAP.encode(), url
    st = PAGE_STATUS.get(url, 200)
    if st != 200:
        raise urllib.error.HTTPError(url, st, "err", {}, None)
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return (f'<html><head><meta property="og:title" content="Post {name}">'
            f'<meta property="og:site_name" content="Example Site"></head></html>').encode(), url


mod.fetch_bytes = fake_fetch
mod.OPTS.update({"match": None, "sitemap_match": None, "limit": 30, "since": 36500,
                 "ua": mod.DEFAULT_UA})
REQ = {"url": "https://example.com/sitemap.xml", "etag": "", "max_size": 5_000_000}

out = mod.run_ingest(dict(REQ))
check("first run: every page, oldest first", [i["title"] for i in out["items"]],
      ["Post p1", "Post p2", "Post p3", "Post p4"])
check("first run: watermark is the newest lastmod", out["etag"], "2026-10-18T04:00:00.000Z")
check("first run: site title", out.get("title"), "Example Site")
check("pages fetched oldest first after the sitemap",
      FETCHED, ["https://example.com/sitemap.xml", "https://example.com/posts/p1/",
                "https://example.com/posts/p2/", "https://example.com/posts/p3/",
                "https://example.com/posts/p4/"])

out = mod.run_ingest(dict(REQ, etag="2026-10-18T04:00:00.000Z"))
check("second run with nothing new: not_modified", out, {"not_modified": True})

out = mod.run_ingest(dict(REQ, etag="2026-10-18T02:00:00Z"))
check("watermark: only the newer pages", [i["title"] for i in out["items"]],
      ["Post p3", "Post p4"])

PAGE_STATUS.clear()
PAGE_STATUS["https://example.com/posts/p2/"] = 404
out = mod.run_ingest(dict(REQ))
check("404: skipped, batch continues", [i["title"] for i in out["items"]],
      ["Post p1", "Post p3", "Post p4"])
check("404: watermark passes the gone page", out["etag"], "2026-10-18T04:00:00.000Z")

PAGE_STATUS.clear()
PAGE_STATUS["https://example.com/posts/p3/"] = 503
FETCHED.clear()
out = mod.run_ingest(dict(REQ))
check("503: batch stops, earlier pages emitted", [i["title"] for i in out["items"]],
      ["Post p1", "Post p2"])
check("503: watermark stays at the last success", out["etag"], "2026-10-18T02:00:00.000Z")
check_true("503: later pages not fetched", "https://example.com/posts/p4/" not in FETCHED,
           str(FETCHED))

PAGE_STATUS.clear()
PAGE_STATUS["https://example.com/posts/p1/"] = 500
try:
    mod.run_ingest(dict(REQ))
    check_true("nothing emitted because of a failure: raises", False)
except mod.BatchStopped as e:
    check_true("nothing emitted because of a failure: raises", "HTTP 500" in str(e), str(e))

PAGE_STATUS.clear()
mod.OPTS["limit"] = 2
out = mod.run_ingest(dict(REQ))
check("limit: the newest N, still oldest first", [i["title"] for i in out["items"]],
      ["Post p3", "Post p4"])
check("limit: watermark is the newest emitted", out["etag"], "2026-10-18T04:00:00.000Z")
mod.OPTS["limit"] = 30

out = mod.run_ingest(dict(REQ, etag="not-a-date"))
check("junk etag: starts from scratch", len(out["items"]), 4)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
