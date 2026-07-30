"""Tests for srr-tts's CLI parsing, voice resolution, and run_item flow
(synthesis monkeypatched — no model downloads, no onnxruntime inference).

    uv run tests/test_tts_cli.py
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-tts")

loader = importlib.machinery.SourceFileLoader("srr_tts", SCRIPT)
spec = importlib.util.spec_from_loader("srr_tts", loader)
tts = importlib.util.module_from_spec(spec)
loader.exec_module(tts)

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


def fake_synthesize(voice_obj, texts, dest):
    """New contract: texts is the kept-segment list; returns their start
    offsets. 2 seconds per segment keeps the fake table predictable."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with wave.open(dest, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        for _ in texts:
            w.writeframes(b"\x00\x00" * 44100)
    return [round(i * 2.0, 2) for i in range(len(texts))]


# --- parse_argv -----------------------------------------------------------
os.environ.pop("SRR_ASSET_DIR", None)
cfg, item = tts.parse_argv([])
check("protocol mode: no item from argv", item, None)
check("asset dir defaults empty without env", cfg["asset_dir"], "")
check("default max chars", cfg["max_chars"], tts.DEFAULT_MAX_CHARS)
check("default cap: the 32k backstop", tts.DEFAULT_MAX_CHARS, 32_768)

os.environ["SRR_ASSET_DIR"] = "/tmp/x"
cfg, _ = tts.parse_argv([])
check("asset dir from SRR_ASSET_DIR", cfg["asset_dir"], "/tmp/x")
cfg, _ = tts.parse_argv(["--asset-dir", "/tmp/y"])
check("--asset-dir overrides env", cfg["asset_dir"], "/tmp/y")
os.environ.pop("SRR_ASSET_DIR", None)

cfg, _ = tts.parse_argv(["--lang-voice", "ca=ca_ES-upc_ona-medium",
                         "--lang-voice", "en=en_GB-alba-medium"])
check("lang-voice extends", cfg["lang_voices"]["ca"], "ca_ES-upc_ona-medium")
check("lang-voice overrides", cfg["lang_voices"]["en"], "en_GB-alba-medium")
check_true("built-in table untouched for other keys",
           cfg["lang_voices"]["es"] == tts.LANG_VOICES["es"])

# Manual mode builds the same item dict srr would send (same-request rule).
with tempfile.TemporaryDirectory() as d:
    page = os.path.join(d, "a.html")
    with open(page, "w") as fh:
        fh.write("<p>Hola.</p>")
    cfg, item = tts.parse_argv(["--title", "T", "--lang", "es", page])
    check("manual item shape",
          {k: item[k] for k in ("title", "content", "link", "published", "lang")},
          {"title": "T", "content": "<p>Hola.</p>", "link": "",
           "published": None, "lang": "es"})
    check("manual guid = fnv1a32(content)", item["guid"], tts.fnv1a32("<p>Hola.</p>"))


# A bad flag must behave differently per mode. Manual run: exit non-zero, a
# human is reading. Protocol mode: NEVER exit non-zero — srr drops an item
# whose step fails and never retries it, and it does not validate external
# steps up front, so one typo in a feed's `pipe` would otherwise destroy every
# article that feed publishes.
def protocol_error(argv):
    cfg, item = tts.parse_argv(argv)      # must not raise
    return cfg["error"]


def manual_exits(argv, file_first=True):
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join(d, "a.html")
        with open(page, "w") as fh:
            fh.write("<p>x</p>")
        # file FIRST by default: a trailing flag missing its value would
        # otherwise swallow it as the value.
        full = [page] + argv if file_first else argv + [page]
        try:
            tts.parse_argv(full)
            return False
        except SystemExit:
            return True


