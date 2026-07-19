"""Tests for srr-digest-gen's pure logic: collection windowing and paging,
note filtering, chunking, RSS rendering and reading the published feed back
as history. No claude, no srr, no network — `srr` is stubbed by
monkeypatching run_json.

    uv run tests/test_digest_content.py
"""
import importlib.machinery
import importlib.util
import os
import re
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-digest-gen")

loader = importlib.machinery.SourceFileLoader("srr_digest_gen", SCRIPT)
spec = importlib.util.spec_from_loader("srr_digest_gen", loader)
dg = importlib.util.module_from_spec(spec)
loader.exec_module(dg)

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


def check_raises(name, fn, needle):
    try:
        fn()
    except dg.DigestError as e:
        check_true(name, needle in str(e), str(e))
        return
    check_true(name, False, "no DigestError raised")


# --- collect --------------------------------------------------------------
NOW = time.time()
FEEDS = [
    {"id": 1, "title": "Tech News", "tag": "news/tech"},
    {"id": 2, "title": "Daily Digest", "tag": "digest"},
    {"id": 3, "title": "Untagged"},
]
ARTS = [  # newest first, as `srr art ls` returns them
    {"f": 1, "a": NOW - 60, "t": "Fresh story", "l": "http://e/1",
     "c": "<p>Body <b>text</b>\n  here</p>"},
    {"f": 2, "a": NOW - 120, "t": "Yesterday's digest", "l": "", "c": "<p>self</p>"},
    {"f": 3, "a": NOW - 180, "t": "  ", "l": "", "c": "<p>untitled body</p>"},
    {"f": 1, "a": NOW - 90000, "t": "Too old", "l": "", "c": "old"},
    {"f": 1, "a": NOW - 90060, "t": "Older still", "l": "", "c": "older"},
]

calls = []


def fake_run_json(cmd):
    calls.append(cmd)
    if cmd[:3] == ["srr", "feed", "ls"]:
        return FEEDS
    return {"articles": ARTS}


dg.run_json = fake_run_json

got = dg.collect(24, [], [], 1000)
check("collect: window stops at the first article older than --hours "
      "(3 in window, 1 dropped by tag)", len(got), 2)
check("collect: feed titles resolved", [a["feed"] for a in got], ["Tech News", "Untagged"])
check("collect: html stripped and whitespace folded", got[0]["text"], "Body text here")
check("collect: link kept", got[0]["link"], "http://e/1")
check("collect: blank title falls back to the body", got[1]["title"], "untitled body")

check("collect: the window is the backend's --since, a page at a time",
      calls[1][:5], ["srr", "art", "ls", "-l", str(dg.PAGE_SIZE)])
