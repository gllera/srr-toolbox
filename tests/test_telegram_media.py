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

    async def download_media(self, msg, file=None, thumb=None):
        self.calls += 1
        with open(file, "wb") as fh:
            fh.write(b"x" * self.size)
        return file


class Interrupter:
    """Connection dies mid-download, partial bytes already written."""

    def __init__(self, partial):
        self.partial = partial

    async def download_media(self, msg, file=None, thumb=None):
        with open(file, "wb") as fh:
            fh.write(b"x" * self.partial)
        raise ConnectionError("simulated mid-download death")


class ShortWriter:
    """Returns 'success' but wrote fewer bytes than Telegram advertised."""

    def __init__(self, partial):
        self.partial = partial

    async def download_media(self, msg, file=None, thumb=None):
        with open(file, "wb") as fh:
            fh.write(b"x" * self.partial)
        return file


class MustNotDownload:
    async def download_media(self, msg, file=None, thumb=None):
        raise AssertionError("download_media called — valid cache must be reused")


class FakeImageFile:
    """A link preview's photo, exactly as Telethon's msg.file resolves it:
    image mime + the small advertised photo size."""

    def __init__(self, size):
        self.ext = ".jpg"
        self.mime_type = "image/jpeg"
        self.size = size
        self.name = "preview.jpg"


class FakeWebPageMsg:
    """A link-preview message: msg.media is MessageMediaWebPage while msg.file
    resolves to the preview photo (image/jpeg). This is the exact shape that
    wedged t.me/hispanmedia/10481 — 94 KB photo advertised, but download_media
    fetches the preview's 7.97 MB video."""

    def __init__(self):
        self.id = 10481
        self.sticker = None
        self.voice = None
        self.audio = None
        self.media = mod.types.MessageMediaWebPage(webpage=None)
        self.file = FakeImageFile(94007)


def run(client, msg, asset_dir):
    return asyncio.run(mod.media_element(client, msg, 111, asset_dir, 0,
                                         "https://t.me/c/111/%d" % msg.id))


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

# 6. A link-preview (WebPage) message is never self-hosted. Its msg.file looks
#    like an image, so the old image-mime branch classified it as a 94 KB photo
#    — but download_media(msg) fetches the preview's 7.97 MB video, so the size
#    check failed every cycle and wedged the whole feed. classify_media must
#    short-circuit WebPage media to None so no download is ever attempted.
info = mod.classify_media(FakeWebPageMsg())
check_true("WebPage link-preview is not classified as self-hosted media",
           info is None, "classify_media returned %r" % (info,))


class FakeDocFile:
    """A non-image/video document attachment (classify_media's file branch)."""

    def __init__(self, size, name="report.pdf"):
        self.ext = ".pdf"
        self.mime_type = "application/pdf"
        self.size = size
        self.name = name


class FakeDocMsg:
    def __init__(self, msg_id, size):
        self.id = msg_id
        self.file = FakeDocFile(size)
        self.media = None
        self.sticker = None
        self.voice = None
        self.audio = None
        self.video = None
        self.video_note = None
        self.gif = None


class FakeVoiceFile:
    """A voice note's file: opus-in-ogg, no filename."""

    def __init__(self, size):
        self.ext = ".oga"
        self.mime_type = "audio/ogg"
        self.size = size
        self.name = None


class FakeVoiceMsg(FakeDocMsg):
    def __init__(self, msg_id, size):
        super().__init__(msg_id, size)
        self.file = FakeVoiceFile(size)
        self.voice = True


class FakeStickerFile:
    def __init__(self, size):
        self.ext = ".webp"
        self.mime_type = "image/webp"
        self.size = size
        self.name = "sticker.webp"


class FakeStickerMsg(FakeDocMsg):
    def __init__(self, msg_id, size):
        super().__init__(msg_id, size)
        self.file = FakeStickerFile(size)
        self.sticker = True


class FakeMusicFile:
    def __init__(self, size):
        self.ext = ".mp3"
        self.mime_type = "audio/mpeg"
        self.size = size
        self.name = "song.mp3"


class FakeMusicMsg(FakeDocMsg):
    """A music track (msg.audio), distinct from a voice note."""

    def __init__(self, msg_id, size):
        super().__init__(msg_id, size)
        self.file = FakeMusicFile(size)
        self.audio = True