for name, argv in [("--lang-voice without =", ["--lang-voice", "noequals"]),
                   ("unknown option", ["--bogus"]),
                   ("--max-chars non-integer", ["--max-chars", "abc"]),
                   ("--max-chars negative", ["--max-chars", "-1"]),
                   ("missing flag value", ["--voice"]),
                   # piper rejects a bad voice name only when it downloads, and
                   # strips whitespace before rebuilding the filename — so a
                   # padded name downloads 60 MB and then can't find it, per
                   # item, forever. Both must be caught at parse time.
                   ("malformed voice name", ["--voice", "lessac"]),
                   ("voice name with whitespace",
                    ["--voice", " en_US-lessac-medium"]),
                   ("--lang-voice's voice", ["--lang-voice", "es=nonsense"])]:
    check_true("%s: reported, not raised, in protocol mode" % name,
               bool(protocol_error(argv)), argv)
    check_true("%s: exits in a manual run" % name, manual_exits(argv), argv)

cfg, _ = tts.parse_argv(["--voice", "en_US-lessac-medium"])
check("well-formed voice accepted", cfg["voice"], "en_US-lessac-medium")
check("clean argv sets no error", cfg["error"], "")

# Mode is decided by the file OPENING, not by a positional existing. An
# unknown flag's orphaned value, and a stray token in a feed's `pipe`, both
# look exactly like filenames — mistaking either for a manual run means a
# non-zero exit on the fetch path, which permanently drops the article.
check_true("unknown flag's orphaned value is not mistaken for a file",
           bool(protocol_error(["--voce", "es_ES-davefx-medium"])))
check_true("stray token after a good flag is not mistaken for a file",
           bool(protocol_error(["--voice", "es_ES-davefx-medium", "stray"])))
check_true("unreadable file is fetch-safe, not a hard exit",
           "cannot read" in protocol_error(["/nonexistent/a.html"]))
# The natural CLI ordering: the typo'd flag's orphaned value precedes the real
# filename in the positional list, so "first positional" is not good enough.
check_true("manual run still exits when the file comes after a typo'd flag",
           manual_exits(["--voce", "es_ES-davefx-medium"], file_first=False))

# When several tokens look like files and none opens, the reported error must
# pair the file it names (paths[0]) with that file's OWN errno, not a later
# candidate's.
msg = protocol_error(["/nonexistent/a.html", "/nonexistent/b.html"])
check_true("multi-candidate open failure pairs message with its own file",
           "a.html" in msg and "b.html" not in msg, msg)

# The invariant behind all of the above, brute-forced rather than sampled:
# NOTHING on the command line may cause a non-zero exit (or an uncaught
# crash) unless a readable file was genuinely supplied, because on the fetch
# path a non-zero exit costs the article permanently.
import itertools

FUZZ_TOKENS = ["--voice", "--voce", "--lang-voice", "--max-chars", "--asset-dir",
               "--title", "--lang", "--bogus", "-", "", "--help", "help",
               "en_US-lessac-medium", " en_US-lessac-medium", "lessac",
               "es=es_ES-davefx-medium", "noequals", "abc", "-1", "3000",
               "stray", "/nonexistent/a.html"]
with tempfile.TemporaryDirectory() as d:
    real = os.path.join(d, "a.html")
    with open(real, "w") as fh:
        fh.write("<p>x</p>")
    violations, tested = [], 0
    for r in (1, 2, 3):
        for combo in itertools.product(FUZZ_TOKENS + [real], repeat=r):
            argv = list(combo)
            tested += 1
            try:
                tts.parse_argv(argv)
            except SystemExit:
                if real not in argv:
                    violations.append(argv)
            except Exception as e:                      # noqa: BLE001
                violations.append((argv, repr(e)))
    check("no argv exits non-zero or crashes without a readable file "
          "(%d combinations)" % tested, violations[:5], [])

# The built-in table must satisfy the same grammar --voice does, and stay on
# one quality tier: the size/timing figures documented for --max-chars assume
# a medium voice, and an x_low entry silently halves the sample rate.
check("table keys are bare primary subtags",
      [k for k in tts.LANG_VOICES if k != tts.norm_lang(k)], [])