check_true("collect: --since is an instant, not a duration that creeps "
           "forward from page to page",
           abs(datetime.strptime(calls[1][6], "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=timezone.utc).timestamp()
               - (time.time() - 24 * 3600)) < 60)
calls.clear()
dg.collect(24, ["news/tech"], [], 5)
check("collect: --tag reaches srr art ls", calls[1][7:], ["-g", "news/tech"])
calls.clear()
dg.collect(24, ["news/tech", "digest"], [], 5)
check("collect: several tags become repeated -g flags",
      calls[1][7:], ["-g", "news/tech", "-g", "digest"])
calls.clear()
dg.collect(24, [], [1, 3], 5)
check("collect: feed ids become repeated -i flags",
      calls[1][7:], ["-i", "1", "-i", "3"])
calls.clear()
dg.collect(24, ["digest"], [3], 5)
check("collect: tags and feeds ride one query — the backend unions them",
      calls[1][7:], ["-g", "digest", "-i", "3"])

# a typo'd selection must be a clear error, not an empty window that the
# empty-window check then blames on the fetch loop
calls.clear()
check_raises("collect: an unknown tag is an error naming the known ones",
             lambda: dg.collect(24, ["newz"], [], 5),
             "unknown tag 'newz'; srr feed ls knows: digest, news/tech")
check("collect: the unknown tag was refused before any article query",
      len(calls), 1)
calls.clear()
check_raises("collect: an unknown feed id is an error naming the known ones",
             lambda: dg.collect(24, [], [9], 5),
             "unknown feed id 9; srr feed ls knows: 1, 2, 3")
check("collect: the unknown feed id was refused before any article query",
      len(calls), 1)

# exclusion wins even over explicit selection: feed 2 carries the default
# exclude tag, so asking for it by id still yields the other feeds only
# (the stub returns every article regardless of filters — what is under test
# is the client-side exclude, not the backend)
calls.clear()
check("collect: --exclude-tag wins over an explicitly selected feed",
      [a["feed"] for a in dg.collect(24, [], [2], 1000)],
      ["Tech News", "Untagged"])

wide = dg.collect(24, [], [], 1000, exclude_tag="")
check("collect: empty exclude-tag digests everything in the window", len(wide), 3)
check("collect: the digest feed is what the default exclude-tag drops",
      wide[1]["feed"], "Daily Digest")
check("collect: exclude-tag is an exact tag match, not a prefix "
      "('news' leaves 'news/tech' alone)",
      len(dg.collect(24, [], [], 1000, exclude_tag="news")), 3)

long_art = [{"f": 1, "a": NOW, "t": "Long", "l": "", "c": "x" * 5000}]
ARTS_SAVE = ARTS
ARTS = long_art
check("collect: article text truncated",
      len(dg.collect(24, [], [], 1000)[0]["text"]), dg.MAX_ARTICLE_CHARS)
ARTS = ARTS_SAVE

# --- parse_time_bound: the forms `srr art ls --since` takes ----------------
T0 = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
check("parse_time_bound: hours duration", dg.parse_time_bound("24h", T0),
      T0 - timedelta(hours=24))
check("parse_time_bound: weeks duration", dg.parse_time_bound("2w", T0),
      T0 - timedelta(days=14))
check("parse_time_bound: compound duration", dg.parse_time_bound("1d12h", T0),
      T0 - timedelta(hours=36))
check("parse_time_bound: float duration", dg.parse_time_bound("1.5h", T0),
      T0 - timedelta(hours=1.5))
check("parse_time_bound: RFC3339 instant, Z accepted",
      dg.parse_time_bound("2026-07-15T10:00:00Z", T0),
      datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc))
check("parse_time_bound: bare date is local midnight, like the backend",
      dg.parse_time_bound("2026-07-15", T0),
      datetime(2026, 7, 15).astimezone())
check_raises("parse_time_bound: garbage is a named error",
             lambda: dg.parse_time_bound("yesterday", T0), "--since 'yesterday'")

# --- collect: explicit --since instant / --before cursor -------------------
calls.clear()
pinned = datetime.fromtimestamp(NOW - 3600, timezone.utc)
dg.collect(24, [], [], 1000, since=pinned)
check("collect: an explicit since instant is sent verbatim, not recomputed",
      calls[1][6], pinned.strftime("%Y-%m-%dT%H:%M:%SZ"))
calls.clear()
dg.collect(24, [], [], 1000,
           since=datetime.fromtimestamp(NOW - 3600, timezone(timedelta(hours=2))))
check("collect: a non-UTC since is normalized before the Z-suffixed strftime",
      calls[1][6], pinned.strftime("%Y-%m-%dT%H:%M:%SZ"))
check("collect: the explicit since drives the client-side belt too",
      [a["feed"] for a in dg.collect(
          24, [], [], 1000,
          since=datetime.fromtimestamp(NOW - 150, timezone.utc))],
      ["Tech News"])
calls.clear()
dg.collect(24, [], [], 1000, before=77)
check("collect: --before seeds the first page's cursor (srr art ls -b)",
      calls[1][7:], ["-b", "77"])

# --- collect: paging back to the cutoff -----------------------------------
# The window is only "covered" once an article older than the cutoff shows up,
# or the store runs out. A page boundary in between must not end collection.
PAGES = {
    None: {"articles": ARTS[:2], "next_cursor": 900},
    900: {"articles": ARTS[2:4], "next_cursor": 800},
    800: {"articles": ARTS[4:]},
}


def paged_run_json(cmd):
    calls.append(cmd)
    if cmd[:3] == ["srr", "feed", "ls"]:
        return FEEDS
    cursor = int(cmd[cmd.index("-b") + 1]) if "-b" in cmd else None
    return PAGES[cursor]


dg.run_json = paged_run_json
calls.clear()
paged = dg.collect(24, [], [], 1000)
check("collect: keeps paging past a page boundary inside the window", len(paged), 2)
check("collect: pages with the returned cursor",
      [c[c.index("-b") + 1] for c in calls if "-b" in c], ["900"])
