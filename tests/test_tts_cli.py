"""Tests for srr-tts's CLI parsing, voice resolution, and run_item flow
(synthesis monkeypatched — no model downloads, no onnxruntime inference).

    uv run tests/test_tts_cli.py
"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
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


def fake_synthesize(voice_obj, text, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with wave.open(dest, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 100)


# --- parse_argv -----------------------------------------------------------
os.environ.pop("SRR_ASSET_DIR", None)
cfg, item = tts.parse_argv([])
check("protocol mode: no item from argv", item, None)
check("asset dir defaults empty without env", cfg["asset_dir"], "")
check("default max chars", cfg["max_chars"], tts.DEFAULT_MAX_CHARS)

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


def raises_systemexit(argv):
    try:
        tts.parse_argv(argv)
        return False
    except SystemExit:
        return True


check_true("--lang-voice without = raises SystemExit",
           raises_systemexit(["--lang-voice", "noequals"]))
check_true("unknown option raises SystemExit",
           raises_systemexit(["--bogus"]))
check_true("--max-chars non-integer raises SystemExit",
           raises_systemexit(["--max-chars", "abc"]))

# --- resolve_voice --------------------------------------------------------
cfg, _ = tts.parse_argv(["--voice", "xx_XX-explicit"])
check("--voice wins", tts.resolve_voice(cfg, "es"), "xx_XX-explicit")
cfg, _ = tts.parse_argv([])
check("language via table", tts.resolve_voice(cfg, "es"), tts.LANG_VOICES["es"])
check("unknown language -> None", tts.resolve_voice(cfg, "zz"), None)
check("no language -> None", tts.resolve_voice(cfg, ""), None)


# --- synthesize (the real function — atomic-write path) --------------------
class FakeVoice:
    def synthesize_wav(self, text, w):
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 50)


with tempfile.TemporaryDirectory() as d:
    dest = os.path.join(d, "out.wav")
    tts.synthesize(FakeVoice(), "x", dest)
    check_true("synthesize: file exists non-empty",
               os.path.exists(dest) and os.path.getsize(dest) > 0)
    leftover = [n for n in os.listdir(d) if ".part" in n]
    check_true("synthesize: no leftover .part files", leftover == [], leftover)

# --- ensure_voice (staged download; piper faked out of sys.modules) --------
# A voice is model + config, and piper's downloader skips any file that merely
# exists and is non-empty — so a half-downloaded model must not be left in
# place, or it poisons the cache forever.
import sys
import types

downloads = []


def fake_download_voice(voice, download_dir, force_redownload=False):
    downloads.append(voice)
    for suffix in (".onnx", ".onnx.json"):
        with open(os.path.join(str(download_dir), voice + suffix), "wb") as fh:
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
        onnx = os.path.join(vd, "xx_XX-test.onnx")

        del downloads[:]
        tts.ensure_voice("xx_XX-test", vd)
        check("first use downloads", downloads, ["xx_XX-test"])
        check_true("both voice files land",
                   os.path.exists(onnx) and os.path.exists(onnx + ".json"))
        check_true("no staging dir left behind",
                   [n for n in os.listdir(vd) if n.startswith(".dl-")] == [],
                   os.listdir(vd))

        del downloads[:]
        tts.ensure_voice("xx_XX-test", vd)
        check("complete voice is not re-downloaded", downloads, [])

        # Model present but config missing (a killed download): must redownload
        # rather than hand a half-voice to PiperVoice.load forever.
        os.remove(onnx + ".json")
        del downloads[:]
        tts.ensure_voice("xx_XX-test", vd)
        check("missing config triggers re-download", downloads, ["xx_XX-test"])
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
            tts.ensure_voice("xx_XX-test", vd)
            check_true("failed download raises", False)
        except OSError:
            check_true("failed download raises", True)
        check_true("no truncated model left in place", not os.path.exists(onnx),
                   os.listdir(vd))
        check_true("no staging dir left after failure",
                   [n for n in os.listdir(vd) if n.startswith(".dl-")] == [],
                   os.listdir(vd))
        fake_dl.download_voice = fake_download_voice
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

        # Success: audio prepended, wav landed, other fields echoed verbatim.
        tts.ensure_voice = lambda v, d: object()
        tts.synthesize = fake_synthesize
        item = dict(BASE)
        out = tts.run_item(item, cfg)
        text = tts.extract_text(BASE["title"], BASE["content"])
        rel = tts.audio_name(tts.LANG_VOICES["es"], text)
        want_prefix = tts.AUDIO_HTML % ("#/" + rel)
        check_true("audio tag prepended", out["content"].startswith(want_prefix),
                   out["content"])
        check_true("original content preserved after the tag",
                   out["content"].endswith(BASE["content"]))
        check_true("wav file landed", os.path.getsize(os.path.join(store, rel)) > 0)
        for k in ("guid", "title", "link", "published", "lang", "raw"):
            check("field %s echoed" % k, out[k], BASE[k])

        # Cache hit: file reused, mtime refreshed, synthesize NOT called again.
        dest = os.path.join(store, rel)
        os.utime(dest, (1, 1))
        def explode(*a):
            raise AssertionError("synthesize called on a cache hit")
        tts.synthesize = explode
        out2 = tts.run_item(dict(BASE), cfg)
        check("cache hit -> same content", out2["content"], out["content"])
        check_true("cache hit refreshes mtime", os.path.getmtime(dest) > 1)
    finally:
        tts.ensure_voice = orig_ensure_voice
        tts.synthesize = orig_synthesize

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