check("every table voice is well-formed",
      [v for v in tts.LANG_VOICES.values() if not tts.VOICE_RE.match(v)], [])
check("every table voice is medium quality",
      [v for v in tts.LANG_VOICES.values() if not v.endswith("-medium")], [])

# --- resolve_voice --------------------------------------------------------
cfg, _ = tts.parse_argv(["--voice", "en_US-onyx-medium"])
check("--voice wins", tts.resolve_voice(cfg, "es"), "en_US-onyx-medium")
cfg, _ = tts.parse_argv([])
check("language via table", tts.resolve_voice(cfg, "es"), tts.LANG_VOICES["es"])
check("unknown language -> None", tts.resolve_voice(cfg, "zz"), None)
check("no language -> None", tts.resolve_voice(cfg, ""), None)

# srr's own detection emits bare codes, but the wire protocol lets an ingest
# strategy DECLARE lang (an RSS <language> verbatim) and srr never folds that
# onto the item — so a declared "pt-BR"/"ES" must still hit the table.
check("declared 'pt-BR' folds to pt", tts.resolve_voice(cfg, "pt-BR"),
      tts.LANG_VOICES["pt"])
check("declared 'ES' folds to es", tts.resolve_voice(cfg, "ES"),
      tts.LANG_VOICES["es"])
check("declared 'en_GB' folds to en", tts.resolve_voice(cfg, "en_GB"),
      tts.LANG_VOICES["en"])
cfg2, _ = tts.parse_argv(["--lang-voice", "pt-BR=pt_BR-cadu-medium"])
check("--lang-voice keys fold too", tts.resolve_voice(cfg2, "pt"),
      "pt_BR-cadu-medium")


# --- synthesize (the real function — atomic-write path) --------------------
class FakeVoice:
    class config:
        sample_rate = 22050

    def synthesize(self, text):
        class Chunk:
            audio_int16_bytes = b"\x00\x00" * 22050  # 1s per segment
        yield Chunk()


with tempfile.TemporaryDirectory() as d:
    dest = os.path.join(d, "tts", "x.wav")
    starts = tts.synthesize(FakeVoice(), ["a", "b", "c"], dest)
    check("synthesize: one start per segment, exact frame math",
          starts, [0.0, 1.0, 2.0])
    check_true("synthesize: file exists non-empty",
               os.path.getsize(dest) > 0)
    leftover = [f for f in os.listdir(os.path.dirname(dest))
                if f.endswith(".part")]
    check_true("synthesize: no leftover .part files", leftover == [], leftover)

# --- split_kept -------------------------------------------------------------
check("split_kept: cap=0 keeps every segment whole",
      tts.split_kept(["one", "two", "three"], 0), ["one", "two", "three"])
check("split_kept: tiny cap hard-cuts, dropping segments the cut can't reach",
      tts.split_kept(["Hello there.", "Another one."], 5), ["Hello"])
check("split_kept: cap landing on a sentence boundary partial-tails the "
      "segment it lands in, dropping the rest",
      tts.split_kept(["First sentence here.",
                      "Second sentence follows. Third here."], 46),
      ["First sentence here.", "Second sentence follows."])

# --- _tts_attr ----------------------------------------------------------------
check("_tts_attr: renders the table, trailing zeros dropped",
      tts._tts_attr([0.0, 2.0, 11.8]), ' data-tts-t="0,2,11.8"')

# --- ensure_voice (staged download; piper faked out of sys.modules) --------
# A voice is model + config, and piper's downloader skips any file that merely
# exists and is non-empty — so a half-downloaded model must not be left in
# place, or it poisons the cache forever.
import sys
import types

downloads = []

# Mirror the real piper downloader's two name behaviours, or the fake is more
# permissive than production and the tests below prove nothing: it strips the
# name, and REBUILDS the filename from the regex groups rather than using the
# string it was given.
VOICE_PATTERN = re.compile(
    r"^(?P<lang_family>[^-]+)_(?P<lang_region>[^-]+)-(?P<voice_name>[^-]+)-"
    r"(?P<voice_quality>.+)$")


