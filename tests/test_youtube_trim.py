"""Unit tests for srr-youtube --trim: boilerplate is cut from descriptions
(separator, link/@handle/#tag paragraphs, call-to-action lines), summaries,
chapters and prose survive, and without the flag the output is unchanged.
The fixtures are invented but shaped like real channel descriptions.

Run in the repo's uv project venv (plain python3 works too):
    uv run tests/test_youtube_trim.py
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-youtube")

loader = importlib.machinery.SourceFileLoader("srr_youtube", SCRIPT)
spec = importlib.util.spec_from_loader("srr_youtube", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
mod.probing = False   # thumbnails hotlink the feed's own URL: no network

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


def trim(text):
    return mod.trim_description(text)


SUMMARY = "En este video repasamos como se forman las mareas y por que la Luna manda."

# 1. a one-line summary, then a long support/social dump (~80 words)
SUPPORT_DUMP = SUMMARY + """

Apoya el canal y ayudanos a seguir creando contenido:
Patreon: https://patreon.example.com/canal
PayPal: https://paypal.example.com/canal
Discord: https://discord.example.com/invite/abc
Tienda oficial (merch): https://shop.example.com
Instagram: https://instagram.example.com/canal
Twitter: https://twitter.example.com/canal
TikTok: https://tiktok.example.com/@canal
Facebook: https://facebook.example.com/canal
Web: https://canal.example.com
"""
check("support/social dump after a summary is cut", trim(SUPPORT_DUMP), SUMMARY)

# 2. a separator and everything after it
SEPARATED = SUMMARY + """

Mas informacion sobre el tema en el articulo de Example Site: https://example.com/mareas

━━━━━━━━━━━━━━━━━━━━
Este texto ya es promocion y no se debe ver.
Suscribete para mas videos.
"""
check("separator cuts the rest, prose with one link stays", trim(SEPARATED),
      SUMMARY + "\n\nMas informacion sobre el tema en el articulo de Example Site: https://example.com/mareas")
for sep in ("---", "___", "===", "***", "~~~", "════", "───", "——— ", " - - - "):
    check_true("separator %r" % sep, trim("Hola\n%s\nPromo" % sep) == "Hola", trim("Hola\n%s\nPromo" % sep))
check("two dashes are not a separator", trim("Hola\n--\nAdios"), "Hola\n--\nAdios")
check("mixed characters are not a separator", trim("Hola\n-_-\nAdios"), "Hola\n-_-\nAdios")
check("a timestamp line with a dash is not a separator",
      trim("0:00 - Intro\n1:30 - Fin"), "0:00 - Intro\n1:30 - Fin")

# 3. a channel whose every description is a social block
SEGUIME = SUMMARY + """

🚀 ¡SEGUIME EN MIS REDES!
https://instagram.example.com/canal
https://twitter.example.com/canal
https://tiktok.example.com/canal
"""
check("emoji call-to-action social block is cut", trim(SEGUIME), SUMMARY)
check("a lone call to action with one link is cut",
      trim(SUMMARY + "\n\n👉 Suscribite al canal https://youtube.example.com/c/canal"), SUMMARY)
check("a long sentence starting with an emoji is kept past 12 words",
      trim("🎬 " + " ".join(["palabra"] * 13) + " https://example.com/x"),
      "🎬 " + " ".join(["palabra"] * 13) + " https://example.com/x")

# 4. a hashtag line
HASHTAGS = SUMMARY + "\n\n#mareas #luna #fisica #ciencia #oceano @canal\n"
check("hashtag line is cut", trim(HASHTAGS), SUMMARY)
check("a hashtag inside prose is kept",
      trim("Hablamos de #mareas con calma y sin prisa."), "Hablamos de #mareas con calma y sin prisa.")

# 5. chapters are kept
CHAPTERS = SUMMARY + """

0:00 Introduccion
1:20 La Luna
12:45 El Sol
1:02:03 Conclusiones

