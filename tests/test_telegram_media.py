"""Unit tests for srr-telegram's media_element partial-download protection.

A fetch cycle killed mid-download (cmd-timeout, crash, dropped connection) must
never leave a truncated file that a later cycle reuses and publishes — the bug
behind live chron 437 (t.me/AltRightEspana/14047, moov-less 9.3 MB mp4 in R2).

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_telegram_media.py
"""
import asyncio
import importlib.machinery
import importlib.util
import os
import shutil
import time
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-telegram")

loader = importlib.machinery.SourceFileLoader("srr_telegram", SCRIPT)
spec = importlib.util.spec_from_loader("srr_telegram", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

failures = []


def check_true(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        print("   ", detail)
        failures.append(name)


class FakeFile:
    def __init__(self, size):
        self.ext = ".mp4"
        self.mime_type = "video/mp4"
        self.size = size
        self.name = "clip.mp4"


class FakeMsg:
    """Just enough surface for classify_media's video branch."""

    def __init__(self, msg_id, size):
        self.id = msg_id
        self.file = FakeFile(size)
        self.media = None          # not a MessageMediaPhoto
        self.sticker = None
        self.voice = None
        self.audio = None
        self.video = True
        self.video_note = None
        self.gif = None


class FullWriter:
    """Downloads the whole advertised file."""

    def __init__(self, size):
        self.size = size
        self.calls = 0

    async def download_media(self, msg, file=None):
        self.calls += 1
        with open(file, "wb") as fh:
            fh.write(b"x" * self.size)
        return file


class Interrupter:
    """Connection dies mid-download, partial bytes already written."""

    def __init__(self, partial):
        self.partial = partial

    async def download_media(self, msg, file=None):
        with open(file, "wb") as fh:
            fh.write(b"x" * self.partial)
        raise ConnectionError("simulated mid-download death")


class ShortWriter:
    """Returns 'success' but wrote fewer bytes than Telegram advertised."""

    def __init__(self, partial):
        self.partial = partial

    async def download_media(self, msg, file=None):
        with open(file, "wb") as fh:
            fh.write(b"x" * self.partial)
        return file


class MustNotDownload:
    async def download_media(self, msg, file=None):
        raise AssertionError("download_media called — valid cache must be reused")


def run(client, msg, asset_dir):
    return asyncio.run(mod.media_element(client, msg, 111, asset_dir, 0))


SIZE = 1024
DEST_REL = os.path.join("tg", "111", "1.mp4")

# 1. THE BUG: a truncated cache file (smaller than Telegram's advertised size)
#    must be re-downloaded, not reused.
tmp = tempfile.mkdtemp()
try:
    dest = os.path.join(tmp, DEST_REL)
    os.makedirs(os.path.dirname(dest))
    with open(dest, "wb") as fh:
        fh.write(b"x" * 100)                      # truncated leftover
    client = FullWriter(SIZE)
    el = run(client, FakeMsg(1, SIZE), tmp)
    check_true("truncated cache file is re-downloaded",
               client.calls == 1, "calls=%d" % client.calls)
    check_true("re-download replaces the truncated file with the full one",
               os.path.getsize(dest) == SIZE,
               "size=%d" % os.path.getsize(dest))
    check_true("marker still emitted after re-download",
               el is not None and "<video" in el, repr(el))
finally:
    shutil.rmtree(tmp)

# 2. An interrupted download (exception mid-write) must not leave a file at
#    dest — the cache must hold either the complete file or nothing.
tmp = tempfile.mkdtemp()
try:
    dest = os.path.join(tmp, DEST_REL)
    raised = False
    try:
        run(Interrupter(100), FakeMsg(1, SIZE), tmp)
    except ConnectionError:
        raised = True
    check_true("interrupted download propagates the error", raised)
    check_true("interrupted download leaves nothing at dest",
               not os.path.exists(dest))
finally:
    shutil.rmtree(tmp)

# 3. A download that 'succeeds' with fewer bytes than advertised must fail the
#    cycle (so srr retries) instead of publishing a truncated file.
tmp = tempfile.mkdtemp()
try:
    dest = os.path.join(tmp, DEST_REL)
    raised = False
    try:
        run(ShortWriter(100), FakeMsg(1, SIZE), tmp)
    except mod.PartialDownload:
        raised = True
    check_true("short download raises PartialDownload", raised)
    check_true("short download leaves nothing at dest",
               not os.path.exists(dest))
finally:
    shutil.rmtree(tmp)

# 4. A complete cached file (exact advertised size) is reused without any
#    network call — and reuse refreshes its mtime, so srr's age-based cache
#    sweep never deletes a file a live feed still consumes.
tmp = tempfile.mkdtemp()
try:
    dest = os.path.join(tmp, DEST_REL)
    os.makedirs(os.path.dirname(dest))
    with open(dest, "wb") as fh:
        fh.write(b"x" * SIZE)
    stale = time.time() - 100 * 3600
    os.utime(dest, (stale, stale))
    el = run(MustNotDownload(), FakeMsg(1, SIZE), tmp)
    check_true("complete cache file is reused", el is not None and "<video" in el,
               repr(el))
    check_true("cache reuse refreshes mtime",
               time.time() - os.path.getmtime(dest) < 60,
               "mtime age %.0fs" % (time.time() - os.path.getmtime(dest)))
finally:
    shutil.rmtree(tmp)

# 5. Happy path: nothing cached, full download lands at dest, marker emitted.
tmp = tempfile.mkdtemp()
try:
    dest = os.path.join(tmp, DEST_REL)
    client = FullWriter(SIZE)
    el = run(client, FakeMsg(1, SIZE), tmp)
    check_true("fresh download emits marker", el is not None and "<video" in el,
               repr(el))
    check_true("fresh download lands complete at dest",
               os.path.exists(dest) and os.path.getsize(dest) == SIZE)
    check_true("no .part debris after a clean download",
               not os.path.exists(dest + ".part"))
finally:
    shutil.rmtree(tmp)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