def fake_download_voice(voice, download_dir, force_redownload=False):
    voice = voice.strip()
    m = VOICE_PATTERN.match(voice)
    if not m:
        raise ValueError("Voice %r did not match pattern" % voice)
    code = "%s_%s-%s-%s" % (m.group("lang_family"), m.group("lang_region"),
                            m.group("voice_name"), m.group("voice_quality"))
    downloads.append(code)
    for suffix in (".onnx", ".onnx.json"):
        with open(os.path.join(str(download_dir), code + suffix), "wb") as fh:
            fh.write(b"model-bytes")


fake_piper = types.ModuleType("piper")
fake_piper.PiperVoice = type("PiperVoice", (), {
    "load": staticmethod(lambda path, *a, **k: ("loaded", path))})
fake_dl = types.ModuleType("piper.download_voices")
fake_dl.download_voice = fake_download_voice
saved_mods = {k: sys.modules.get(k) for k in ("piper", "piper.download_voices")}
sys.modules["piper"] = fake_piper
sys.modules["piper.download_voices"] = fake_dl
try:
    with tempfile.TemporaryDirectory() as vd:
        onnx = os.path.join(vd, "en_US-lessac-medium.onnx")

        del downloads[:]
        tts.ensure_voice("en_US-lessac-medium", vd)
        check("first use downloads", downloads, ["en_US-lessac-medium"])
        check_true("both voice files land",
                   os.path.exists(onnx) and os.path.exists(onnx + ".json"))
        check_true("no staging dir left behind",
                   [n for n in os.listdir(vd) if n.startswith(".dl-")] == [],
                   os.listdir(vd))

        del downloads[:]
        tts.ensure_voice("en_US-lessac-medium", vd)
        check("complete voice is not re-downloaded", downloads, [])

        # Model present but config missing (a killed download): must redownload
        # rather than hand a half-voice to PiperVoice.load forever.
        os.remove(onnx + ".json")
        del downloads[:]
        tts.ensure_voice("en_US-lessac-medium", vd)
        check("missing config triggers re-download", downloads, ["en_US-lessac-medium"])
        check_true("config restored", os.path.exists(onnx + ".json"))

        # A download that dies mid-flight leaves the previous state intact.
        def dying_download(voice, download_dir, force_redownload=False):
            with open(os.path.join(str(download_dir), voice + ".onnx"), "wb") as fh:
                fh.write(b"trunc")
            raise OSError("connection reset")

        fake_dl.download_voice = dying_download
        os.remove(onnx)
        os.remove(onnx + ".json")
        try:
            tts.ensure_voice("en_US-lessac-medium", vd)
            check_true("failed download raises", False)
        except OSError:
            check_true("failed download raises", True)
        check_true("no truncated model left in place", not os.path.exists(onnx),
                   os.listdir(vd))
        check_true("no staging dir left after failure",
                   [n for n in os.listdir(vd) if n.startswith(".dl-")] == [],
                   os.listdir(vd))
        fake_dl.download_voice = fake_download_voice

        # Present-but-corrupt files can't be spotted by stat: a load failure
        # on a cached voice must heal via one forced re-download, or the
        # voice is wedged for every future cycle.
        tts.ensure_voice("en_US-lessac-medium", vd)          # populate the cache
        loads = []

        def picky_load(path, *a, **k):
            loads.append(path)
            with open(path, "rb") as fh:
                if fh.read() != b"model-bytes":
                    raise ValueError("corrupt model")
            return ("loaded", path)

        fake_piper.PiperVoice.load = staticmethod(picky_load)

        def corrupt(age_seconds):
            """Break the cached model and age it, since the heal is gated on
            the model's own mtime."""
            with open(onnx, "wb") as fh:
                fh.write(b"corrupt")
            old = time.time() - age_seconds
            os.utime(onnx, (old, old))

        corrupt(tts.HEAL_COOLDOWN + 60)
        del downloads[:]
        got = tts.ensure_voice("en_US-lessac-medium", vd)
        check("corrupt cached voice is re-downloaded", downloads, ["en_US-lessac-medium"])
        check("...and then loads", got, ("loaded", onnx))
        check_true("load retried exactly once", len(loads) == 2, loads)

        # The heal must NOT fire for a model touched within the cooldown: a
        # load failure is not proof of corruption (OOM fails the same way),
        # and srr runs one process per item, so an ungated heal would pull
        # 60 MB for every item of the feed.
        corrupt(0)
        del downloads[:]
        try:
            tts.ensure_voice("en_US-lessac-medium", vd)
            check_true("recently-fetched broken model raises", False)
        except ValueError:
            check_true("recently-fetched broken model raises", True)
        check("no re-download inside the heal cooldown", downloads, [])

        # A voice broken at the SOURCE must not loop either: the re-download
        # refreshes mtime, so the next item is inside the cooldown.
        def bad_download(voice, download_dir, force_redownload=False):
            downloads.append(voice)
            for suffix in (".onnx", ".onnx.json"):
                with open(os.path.join(str(download_dir), voice + suffix), "wb") as fh:
                    fh.write(b"still-corrupt")

        fake_dl.download_voice = bad_download
        corrupt(tts.HEAL_COOLDOWN + 60)
        del downloads[:]
        try:
            tts.ensure_voice("en_US-lessac-medium", vd)
            check_true("still-broken after heal raises", False)
        except ValueError:
            check_true("still-broken after heal raises", True)
        check("heal downloads once", downloads, ["en_US-lessac-medium"])
        del downloads[:]
        try:
            tts.ensure_voice("en_US-lessac-medium", vd)
        except ValueError:
            pass
        check("next item does not re-download", downloads, [])
        fake_dl.download_voice = fake_download_voice
        fake_piper.PiperVoice.load = staticmethod(lambda path, *a, **k: ("loaded", path))

        # Staging dirs an interrupted run leaked are reclaimed once stale —
        # voices_dir sits outside srr's age-swept cache, so nothing else would.
        fresh_stage = os.path.join(vd, ".dl-live")
        stale_stage = os.path.join(vd, ".dl-old")
        for p in (fresh_stage, stale_stage):
            os.makedirs(p, exist_ok=True)
        old = time.time() - tts.STAGE_TTL - 60
        os.utime(stale_stage, (old, old))
        os.remove(onnx)
        os.remove(onnx + ".json")
        tts.ensure_voice("en_US-lessac-medium", vd)
        check_true("stale staging dir swept", not os.path.exists(stale_stage))
        check_true("a concurrent worker's live staging dir is left alone",
                   os.path.exists(fresh_stage))

        # The kill-then-succeed sequence: debris from a killed download is
        # still younger than STAGE_TTL when the retry succeeds minutes later,
        # and once the voice is complete a download-path-only sweep never runs
        # again — the ~60 MB leak would be permanent. So the sweep must run on
        # EVERY load, not just ahead of a download.
        settled_stage = os.path.join(vd, ".dl-settled")
        os.makedirs(settled_stage, exist_ok=True)
        os.utime(settled_stage, (old, old))
        del downloads[:]
        tts.ensure_voice("en_US-lessac-medium", vd)
        check("complete voice still not re-downloaded", downloads, [])
        check_true("stale staging debris swept even without a download",
                   not os.path.exists(settled_stage))