# 7. Document attachments are self-hosted like photos/videos: downloaded into
#    the store and linked in the article by filename.
tmp = tempfile.mkdtemp()
try:
    client = FullWriter(SIZE)
    el = run(client, FakeDocMsg(7, SIZE), tmp)
    dest = os.path.join(tmp, "tg", "111", "7.pdf")
    check_true("doc is self-hosted",
               client.calls == 1 and os.path.exists(dest)
               and os.path.getsize(dest) == SIZE)
    check_true("doc links the stored file by name",
               el is not None and "#/tg/111/7.pdf" in el and "report.pdf" in el,
               repr(el))
finally:
    shutil.rmtree(tmp)

# 8. Preview (no asset_dir): docs show the bare kind placeholder, like
#    photos/videos do — never a download.
el = asyncio.run(mod.media_element(MustNotDownload(), FakeDocMsg(7, SIZE), 111,
                                   "", 0, "https://t.me/c/111/7"))
check_true("doc in preview mode yields the file placeholder",
           el == "<p><em>[file]</em></p>", repr(el))

# 9. Over srr's size cap nothing is downloaded — but the skip note must keep
#    a link to the original post, so the attachment stays reachable.
tmp = tempfile.mkdtemp()
try:
    el = asyncio.run(mod.media_element(MustNotDownload(), FakeDocMsg(7, SIZE),
                                       111, tmp, SIZE - 1,
                                       "https://t.me/c/111/7"))
    check_true("over-cap skip note links the original post",
               el is not None and "too large" in el
               and 'href="https://t.me/c/111/7"' in el
               and "open in Telegram" in el, repr(el))
    check_true("over-cap doc writes nothing to the store",
               not os.listdir(tmp), os.listdir(tmp))
finally:
    shutil.rmtree(tmp)

# 10. Voice notes are self-hosted like videos and emitted as an <audio>
#     player (the srr sanitizer allowlists <audio src/controls/preload>).
tmp = tempfile.mkdtemp()
try:
    client = FullWriter(SIZE)
    el = run(client, FakeVoiceMsg(8, SIZE), tmp)
    dest = os.path.join(tmp, "tg", "111", "8.oga")
    check_true("voice note is self-hosted",
               client.calls == 1 and os.path.exists(dest)
               and os.path.getsize(dest) == SIZE)
    check_true("voice note emits an <audio> player",
               el is not None and "<audio" in el and "#/tg/111/8.oga" in el
               and "controls" in el, repr(el))
finally:
    shutil.rmtree(tmp)

# 11. Preview (no asset_dir): voice shows the bare kind placeholder, like
#     images/videos do.
el = asyncio.run(mod.media_element(MustNotDownload(), FakeVoiceMsg(8, SIZE), 111,
                                   "", 0, "https://t.me/c/111/8"))
check_true("voice in preview mode yields the audio placeholder",
           el == "<p><em>[audio]</em></p>", repr(el))

# 12. No attachment class is silently dropped — everything follows the same
#     download-or-placeholder logic: music tracks ride the <audio> player like
#     voice notes, and a (webp) sticker classifies as a plain image.
tmp = tempfile.mkdtemp()
try:
    client = FullWriter(SIZE)
    el = run(client, FakeMusicMsg(9, SIZE), tmp)
    check_true("music track is self-hosted into an <audio> player",
               client.calls == 1 and el is not None and "<audio" in el
               and "#/tg/111/9.mp3" in el, repr(el))
finally:
    shutil.rmtree(tmp)

tmp = tempfile.mkdtemp()
try:
    client = FullWriter(SIZE)
    el = run(client, FakeStickerMsg(10, SIZE), tmp)
    check_true("webp sticker is self-hosted as an image",
               client.calls == 1 and el is not None and "<img" in el
               and "#/tg/111/10.webp" in el, repr(el))
finally:
    shutil.rmtree(tmp)

# 13. …and they degrade like every other kind: preview placeholder without a
#     store, linked skip note over the cap.
el = asyncio.run(mod.media_element(MustNotDownload(), FakeMusicMsg(9, SIZE), 111,
                                   "", 0, "https://t.me/c/111/9"))
check_true("music in preview mode yields the audio placeholder",
           el == "<p><em>[audio]</em></p>", repr(el))