check("collect: stops paging at the first article past the cutoff",
      len([c for c in calls if c[:3] == ["srr", "art", "ls"]]), 2)

# store exhausted before the window is: no cursor, no error, no infinite loop
EXHAUSTED = {None: {"articles": ARTS[:1]}}
PAGES_SAVE = PAGES
PAGES = EXHAUSTED
check("collect: an exhausted store ends collection", len(dg.collect(24, [], [], 1000)), 1)
PAGES = PAGES_SAVE

# hitting --limit before the window is covered is an error, never a quiet
# partial digest
check_raises("collect: --limit reached mid-window aborts the run",
             lambda: dg.collect(24, [], [], 1), "--limit 1 reached")


def stuck_run_json(cmd):
    if cmd[:3] == ["srr", "feed", "ls"]:
        return FEEDS
    return {"articles": ARTS[:1], "next_cursor": 7}  # same cursor, forever


dg.run_json = stuck_run_json
check_raises("collect: a cursor that never advances stops the loop, "
             "rather than paging the store forever",
             lambda: dg.collect(24, [], [], 1000), "same cursor twice")
dg.run_json = fake_run_json

# --- filter_notes ---------------------------------------------------------
notes = [
    "* [5] Big thing happened. (Feed) http://e/a\n* [2] Minor thing. (Feed) http://e/b",
    "* [3] Worth a glance. (Feed) http://e/c\n* [1] Noise. (Feed) http://e/d",
]
kept = dg.filter_notes(notes).splitlines()
check("filter_notes: drops below the relevance floor", len(kept), 2)
check_true("filter_notes: keeps the 5", any("[5]" in k for k in kept), kept)
check_true("filter_notes: keeps the 3", any("[3]" in k for k in kept), kept)

wrapped = ["* [4] A claim that the model\n  hard-wrapped mid-sentence. (Feed) http://e/e"]
check("filter_notes: rejoins hard-wrapped continuation lines",
      dg.filter_notes(wrapped),
      "* [4] A claim that the model hard-wrapped mid-sentence. (Feed) http://e/e")

drifted = ["* No score here.\n* Nor here.\n* [1] Only this one scored."]
check("filter_notes: fails open when the format drifted",
      len(dg.filter_notes(drifted).splitlines()), 3)

# the reduce prompt has no natural bound: a wide window means many chunks
# means many notes, so the budget trims from the bottom of the scores
many = ["\n".join("* [%d] note %d, padded out to a realistic length %s"
                  % (1 + i % 5, i, "x" * 60) for i in range(200))]
check("filter_notes: under budget, the floor stays where it is",
      {ln[3] for ln in dg.filter_notes(many).splitlines()}, {"3", "4", "5"})
budgeted = dg.filter_notes(many, budget=4000).splitlines()
check_true("filter_notes: over budget, the floor rises instead of cutting "
           "somewhere arbitrary", {ln[3] for ln in budgeted} == {"5"}, budgeted[:3])
check_true("filter_notes: and the result fits the budget",
           len("\n".join(budgeted)) <= 4000, len("\n".join(budgeted)))
check_true("filter_notes: a budget below one tier still fits (tail dropped)",
           len(dg.filter_notes(many, budget=500)) <= 500,
           len(dg.filter_notes(many, budget=500)))
check_raises("filter_notes: a day where nothing clears the floor is a failed "
             "run, not an empty prompt for the reduce call",
             lambda: dg.filter_notes(["* [1] noise\n* [2] marginal"]),
             "nothing worth a digest")
check_true("filter_notes: an unscored batch is never emptied by the budget",
           len(dg.filter_notes(["\n".join("* unscored note %d" % i
                                          for i in range(200))], budget=500)) > 0,
           "fail-open notes have no scores to raise a floor against")

# --- chunk_articles -------------------------------------------------------
half = dg.MAP_CHUNK_CHARS // 2 + 1
arts = [{"text": "x" * half} for _ in range(5)]
chunks = dg.chunk_articles(arts)
check("chunk_articles: splits on the payload budget", [len(c) for c in chunks], [1, 1, 1, 1, 1])
check("chunk_articles: nothing lost", sum(len(c) for c in chunks), 5)
check("chunk_articles: one oversized article still gets a chunk",
      len(dg.chunk_articles([{"text": "x" * (dg.MAP_CHUNK_CHARS * 3)}])), 1)