finally:
    for k, v in saved_mods.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v

# --- run_item -------------------------------------------------------------
BASE = {"guid": 42, "title": "T", "content": "<p>Hola mundo.</p>",
        "link": "https://e.com/a", "published": "2026-07-19T00:00:00Z",
        "lang": "es", "raw": {"x": [1]}}

# No asset dir (preview / old backend): untouched.
cfg, _ = tts.parse_argv([])
item = dict(BASE)
check("no asset dir -> pass through", tts.run_item(item, cfg), BASE)

with tempfile.TemporaryDirectory() as store:
    orig_ensure_voice = tts.ensure_voice
    orig_synthesize = tts.synthesize
    try:
        # No resolvable voice: untouched.
        cfg, _ = tts.parse_argv(["--asset-dir", store])
        item = dict(BASE, lang="zz")
        check("no voice -> pass through", tts.run_item(item, cfg),
              dict(BASE, lang="zz"))

        # Empty text: untouched.
        item = dict(BASE, title="", content="<p>  </p>")
        check("empty text -> pass through", tts.run_item(item, cfg),
              dict(BASE, title="", content="<p>  </p>"))

        # Synthesis failure: untouched (never fail the cycle).
        tts.ensure_voice = lambda v, d: (_ for _ in ()).throw(RuntimeError("boom"))
        item = dict(BASE)
        check("synthesis failure -> pass through", tts.run_item(item, cfg), BASE)

        # Success: audio prepended with the timing table, blocks stamped,
        # wav + sidecar landed, other fields echoed verbatim.
        tts.ensure_voice = lambda v, d: object()
        tts.synthesize = fake_synthesize
        item = dict(BASE)
        out = tts.run_item(item, cfg)
        text = tts.extract_text(BASE["title"], BASE["content"])
        rel = tts.audio_name(tts.LANG_VOICES["es"], text)
        # Segments: title (0) + one <p> (1) -> table "0,2" from the fake.
        want_prefix = tts.AUDIO_HTML % (' data-tts-t="0,2"', "#/" + rel)
        check_true("audio tag prepended with table",
                   out["content"].startswith(want_prefix), out["content"])
        check_true("block stamped with its segment index",
                   '<p data-tts="1">Hola mundo.</p>' in out["content"],
                   out["content"])
        check_true("wav file landed",
                   os.path.getsize(os.path.join(store, rel)) > 0)
        with open(os.path.join(store, rel + ".json")) as fh:
            check("sidecar holds the table", json.load(fh),
                  {"v": 1, "starts": [0.0, 2.0]})
        for k in ("guid", "title", "link", "published", "lang", "raw"):
            check("field %s echoed" % k, out[k], BASE[k])

        # Cache hit: both files reused, mtimes refreshed, no synthesis.
        dest = os.path.join(store, rel)
        os.utime(dest, (1, 1))
        os.utime(dest + ".json", (1, 1))
        def explode(*a):
            raise AssertionError("synthesize called on a cache hit")
        tts.synthesize = explode
        out2 = tts.run_item(dict(BASE), cfg)
        check("cache hit -> same content", out2["content"], out["content"])
        check_true("cache hit refreshes wav mtime", os.path.getmtime(dest) > 1)
        check_true("cache hit refreshes sidecar mtime",
                   os.path.getmtime(dest + ".json") > 1)

        # Pre-upgrade cache: wav present, no sidecar -> plain tag (no table,
        # no stamps), and NEVER a re-synthesis.
        os.remove(dest + ".json")
        out3 = tts.run_item(dict(BASE), cfg)
        check_true("sidecar-less hit degrades to a plain tag",
                   out3["content"].startswith(
                       tts.AUDIO_HTML % ("", "#/" + rel)), out3["content"])
        check_true("sidecar-less hit keeps original content verbatim",
                   out3["content"].endswith(BASE["content"]))

        # No title: segment 0 is the first block, so it takes data-tts="0".
        tts.synthesize = fake_synthesize
        item = dict(BASE, title="", content="<p>Uno.</p><p>Dos.</p>")
        out4 = tts.run_item(item, cfg)
        check_true("titleless: first block is segment 0",
                   '<p data-tts="0">Uno.</p><p data-tts="1">Dos.</p>'
                   in out4["content"], out4["content"])

        # Regression guard: segment_html must hand back a PRISTINE soup —
        # run_item re-serializes it as the stored article. A <br> must
        # survive as itself (not collapsed to a literal newline) and a
        # DROP_TAGS player must survive whole (not decomposed away), even
        # though both are excluded from narration/stamping.
        item = dict(BASE, content='<p>line one<br>line two</p>'
                    '<video src="https://e.com/v.mp4" controls></video>')
        out5 = tts.run_item(item, cfg)
        check("regression: br survives, video survives, block stamped",
              out5["content"],
              '<p><audio controls preload="none" data-tts-t="0,2" '
              'src="#/' + tts.audio_name(tts.LANG_VOICES["es"],
                  "T\n\nline one\nline two") + '"></audio></p>'
              '<p data-tts="1">line one<br/>line two</p>'
              '<video controls="" src="https://e.com/v.mp4"></video>')

        # Feed-supplied data-tts/data-tts-t must never survive into the
        # reply — the reader must only ever see OUR table, or a stale/
        # misleading one from the source feed could misdirect it. Distinct
        # wording from every earlier case in this block, so the cache key
        # is fresh and this exercises the real synthesis+stamp path (not a
        # cache hit left in whatever state an earlier case put it in).
        item = dict(BASE, content='<p data-tts="99" data-tts-t="1,2,3">'
                    'Something else distinctly worded.</p>')
        out6 = tts.run_item(item, cfg)
        check_true("feed-supplied data-tts is swept before restamping",
                   'data-tts="99"' not in out6["content"], out6["content"])
        check_true("feed-supplied data-tts-t on a content element is swept",
                   'data-tts-t="1,2,3"' not in out6["content"], out6["content"])
        check_true("...and restamped with our own index",
                   '<p data-tts="1">Something else distinctly worded.</p>'
                   in out6["content"], out6["content"])
    finally:
        tts.ensure_voice = orig_ensure_voice
        tts.synthesize = orig_synthesize

