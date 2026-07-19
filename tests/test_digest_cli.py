"""Tests for srr-digest-gen's modes and the srr/claude boundary: what each
mode spawns, what it pushes, that the published feed is the only state, and
that nothing reaches the store unless the whole run succeeded. Every
subprocess (srr, claude) is monkeypatched.

    uv run tests/test_digest_cli.py
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from zoneinfo import ZoneInfo

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


def check_exits(name, argv, needle):
    try:
        run(argv)
    except SystemExit as e:
        check_true(name, needle in str(e), str(e))
        return
    check_true(name, False, "no SystemExit raised")


NOW = time.time()
TODAY = datetime.now().astimezone().strftime("%Y-%m-%d")
FEEDS = [{"id": 1, "title": "Tech News", "tag": "news"}]
ARTS = [{"f": 1, "a": NOW - 60, "t": "Story", "l": "http://e/1", "c": "<p>body</p>"}]
DIGEST = ('<p><strong>Top:</strong> the day in a couple of sentences.</p>'
          '<h3>Tech</h3><p><strong><a href="http://e/1">A thing happened</a>'
          '</strong> with some substance. <small>(Tech News)</small></p><hr>'
          '<p><small>Also:</small></p><ul><li><small>a minor thing</small></li></ul>')
YESTERDAY = {"date": "2000-01-01", "ts": 946684800,
             "title": "Daily Digest — 2000-01-01 (3 articles)", "html": "<p>older</p>"}

claude_calls = []
pushed = []
fetched = []
HISTORY = []


def fake_run_json(cmd):
    return FEEDS if cmd[:3] == ["srr", "feed", "ls"] else {"articles": ARTS}


def fake_run_claude(prompt, model=None):
    claude_calls.append((prompt, model))
    return DIGEST


def fake_publish(name, rss):
    pushed.append((name, rss))


def fake_fetch_history(name):
    fetched.append(name)
    return [dict(e) for e in HISTORY]


windows = []
real_collect = dg.collect


def spy_collect(hours, tags, feed_ids, limit, exclude_tag=dg.DEFAULT_EXCLUDE_TAG):
    windows.append(hours)
    return real_collect(hours, tags, feed_ids, limit, exclude_tag)


dg.run_json = fake_run_json
dg.run_claude = fake_run_claude
dg.publish = fake_publish
dg.fetch_history = fake_fetch_history
dg.collect = spy_collect


def run(argv):
    """Run main(argv) capturing stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        dg.main(argv)
    return buf.getvalue()


def reset():
    claude_calls.clear()
    pushed.clear()
    fetched.clear()
    windows.clear()


# --- --dump ---------------------------------------------------------------
reset()
out = run(["--dump"])
check("--dump: prints the collected articles as JSON",
      json.loads(out),
      [{"feed": "Tech News", "title": "Story", "link": "http://e/1", "text": "body"}])
check("--dump: never calls claude", claude_calls, [])
check("--dump: never publishes", pushed, [])
check("--dump: never reads the feed back", fetched, [])
check("--dump: plain default window, no gap arithmetic", windows, [dg.DEFAULT_HOURS])

# --- selection flags --------------------------------------------------------
p = dg.build_parser()
check("selection: --tag defaults to no filter", p.parse_args([]).tag, [])
check("selection: --feed defaults to no filter", p.parse_args([]).feed, [])
check("selection: --tag is repeatable and comma-splittable",
      p.parse_args(["--tag", "a", "--tag", "b,c"]).tag, ["a", "b", "c"])
check("selection: --feed is repeatable, comma-splittable, and numeric",
      p.parse_args(["--feed", "3", "--feed", "4,5"]).feed, [3, 4, 5])


def check_parse_fails(name, argv):
    """argparse rejections exit(2) before main() ever runs; usage noise on
    stderr is swallowed so the suite output stays one line per check."""
    try:
        with open(os.devnull, "w") as null, redirect_stderr(null):
            dg.build_parser().parse_args(argv)
    except SystemExit:
        check_true(name, True)
        return
    check_true(name, False, "argparse accepted it")


check_parse_fails("selection: a non-numeric --feed is refused at parse time",
                  ["--feed", "nasa"])
check_parse_fails("selection: an empty --tag token is refused at parse time "
                  "(it would silently select nothing, i.e. widen the digest)",
                  ["--tag", "a,,b"])
check_parse_fails("selection: an empty --feed token is refused at parse time",
                  ["--feed", ""])

# end-to-end: the single-tag form the /srr-digest skill uses keeps working
reset()
out = run(["--dump", "--tag", "news"])
check("selection: --dump --tag <one tag> still collects (back-compat)",
      json.loads(out),
      [{"feed": "Tech News", "title": "Story", "link": "http://e/1", "text": "body"}])

reset()
check_exits("selection: an unknown tag aborts with a clear error",
            ["--dump", "--tag", "nope"], "unknown tag 'nope'")
check("selection: the unknown tag cost no claude call", claude_calls, [])
check("selection: ...and pushed nothing", pushed, [])