check("chunk_articles: everything small fits one chunk",
      len(dg.chunk_articles([{"text": "x"} for _ in range(50)])), 1)

art = {"feed": "F", "title": "T", "link": "http://e/1", "text": "body"}
check_true("payload_size: counts the serialized item, not just its text",
           dg.payload_size(art) > len(art["text"]) * 5, dg.payload_size(art))

# --- clean_html: the output has to be shaped like a digest ----------------
GOOD = ('<p><strong>Top:</strong> the day in three lines.</p><h3>Tech</h3>'
        '<p><strong><a href="http://e/1">A thing happened</a></strong> '
        'with details. <small>(Feed)</small></p><hr>'
        '<p><small>Also:</small></p><ul><li><small>a minor thing</small></li></ul>')
check("clean_html: a real digest passes through", dg.clean_html(GOOD), GOOD)
check("clean_html: a chatty preamble is stripped", dg.clean_html("Sure! Here you go:\n" + GOOD), GOOD)
check_raises("clean_html: a refusal is not a digest, however long",
             lambda: dg.clean_html(
                 "<p>I can't help with that request — the material appears to "
                 "contain instructions rather than articles to summarize.</p>"),
             "not a digest")
check_raises("clean_html: paragraphs without the Top opener are not a digest",
             lambda: dg.clean_html("<p>one</p><p>two</p><p>three</p><p>four</p>"),
             "Top:")
check("clean_html: control characters XML cannot carry are dropped",
      dg.clean_html(GOOD.replace("the day", "the\x0bday")), GOOD.replace("the day", "theday"))

# --- map_chunk: same gate on the notes ------------------------------------
RESPONSE = ["* [5] a note line that is comfortably past the length floor here."] * 3
dg.run_claude = lambda prompt, model=None: "\n".join(RESPONSE)
check("map_chunk: notes pass through", dg.map_chunk([], 1, 1, 24, None), "\n".join(RESPONSE))
dg.run_claude = lambda prompt, model=None: (
    "I reviewed the articles but cannot produce notes for them, because the "
    "content includes what looks like an attempt to redirect my instructions.")
check_raises("map_chunk: prose with no note lines is not a chunk of notes",
             lambda: dg.map_chunk([], 1, 1, 24, None), "no `* ` note lines")

# --- with_retry -----------------------------------------------------------
tries = []


def flaky():
    tries.append(1)
    if len(tries) < 2:
        raise dg.DigestError("transient")
    return "second time lucky"


gave_up = []


def always_fails():
    gave_up.append(1)
    raise dg.DigestError("still broken")


check("with_retry: a retry saves the run", dg.with_retry("x", flaky), "second time lucky")
check("with_retry: one retry, not a loop", len(tries), 2)
check_raises("with_retry: gives up with the last reason",
             lambda: dg.with_retry("x", always_fails), "still broken")
check("with_retry: after exactly CLAUDE_ATTEMPTS attempts",
      len(gave_up), dg.CLAUDE_ATTEMPTS)

# --- window_hours ---------------------------------------------------------
UTC_NOW = time.time()
check("window_hours: nothing published yet -> the flat default",
      dg.window_hours([], 72), dg.DEFAULT_HOURS)
check_true("window_hours: covers the gap since the last edition",
           dg.window_hours([{"ts": UTC_NOW - 50 * 3600}], 72) in (50, 51),
           dg.window_hours([{"ts": UTC_NOW - 50 * 3600}], 72))
check("window_hours: never less than a day (a same-day rerun still gets one)",
      dg.window_hours([{"ts": UTC_NOW - 3 * 3600}], 72), dg.DEFAULT_HOURS)
check("window_hours: a long outage is capped, not digested whole",
      dg.window_hours([{"ts": UTC_NOW - 200 * 3600}], 72), 72)
check_true("window_hours: measures the newest entry, whatever the order",
           dg.window_hours([{"ts": UTC_NOW - 200 * 3600},
                            {"ts": UTC_NOW - 50 * 3600}], 72) in (50, 51),
           "")

# --- map_chunks -----------------------------------------------------------
mapped = []


def fake_map_chunk(chunk, part, parts, hours, model):
    mapped.append(part)
    if part in FAIL_PARTS:
        raise dg.DigestError("boom")
    return f"* [5] note from chunk {part}"