# --- end-to-end: a misconfigured feed must never lose articles -------------
# The point of the mode split above, exercised through a real process. srr
# reads a non-zero exit as "drop this item, permanently" and empty stdout as
# "leave it unchanged".
import subprocess

ITEM = json.dumps({"guid": 1, "title": "T", "content": "<p>Hi.</p>",
                   "link": "", "published": None, "lang": "en"})
BAD_ENV = dict(os.environ, SRR_ASSET_DIR="/nonexistent")

proc = subprocess.run([sys.executable, SCRIPT, "--voice", "bogus"],
                      input=ITEM, capture_output=True, text=True, env=BAD_ENV)
check("bad --voice: exit 0 (non-zero would drop the article)",
      proc.returncode, 0)
check("bad --voice: empty stdout (srr keeps the item unchanged)",
      proc.stdout.strip(), "")
check_true("bad --voice: says why on stderr",
           "not a piper voice name" in proc.stderr, proc.stderr)

proc = subprocess.run([sys.executable, SCRIPT], input="{not json",
                      capture_output=True, text=True, env=BAD_ENV)
check("malformed stdin: exit 0", proc.returncode, 0)
check("malformed stdin: empty stdout", proc.stdout.strip(), "")

# The shape a real misconfigured `pipe` takes: a typo'd flag that still has a
# value. The orphaned value must not be read as a filename.
proc = subprocess.run([sys.executable, SCRIPT, "--voce", "es_ES-davefx-medium"],
                      input=ITEM, capture_output=True, text=True, env=BAD_ENV)
