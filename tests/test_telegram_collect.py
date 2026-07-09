"""Unit tests for srr-telegram's batch collectors: web-mode pagination
(?after forward paging, ?before backfill, overlap dedup, termination) and
account-mode batching (group_messages album grouping, collect_messages
bounds and filtering).

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_telegram_collect.py
"""
import asyncio
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


def check_true(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        print("   ", detail)
        failures.append(name)


# --- web-mode pagination -------------------------------------------------------
#
# collect_web_widgets is driven through a fake web_get serving canned t.me/s
# pages; a page of WEB_PAGE_SIZE widgets is "full" (there may be more), a
# shorter one is the last.

def page(ids):
    posts = "".join(
        '<div class="tgme_widget_message" data-post="chan/%d">'
        '<div class="tgme_widget_message_text js-message_text">m%d</div>'
        '<a class="tgme_widget_message_date" href="https://t.me/chan/%d">'
        '<time datetime="2026-06-30T10:00:00+00:00">t</time></a></div>'
        % (i, i, i) for i in ids)
    return ('<html><head><meta property="og:title" content="Chan"></head><body>'
            '<section><div class="tgme_channel_info">i</div>%s</section>'
            '</body></html>' % posts)


PAGES, GETS = {}, []


def fake_web_get(url):
    GETS.append(url)
    return PAGES[url]


mod.web_get = fake_web_get
FULL = mod.WEB_PAGE_SIZE


def set_pages(pages):
    PAGES.clear()
    PAGES.update(pages)
    GETS.clear()


BASE = "https://t.me/s/chan"

# Forward (cursor) paging: a full page leads to the next ?after page,
# overlapping ids are deduped, and a short page ends the walk.
set_pages({
    BASE + "?after=100": page(range(101, 101 + FULL)),      # 101..120: full
    BASE + "?after=120": page(range(116, 126)),             # overlap + 121..125
})
title, widgets, exists = mod.collect_web_widgets("chan", 100)
check("forward: walks pages, dedups the overlap",
      [w["id"] for w in widgets], list(range(101, 126)))
check("forward: two page fetches", len(GETS), 2)
check_true("forward: title + exists picked up", title == "Chan" and exists)

# A repeated page (t.me ignoring ?after) must terminate the walk, not loop.
set_pages({
    BASE + "?after=100": page(range(101, 101 + FULL)),
    BASE + "?after=120": page(range(101, 101 + FULL)),      # same page again
})
_, widgets, _ = mod.collect_web_widgets("chan", 100)
check("forward: repeated page terminates the walk",
      [w["id"] for w in widgets], list(range(101, 121)))

# Nothing new after the cursor -> no widgets (run_web_ingest answers
# not_modified), channel existence still detected off the page header.
set_pages({BASE + "?after=500": page([])})
_, widgets, exists = mod.collect_web_widgets("chan", 500)
check_true("forward: no new messages -> empty, channel still exists",
           widgets == [] and exists, repr((widgets, exists)))

# Backfill (first run): page ?before until BACKFILL messages are on hand,
# return the most recent BACKFILL ascending.
saved_backfill = mod.BACKFILL
mod.BACKFILL = 8
set_pages({
    BASE: page(range(96, 101)),                             # 96..100 (5)
    BASE + "?before=96": page(range(86, 96)),               # 86..95
})
_, widgets, _ = mod.collect_web_widgets("chan", 0)
check("backfill: pages back to BACKFILL, most recent kept ascending",
      [w["id"] for w in widgets], list(range(93, 101)))

# A channel shorter than BACKFILL stops when a ?before page comes back empty.
mod.BACKFILL = 50
set_pages({
    BASE: page(range(96, 101)),
    BASE + "?before=96": page([]),
})
_, widgets, _ = mod.collect_web_widgets("chan", 0)
check("backfill: short channel -> everything it has",
      [w["id"] for w in widgets], list(range(96, 101)))
mod.BACKFILL = saved_backfill


# --- account-mode batching -------------------------------------------------------

class Msg:
    def __init__(self, mid, gid=None, message="m", media=None):
        self.id, self.grouped_id = mid, gid
        self.message, self.media = message, media


class Svc(mod.types.MessageService):
    """A service message (join/pin/...) — must be filtered out."""

    def __init__(self, mid):
        self.id, self.grouped_id = mid, None
        self.message = self.media = None


class FakeClient:
    """iter_messages semantics: newest-first by default; reverse=True walks
    ascending from min_id (exclusive)."""

    def __init__(self, msgs):
        self.msgs = msgs                     # ascending by id

    async def iter_messages(self, entity, min_id=0, reverse=False, limit=None):
        out = ([m for m in self.msgs if m.id > min_id] if reverse
               else list(reversed(self.msgs))[:limit])
        for m in out:
            yield m


# group_messages: adjacent messages sharing a grouped_id collapse into one album.
groups = mod.group_messages(
    [Msg(1), Msg(2, gid=9), Msg(3, gid=9), Msg(4), Msg(5, gid=9)])
check("albums: adjacent grouped_id runs collapse",
      [[m.id for m in g["msgs"]] for g in groups], [[1], [2, 3], [4], [5]])
check("albums: ungrouped messages never merge",
      len(mod.group_messages([Msg(1), Msg(2)])), 2)

# collect_messages, cursor mode: ascending from the cursor, bounded by
# MAX_BATCH plus the album-straddling slack.
saved_batch = mod.MAX_BATCH
mod.MAX_BATCH = 3                            # hard bound = 3 + 32 = 35
client = FakeClient([Msg(i) for i in range(1, 61)])
got = asyncio.run(mod.collect_messages(client, None, 10))
check("cursor mode: ascending from the cursor, hard-bounded",
      [m.id for m in got], list(range(11, 46)))
mod.MAX_BATCH = saved_batch

# Service and content-less messages are dropped.
client = FakeClient([Msg(11), Svc(12), Msg(13, message=None), Msg(14)])
got = asyncio.run(mod.collect_messages(client, None, 10))
check("cursor mode: service/empty messages filtered",
      [m.id for m in got], [11, 14])

# Backfill (first run): the BACKFILL most recent messages, ascending.
saved_backfill = mod.BACKFILL
mod.BACKFILL = 5
client = FakeClient([Msg(i) for i in range(1, 11)])
got = asyncio.run(mod.collect_messages(client, None, 0))
check("backfill: most recent BACKFILL ascending",
      [m.id for m in got], [6, 7, 8, 9, 10])
mod.BACKFILL = saved_backfill

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