# --- --dry-run ------------------------------------------------------------
reset()
HISTORY[:] = [YESTERDAY]
out = run(["--dry-run"])
check_true("--dry-run: prints the rss to stdout", out.startswith("<?xml version"), out[:80])
check_true("--dry-run: the digest rides in the item", "Top:" in out, out)
check("--dry-run: called claude once", len(claude_calls), 1)
check("--dry-run: never publishes", pushed, [])
check("--dry-run: still reads the published feed back", fetched, ["digest"])
check("--dry-run: renders today on top of the published days", out.count("<item>"), 2)

# --- default (publish) ----------------------------------------------------
reset()
HISTORY[:] = [YESTERDAY]
out = run(["--name", "digest"])
check("publish: one push", len(pushed), 1)
check("publish: syndication name passed through", pushed[0][0], "digest")
check("publish: the feed it read back is the feed it pushes to", fetched, ["digest"])
check_true("publish: pushed bytes are the rss", pushed[0][1].startswith("<?xml"), pushed[0][1][:80])
check_true("publish: reports the destination", "out/digest" in out, out)

published = dg.parse_history(pushed[0][1])
check("publish: today prepended to the days already in the feed",
      [e["date"] for e in published], [TODAY, YESTERDAY["date"]])
check("publish: the published day is kept verbatim", published[1], YESTERDAY)
check("publish: entry keeps the digest html", published[0]["html"], DIGEST)
check("publish: title counts the collected articles",
      published[0]["title"], f"Daily Digest — {TODAY} (1 articles)")

reset()
run(["--tz", "Europe/Madrid"])
madrid = dg.parse_history(pushed[0][1])[0]
check("publish: entry dated in --tz",
      madrid["date"],
      datetime.fromtimestamp(madrid["ts"], ZoneInfo("Europe/Madrid")).strftime("%Y-%m-%d"))

# --- the window follows the gap since the last edition --------------------
def aged(hours_ago):
    return [{"date": "2000-01-01", "ts": int(NOW - hours_ago * 3600),
             "title": "Daily Digest — old", "html": "<p>old</p>"}]


reset()
HISTORY[:] = aged(50)
run([])
check_true("window: defaults to the gap since the last edition (a missed run "
           "is caught up, not skipped)", windows[0] in (50, 51), windows)

reset()
HISTORY[:] = aged(400)
run(["--max-hours", "48"])
check("window: --max-hours caps a long outage", windows[0], 48)

reset()
HISTORY[:] = aged(50)
run(["--hours", "6"])
check("window: --hours overrides the gap", windows[0], 6)

reset()
HISTORY[:] = []
run([])
check("window: an empty feed falls back to the flat default",
      windows[0], dg.DEFAULT_HOURS)

# --- a day already in the feed --------------------------------------------
reset()
HISTORY[:] = [{"date": TODAY, "ts": int(NOW), "title": "Daily Digest — old run",
               "html": "<p>the version readers already have</p>"}, YESTERDAY]
check_exits("rerun: refuses to republish a day already in the feed",
            [], "already published")
check("rerun: refusing costs no claude call", claude_calls, [])
check("rerun: refusing pushes nothing", pushed, [])

reset()
out = run(["--force"])
forced = dg.parse_history(pushed[0][1])
check("--force: replaces the day rather than appending a second one",
      [e["date"] for e in forced], [TODAY, YESTERDAY["date"]])
check("--force: the replacement is this run's digest", forced[0]["html"], DIGEST)

reset()
out = run(["--dry-run"])
check_true("--dry-run: previews a rerun without needing --force", out.count("<item>") == 2, out[:200])

# --- --no-history ---------------------------------------------------------
reset()
HISTORY[:] = [YESTERDAY]
out = run(["--no-history"])
check("--no-history: never reads the feed back", fetched, [])
check("--no-history: rewrites the feed with today alone",
      [e["date"] for e in dg.parse_history(pushed[0][1])], [TODAY])