check("typo'd flag with a value: exit 0", proc.returncode, 0)
check("typo'd flag with a value: empty stdout", proc.stdout.strip(), "")
check_true("typo'd flag with a value: names the bad flag",
           "--voce" in proc.stderr, proc.stderr)

# No asset dir (preview / older backend): item echoed verbatim, exit 0.
env = dict(os.environ)
env.pop("SRR_ASSET_DIR", None)
proc = subprocess.run([sys.executable, SCRIPT], input=ITEM,
                      capture_output=True, text=True, env=env)
check("no asset dir: exit 0", proc.returncode, 0)
check("no asset dir: item echoed unchanged", json.loads(proc.stdout),
      json.loads(ITEM))

# Help/version are argv[0] conveniences, but stdout is the protocol channel:
# their text is not JSON, so on the fetch path it would fail srr's parse and
# drop the article as surely as a non-zero exit would. Both must report on
# stderr and leave stdout empty (= item unchanged).
proc = subprocess.run([sys.executable, SCRIPT, "--help"], input=ITEM,
                      capture_output=True, text=True, env=BAD_ENV)
check("--help: exit 0", proc.returncode, 0)
check("--help: empty stdout (srr keeps the item unchanged)",
      proc.stdout.strip(), "")
check_true("--help: documentation on stderr", "VOICE RESOLUTION" in proc.stderr,
           proc.stderr[:200])