FAIL_PARTS = set()
dg.map_chunk = fake_map_chunk
six = [[{"text": "x"}] for _ in range(6)]

check("map_chunks: one note per chunk, in chunk order",
      dg.map_chunks(six, 24, None),
      [f"* [5] note from chunk {i}" for i in range(1, 7)])

FAIL_PARTS = {2, 5}
check("map_chunks: tolerates up to a third of the chunks failing",
      dg.map_chunks(six, 24, None),
      ["* [5] note from chunk 1", "* [5] note from chunk 3",
       "* [5] note from chunk 4", "* [5] note from chunk 6"])

FAIL_PARTS = {1, 2, 3}
check_raises("map_chunks: aborts when more than a third is lost",
             lambda: dg.map_chunks(six, 24, None), "3/6 map chunks failed")
FAIL_PARTS = set()

# --- render_rss -----------------------------------------------------------
history = [
    {"date": "2026-07-19", "ts": 1784448000, "title": "Daily Digest — 2026-07-19 (12 articles)",
     "html": "<p>a & b <em>c</em></p>"},
    {"date": "2026-07-18", "ts": 1784361600, "title": "Daily Digest — 2026-07-18 (7 articles)",
     "html": "<p>older</p>"},
]
rss = dg.render_rss(history)
check("render_rss: one item per history entry", rss.count("<item>"), 2)
check_true("render_rss: date-keyed guid",
           '<guid isPermaLink="false">srr-digest-2026-07-19</guid>' in rss, rss[:400])
check_true("render_rss: html escaped into the description",
           "&lt;p&gt;a &amp; b &lt;em&gt;c&lt;/em&gt;&lt;/p&gt;" in rss, rss)
check_true("render_rss: entry title kept verbatim",
           "Daily Digest — 2026-07-19 (12 articles)" in rss, rss)
check_true("render_rss: rfc822 pubDate",
           re.search(r"<pubDate>\w{3}, \d\d \w{3} 2026 ", rss) is not None, rss)
check("render_rss: no <link> without --link", rss.count("<link>"), 0)

linked = dg.render_rss(history, "https://example.test/out/digest.rss")
check("render_rss: --link is the channel's, not every item's "
      "(an item link would open the raw feed file)", linked.count("<link>"), 1)
check_true("render_rss: link escaped",
           "<link>https://example.test/out/digest.rss</link>" in linked, linked[:400])
check_true("render_rss: control characters never reach the XML",
           "\x0b" not in dg.render_rss(
               [{"date": "2026-07-19", "ts": 1784448000, "title": "t\x0b",
                 "html": "<p>a\x0bb</p>"}]), "")

# --- parse_history (the published feed IS the state) ----------------------
check("parse_history: round-trips render_rss exactly", dg.parse_history(rss), history)
check("parse_history: round-trips through --link too",
      dg.parse_history(linked), history)
check("parse_history: an empty feed is an empty history",
      dg.parse_history(dg.render_rss([])), [])

foreign = rss.replace('<guid isPermaLink="false">srr-digest-2026-07-18</guid>',
                      '<guid isPermaLink="false">something-else</guid>')
check("parse_history: leaves items that are not ours alone",
      [e["date"] for e in dg.parse_history(foreign)], ["2026-07-19"])

nodate = re.sub(r"<pubDate>[^<]*</pubDate>", "", dg.render_rss(history[1:]))
check("parse_history: a missing pubDate falls back to midnight UTC on the guid's date",
      dg.parse_history(nodate),
      [{"date": "2026-07-18", "ts": 1784332800, "title": history[1]["title"],
        "html": history[1]["html"]}])

dup = rss.replace("srr-digest-2026-07-18", "srr-digest-2026-07-19")
check("parse_history: one entry per day, whatever the feed holds",
      [e["date"] for e in dg.parse_history(dup)], ["2026-07-19"])

undated = re.sub(r"<pubDate>[^<]*</pubDate>", "",
                 dg.render_rss([dict(history[0], date="not-a-date")]))
check("parse_history: an item with neither a pubDate nor a dated guid is "
      "skipped, not a traceback", dg.parse_history(undated), [])

check_raises("parse_history: refuses to guess at unparsable XML",
             lambda: dg.parse_history("<rss><channel><item>"), "not parsable XML")

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