tmp = tempfile.mkdtemp()
try:
    el = asyncio.run(mod.media_element(MustNotDownload(), FakeStickerMsg(10, SIZE),
                                       111, tmp, SIZE - 1,
                                       "https://t.me/c/111/10"))
    check_true("over-cap sticker links the original post",
               el is not None and "too large" in el and "open in Telegram" in el,
               repr(el))
finally:
    shutil.rmtree(tmp)


# --- animated .tgs stickers ---------------------------------------------------

class FakeDoc:
    def __init__(self, thumbs):
        self.thumbs = thumbs


class FakeTgsFile:
    def __init__(self, size):
        self.ext = ".tgs"
        self.mime_type = "application/x-tgsticker"
        self.size = size
        self.name = "AnimatedSticker.tgs"


class FakeTgsMsg(FakeDocMsg):
    """Animated Lottie sticker; the downloadable content is the static thumb."""

    def __init__(self, msg_id, thumbs):
        super().__init__(msg_id, 30 * 1024)
        self.file = FakeTgsFile(30 * 1024)
        self.sticker = True
        self.document = FakeDoc(thumbs)


class ThumbWriter:
    """Serves the sticker's static thumbnail bytes; refuses a full download."""

    def __init__(self, size, head=b"RIFF\x00\x00\x00\x00WEBP"):
        self.size = size
        self.head = head
        self.thumbs_asked = []

    async def download_media(self, msg, file=None, thumb=None):
        assert thumb is not None, "sticker must download the thumb, not the .tgs"
        self.thumbs_asked.append(thumb)
        with open(file, "wb") as fh:
            fh.write((self.head + b"\x00" * self.size)[:self.size])
        return file


THUMB = mod.types.PhotoSize(type="m", w=128, h=128, size=512)

# 14. A .tgs sticker self-hosts its largest static thumbnail as an image —
#     never the Lottie file itself, which no browser renders.
tmp = tempfile.mkdtemp()
try:
    client = ThumbWriter(512)
    msg = FakeTgsMsg(11, [mod.types.PhotoSize(type="s", w=32, h=32, size=64),
                          THUMB])
    el = run(client, msg, tmp)
    dest = os.path.join(tmp, "tg", "111", "11.webp")
    check_true("tgs sticker downloads its largest static thumb",
               client.thumbs_asked == [THUMB], repr(client.thumbs_asked))
    check_true("tgs thumb lands complete in the store",
               os.path.exists(dest) and os.path.getsize(dest) == 512)
    check_true("tgs sticker emits an <img> of the thumb",
               el == '<p><img src="#/tg/111/11.webp" alt=""></p>', repr(el))
finally:
    shutil.rmtree(tmp)

# 15. The thumb's real format wins over the assumed one (Telegram serves webp,
#     jpeg or png depending on sticker age) — and the sniffed file is found
#     again as cache on the next cycle.
tmp = tempfile.mkdtemp()
try:
    msg = FakeTgsMsg(12, [THUMB])
    el = run(ThumbWriter(512, head=b"\xff\xd8"), msg, tmp)
    check_true("jpeg thumb is stored under the sniffed .jpg ext",
               os.path.exists(os.path.join(tmp, "tg", "111", "12.jpg"))
               and el is not None and "#/tg/111/12.jpg" in el, repr(el))
    el = run(MustNotDownload(), msg, tmp)
    check_true("sniffed thumb is reused as cache",
               el is not None and "#/tg/111/12.jpg" in el, repr(el))
finally:
    shutil.rmtree(tmp)

# 16. No downloadable static thumb (inline vector-path preview only): the
#     placeholder link, nothing downloaded. Preview shows the bare kind.
tmp = tempfile.mkdtemp()
try:
    msg = FakeTgsMsg(13, [mod.types.PhotoPathSize(type="j", bytes=b"x")])
    el = run(MustNotDownload(), msg, tmp)
    check_true("thumbless tgs degrades to the open-in-Telegram link",
               el is not None and "sticker" in el and "open in Telegram" in el
               and 'href="https://t.me/c/111/13"' in el, repr(el))
    check_true("thumbless tgs writes nothing to the store",
               not os.listdir(tmp), os.listdir(tmp))
finally:
    shutil.rmtree(tmp)
el = asyncio.run(mod.media_element(MustNotDownload(), FakeTgsMsg(13, [THUMB]),
                                   111, "", 0, "https://t.me/c/111/13"))
check_true("tgs in preview mode yields the sticker placeholder",
           el == "<p><em>[sticker]</em></p>", repr(el))

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