proc = subprocess.run([sys.executable, SCRIPT, "--version"], input=ITEM,
                      capture_output=True, text=True, env=BAD_ENV)
check("--version: exit 0", proc.returncode, 0)
check("--version: empty stdout", proc.stdout.strip(), "")
check_true("--version: version on stderr",
           ("srr-tts %s" % tts.VERSION) in proc.stderr, proc.stderr)

# srr's Go encoder only ever emits valid UTF-8, but the decode must not be at
# the mercy of the service unit's locale: invalid bytes raise
# UnicodeDecodeError — a ValueError, NOT a JSONDecodeError — and an uncaught
# one exits non-zero, which costs the article.
proc = subprocess.run([sys.executable, SCRIPT],
                      input=b'{"guid": 1, "title": "\xff\xfe"}',
                      capture_output=True, env=BAD_ENV)
check("invalid UTF-8 on stdin: exit 0", proc.returncode, 0)
check("invalid UTF-8 on stdin: empty stdout", proc.stdout.strip(), b"")

# `raw` is srr's own payload: it saves and restores its typed copy around the
# reply, so echoing the key back is discarded work — the whole feed entry
# re-encoded per item. It must not be in the reply at all.
proc = subprocess.run([sys.executable, SCRIPT],
                      input=json.dumps(dict(json.loads(ITEM),
                                            raw={"+": [{"@": "x"}]})),
                      capture_output=True, text=True, env=env)
check("raw: exit 0", proc.returncode, 0)
check("raw is not echoed back (srr discards it either way)",
      json.loads(proc.stdout), json.loads(ITEM))

# srr ships the feed entry's XML tree as `raw` with NO depth cap (Go's parser
# handles arbitrary nesting and models the tree as a recursively nested map),
# but Python's JSON scanner gives up near depth 500 — with RecursionError,
# which is NOT a ValueError, and an uncaught one costs the article. Dropping
# `raw` from the reply keeps that depth off the encode side entirely; the
# decode side still has to survive it. Whatever the depth, the reply must be
# all-or-nothing: exit 0 and either a full echo or empty stdout (= item
# unchanged). A non-zero exit or partial JSON on stdout both drop the article
# permanently.
for depth in (5, 496, 497, 5000):
    raw = '{"+":[' * depth + '"x"' + ']}' * depth
    deep = ('{"guid":1,"title":"T","content":"<p>Hi.</p>","link":"",'
            '"published":null,"raw":' + raw + "}")
    proc = subprocess.run([sys.executable, SCRIPT], input=deep,
                          capture_output=True, text=True, env=BAD_ENV)
    if proc.stdout:
        try:
            json.loads(proc.stdout)
            intact = True
        except ValueError:
            intact = False
    else:
        intact = True
    check("raw depth %d: exit 0" % depth, proc.returncode, 0)
    check_true("raw depth %d: stdout is a full echo or empty" % depth,
               intact, proc.stdout[:80])

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
