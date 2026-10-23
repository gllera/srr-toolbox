"""Unit tests for srr-devto: state, URL check, listing URL, selection, item.

Run: python3 tests/test_devto_select.py
"""
import importlib.machinery
import importlib.util
import os
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-devto")

loader = importlib.machinery.SourceFileLoader("srr_devto", SCRIPT)
spec = importlib.util.spec_from_loader("srr_devto", loader)
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


TODAY = 20000

check("state: empty etag", mod.parse_state("", TODAY), {})
check("state: pruned",
      mod.parse_state(mod.encode_state({5: TODAY, 6: TODAY - 40}), TODAY), {5: TODAY})
check("state: kept at the edge", mod.parse_state(mod.encode_state({7: TODAY - 31}), TODAY),
      {7: TODAY - 31})
check("state: another strategy's watermark", mod.parse_state("2026-10-01T00:00:00Z", TODAY), {})
check("state: garbage", mod.parse_state("{bad", TODAY), {})
check("state: wrong shape", mod.parse_state('{"v":2,"sent":{}}', TODAY), {})
check("state: wrong shape, list", mod.parse_state("[1]", TODAY), {})
check("encode: sorted, compact", mod.encode_state({10: 3, 2: 4}), '{"sent":{"10":3,"2":4},"v":1}')

for u in ("https://dev.to", "https://dev.to/"):
    try:
        mod.check_url(u)
        check("url ok " + u, True, True)
    except ValueError as e:
        check("url ok " + u, str(e), "")
for u in ("https://dev.to/t/python", "https://example.com/"):
    try:
        mod.check_url(u)
        check("url refused " + u, "accepted", "refused")
    except ValueError as e:
        check("url refused " + u, str(e), f"the feed URL must be https://dev.to/ (got {u})")

check("list_url", mod.list_url(500), "https://dev.to/api/articles?top=30&per_page=500")


def art(i, lang="en", **kw):
    d = {"id": i, "title": f"T{i}", "url": f"https://dev.to/x/{i}", "language": lang}
    d.update(kw)
    return d


arts = [art(3), art(1, "es"), art(2, None), art(4), {"id": 5, "title": "t", "url": "u"}, art(6)]
check("select: all, in order", [a["id"] for a in mod.select(arts, {}, None)], [3, 1, 2, 4, 5, 6])
check("select: sent skipped", [a["id"] for a in mod.select(arts, {3: 1, 6: 1}, None)], [1, 2, 4, 5])
check("select: lang en", [a["id"] for a in mod.select(arts, {}, {"en"})], [3, 4, 6])
check("select: lang en+es", [a["id"] for a in mod.select(arts, {}, {"en", "es"})], [3, 1, 4, 6])

it = mod.build_item({"id": 42, "title": "Hello", "url": "https://dev.to/a/hello",
                     "cover_image": "https://example.com/c.png?a=1&b=2",
                     "body_html": "<p>Body</p>", "language": "en",
                     "published_at": "2026-10-01T00:00:00Z"})
check("item: guid", it["guid"], mod.fnv1a32("dev.to/42"))
check("item: link", it["link"], "https://dev.to/a/hello")
check("item: title", it["title"], "Hello")
check("item: published null", it["published"], None)
check("item: lang", it["lang"], "en")
check("item: content", it["content"],
      '<p><img src="https://example.com/c.png?a=1&amp;b=2" alt=""></p><p>Body</p>')
it = mod.build_item({"id": 1, "title": "x", "url": "u", "cover_image": None, "body_html": "<p>B</p>"})
check("item: no cover", it["content"], "<p>B</p>")
check_true("item: no lang key", "lang" not in it, str(it))

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