Patreon: https://patreon.example.com/canal
"""
check("chapter list is kept", trim(CHAPTERS),
      SUMMARY + "\n\n0:00 Introduccion\n1:20 La Luna\n12:45 El Sol\n1:02:03 Conclusiones")
check("00:00-style chapters are kept", trim("00:00 Inicio\n05:10 Medio"), "00:00 Inicio\n05:10 Medio")
check("chapters made of links only are still kept",
      trim("0:00 https://example.com/a\n1:00 https://example.com/b"),
      "0:00 https://example.com/a\n1:00 https://example.com/b")

# 6. empty and degenerate descriptions
check("an all-boilerplate description is trimmed to nothing",
      trim("🚀 ¡SEGUIME EN MIS REDES!\nhttps://instagram.example.com/c\n\n#a #b"), "")
check("empty text", trim(""), "")
check("None", trim(None), "")
check("text before and only separator", trim("━━━━━\nPromo"), "")

# --- through the item builder, flag off and on --------------------------------

def feed_xml(desc):
    desc = desc.replace("&", "&amp;")
    return ("""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"
 xmlns:media="http://search.yahoo.com/mrss/">
<entry><id>yt:video:vid123</id><yt:videoId>vid123</yt:videoId><title>Mareas</title>
<published>2026-10-01T10:00:00+00:00</published>
<media:group><media:thumbnail url="https://i.ytimg.com/vi/vid123/hqdefault.jpg"/>
<media:description>%s</media:description></media:group></entry></feed>""" % desc).encode()


THUMB = ('<p><a href="https://www.youtube.com/watch?v=vid123">'
         '<img src="https://i.ytimg.com/vi/vid123/hqdefault.jpg" alt="Mareas"></a></p>')

mod.TRIM = False
off = mod.build_items(feed_xml(SUPPORT_DUMP), "", 0)[0]["content"]
check_true("without --trim the description is rendered whole",
           off == THUMB + mod.desc_html(SUPPORT_DUMP) and "patreon.example.com" in off, off)
# the pin: byte-identical to what the builder produced before --trim existed
check("without --trim, exact content for a small description",
      mod.build_items(feed_xml("Hola\n\n#a #b"), "", 0)[0]["content"],
      THUMB + "<p>Hola</p><p>#a #b</p>")

mod.TRIM = True
on = mod.build_items(feed_xml(SUPPORT_DUMP), "", 0)[0]["content"]
check("with --trim the dump is gone", on, THUMB + "<p>%s</p>" % SUMMARY)
empty = mod.build_items(feed_xml("🚀 ¡SEGUIME EN MIS REDES!\nhttps://instagram.example.com/c"), "", 0)[0]
check("an emptied description keeps the thumbnail, no paragraph", empty["content"], THUMB)
mod.TRIM = False

# --- the flag on the command line AND with the stdin request ------------------

def run_main(argv, stdin_req):
    saved = (sys.argv, sys.stdin, sys.stdout, mod.fetch_feed)
    out = io.StringIO()
    try:
        sys.argv = ["srr-youtube"] + argv
        sys.stdin = io.StringIO(json.dumps(stdin_req))
        sys.stdout = out
        mod.fetch_feed = lambda req: (feed_xml(SUPPORT_DUMP), None, None)
        mod.main()
    finally:
        sys.argv, sys.stdin, sys.stdout, mod.fetch_feed = saved
        mod.TRIM = False
    return json.loads(out.getvalue())


REQ = {"url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc"}
res = run_main(["--trim"], REQ)
check("--trim with the stdin request trims", res["items"][0]["content"], THUMB + "<p>%s</p>" % SUMMARY)
res = run_main([], REQ)
check_true("no flag with the stdin request keeps the dump",
           "patreon.example.com" in res["items"][0]["content"])
res = run_main(["--selfhost", "--trim"], REQ)
check("--selfhost --trim together", res["items"][0]["content"], THUMB + "<p>%s</p>" % SUMMARY)

saved = (sys.argv, sys.stdout, mod.fetch_feed)
try:
    sys.argv = ["srr-youtube", "--trim", REQ["url"]]
    sys.stdout = io.StringIO()
    mod.fetch_feed = lambda req: (feed_xml(SUPPORT_DUMP), None, None)
    mod.main()
    cli = json.loads(sys.stdout.getvalue())
finally:
    sys.argv, sys.stdout, mod.fetch_feed = saved
    mod.TRIM = False
check("--trim with a URL argument (manual run) trims too",
      cli["items"][0]["content"], THUMB + "<p>%s</p>" % SUMMARY)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