# --- KEEP_DAYS ------------------------------------------------------------
reset()
HISTORY[:] = [{"date": "2026-%02d-%02d" % (1 + i // 28, 1 + i % 28), "ts": 1767225600,
               "title": "Daily Digest — old", "html": "<p>old</p>"}
              for i in range(dg.KEEP_DAYS + 5)]
run([])
check("publish: the feed is capped at KEEP_DAYS",
      len(dg.parse_history(pushed[0][1])), dg.KEEP_DAYS)

# --- models ---------------------------------------------------------------
reset()
HISTORY[:] = []
run(["--model", "opus"])
check("--model reaches the digest call", claude_calls[0][1], "opus")
check("default: no model flag leaves the claude CLI's default",
      dg.build_parser().parse_args([]).model, None)
check("default map model", dg.build_parser().parse_args([]).map_model, dg.DEFAULT_MAP_MODEL)

# map-reduce path: chunks map on --map-model, the reduce keeps --model
reset()
big = "y" * dg.MAX_ARTICLE_CHARS
ARTS = [{"f": 1, "a": NOW - 60, "t": "S%d" % i, "l": "", "c": big}
        for i in range(dg.SINGLE_PASS_CHARS // dg.MAX_ARTICLE_CHARS + 2)]


def fake_map_claude(prompt, model=None):
    claude_calls.append((prompt, model))
    return "* [5] A scored note that is long enough to clear the length floor. " * 3


dg.run_claude = fake_map_claude
try:
    run(["--model", "opus", "--map-model", "haiku"])
except SystemExit:  # the reduce output is notes, not html — expected here
    pass
models = [m for _, m in claude_calls]
check_true("map-reduce: more than one claude call", len(models) > 1, len(models))
check("map-reduce: map calls use --map-model", sorted(set(models[:-2])), ["haiku"])
check("map-reduce: the reduce call uses --model", models[-1], "opus")
check("map-reduce: a reduce that comes back unusable is retried once",
      models[-2:], ["opus", "opus"])
# the fan-out is bounded: a window wide enough to need more chunks than
# --max-chunks is refused before a single call is paid for
reset()
dg.run_claude = fake_map_claude
check_exits("--max-chunks: refuses a fan-out that was never asked for",
            ["--max-chunks", "2"], "refusing to spend that unasked")
check("--max-chunks: refused before any claude call", claude_calls, [])

dg.run_claude = fake_run_claude
ARTS = [{"f": 1, "a": NOW - 60, "t": "Story", "l": "http://e/1", "c": "<p>body</p>"}]

# --- failure discipline ---------------------------------------------------
reset()


def failing_publish(name, rss):
    raise dg.DigestError("srr syndicate push failed (1): boom")


dg.publish = failing_publish
check_exits("publish failure: exits with the reason, not a traceback",
            [], "srr syndicate push failed")
dg.publish = fake_publish

reset()


def failing_run_json(cmd):
    raise dg.DigestError("srr: not found on PATH")


dg.run_json = failing_run_json
check_exits("a broken srr exits with the reason, not a traceback", [], "not found on PATH")
check("a broken srr costs no claude call", claude_calls, [])
dg.run_json = fake_run_json

# --- empty window ---------------------------------------------------------
reset()
HISTORY[:] = []
ARTS = []
check_exits("empty window: an error, because that is a broken fetch loop, "
            "not a quiet news day", [], "no articles at all")
check("empty window: nothing published", pushed, [])
check("empty window: no claude call either", claude_calls, [])

reset()
out = run(["--allow-empty"])
check_true("--allow-empty: takes the quiet day and exits clean",
           "no articles" in out, out)
check("--allow-empty: still publishes nothing", pushed, [])

# --- run_cmd: the one path that spawns for real ---------------------------
# The only test that runs an actual subprocess, because the bug it guards
# cannot be faked: `claude` is a wrapper, so the child that holds the pipe is
# a *grandchild*. Killing the timed-out process by pid leaves it alive holding
# stdout open, and the follow-up communicate() then waits for it forever — a
# 15-minute timeout that never fires, in a unit with no timeout of its own.
# `sh -c 'sleep 30 & wait'` is that shape in one line.
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(
    AssertionError("run_cmd hung past its timeout")))
signal.alarm(20)  # so a regression fails the suite instead of hanging it
t0 = time.monotonic()
try:
    dg.run_cmd(["sh", "-c", "sleep 30 & wait"], timeout=1)
    hung, err = False, "no DigestError"
except dg.DigestError as e:
    hung, err = False, str(e)
except AssertionError as e:
    hung, err = True, str(e)
finally:
    signal.alarm(0)
elapsed = time.monotonic() - t0
# Tight bound on purpose: killing by pid instead of by group still returns,
# because the reap below it is bounded too — but only after burning that
# bound. Measured 1.0s correct, 11.0s with the group kill removed.
check_true("run_cmd: a timeout kills the process group, so a grandchild "
           "holding the pipe cannot stall the reap", not hung and elapsed < 5,
           f"{elapsed:.1f}s, {err}")
check_true("run_cmd: a timeout is a DigestError naming the limit",
           "timed out after 1s" in err, err)
check_true("run_cmd: the grandchild does not outlive the run",
           subprocess.run(["pgrep", "-x", "-f", "sleep 30"],
                          capture_output=True).returncode != 0)

# SIGTERM is a request, and the shutdown path used to make it and leave: a
# claude call that declined would have gone on running (and billing) with the
# process that started it already gone. Measured with a child that ignores
# SIGTERM, because the escalation is invisible against any child that doesn't.
stubborn = subprocess.Popen(
    [sys.executable, "-c", "import signal, time; "
     "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
     "print('ready', flush=True); time.sleep(60)"],
    stdout=subprocess.PIPE, text=True, start_new_session=True)
stubborn.stdout.readline()  # running, and the handler is installed
dg._children.add(stubborn)
t0 = time.monotonic()
dg.kill_children(grace=0.5)
rc = stubborn.wait(timeout=5)
elapsed = time.monotonic() - t0
dg._children.discard(stubborn)
check("kill_children: a child that ignores SIGTERM is escalated to SIGKILL",
      rc, -signal.SIGKILL)
check_true("kill_children: ... after the grace period, not instead of it",
           0.5 <= elapsed < 4, f"{elapsed:.2f}s")

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
