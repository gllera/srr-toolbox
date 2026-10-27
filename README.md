# srr-toolbox

The ingest and pipeline toolbox for **drr** (and formerly **srr**, its static-RSS-reader
predecessor): `srr-*` scripts with the ingest strategies that teach the reader to
read sources that don't speak usable RSS, and two pipeline steps. (`drr-digest-gen`, a tool that
drove the `drr` CLI to write a daily digest, was removed: drr writes the digest itself.)

## Layout

```
bin/srr-telegram    ingest strategy: Telegram channel (incl. private) -> SRR items
bin/srr-youtube     ingest strategy: YouTube channel Atom feed -> SRR items
bin/srr-x           ingest strategy: X/Twitter account (via a Nitter instance) -> SRR items
bin/srr-sitemap     ingest strategy: a site's XML sitemap (no feed) -> SRR items
bin/srr-devto       ingest strategy: dev.to's top-of-the-month ranking -> SRR items
bin/srr-tts         pipeline step: prepend a piper TTS narration to the article
bin/srr-gtproxy     pipeline step: the whole page, read through Google Translate's site proxy
bin/srr-uvrun       shebang wrapper that runs the Python scripts in this repo's uv venv
tests/              self-checking test scripts + fixtures
pyproject.toml      shared dependencies for every bin/ script, pinned by uv.lock
```

Every `bin/` entry point must be reachable from `PATH` — `srr` resolves ingest
strategies by bare name, and `srr-uvrun` shebangs rely on it too. Symlinks into a
PATH dir work fine (`srr-uvrun` sees through them via `readlink -f`), as does
putting `bin/` on `PATH` directly. A missing entry fails loudly, never silently
wrong.

## Ingest strategies

Each is an executable that `srr` drives through the external-ingest contract: a JSON
request on stdin, SRR items as JSON on stdout. Subscribe a channel to one with
`-i "<strategy> [flags]"`; all five also accept the URL as a CLI argument for manual
testing (the result is pretty-printed).

### `srr-telegram`

Turns each message (or album) of a Telegram channel into an article, self-hosting
every attachment into the SRR store: photos, videos, voice notes and music as inline
players, stickers as images (animated `.tgs` via their static thumbnail), anything
else as a download link; whatever can't be downloaded (over srr's size cap, or a
sticker with no static thumb) degrades to an "open in Telegram" link, never silence.
Two modes:

- **Account (MTProto) mode — the default**: private *and* public channels through your
  own user account (Telethon). Needs a one-time login: get an API id/hash from
  <https://my.telegram.org>, then `srr-telegram login` mints a session string; keep the
  three `SRR_TG_*` values where the fetch loop can see them. Every poll holds a blocking `flock` on a lock file beside the session (`SRR_TG_SESSION_FILE` + `.lock`, or `SRR_TG_LOCK_FILE`), so only one client uses the session at a time. (A user account, not a
  bot, because bots can't read pre-join history and cap media at ~20 MB.)
- **Web mode — `--no-auth`**: public channels via the public preview (`t.me/s/<name>`),
  no credentials at all. Media is linked out by default (the preview CDN's URLs are
  token-signed and expire, so hotlinking is not an option); `--selfhost` downloads it
  into the store like account mode does.

```bash
srr feed add -t "My private channel" -u "https://t.me/c/1234567890" -i "srr-telegram"
srr feed add -t "Durov" -u "https://t.me/s/durov" -i "srr-telegram --no-auth --selfhost"
```

### `srr-youtube`

YouTube already serves a valid Atom feed, but the description and thumbnail live inside
`<media:group>`, which plain RSS handling leaves empty. This strategy synthesizes a
clickable-thumbnail + description body that survives `#sanitize`. Stdlib only.
Thumbnails hotlink to `i.ytimg.com` by default; `--selfhost` downloads them into the
store, falling back to the hotlink if a download fails (those URLs are public and
stable, so degrading beats failing the cycle). The video itself is never downloaded.
`--trim` cuts boilerplate from each description (everything after a separator line, paragraphs
that are mostly links, @handles or #hashtags, and one-line calls to action with a link) and
keeps summaries, chapter lists and prose; it works on the stdin path like any other flag.

```bash
srr feed add -t "Veritasium" \
  -u "https://www.youtube.com/feeds/videos.xml?channel_id=UCHnyfMqiRRG1u-2MsSQLbXA" \
  -i "srr-youtube --selfhost"
```

### `srr-x`

X serves no feeds and its read API is paid, so this strategy reads an account's
timeline as RSS through a Nitter instance (default `https://nitter.net`,
`--instance` to point elsewhere) and rewrites every nitter artifact back to a
canonical URL — proxied media to `pbs.twimg.com`, mentions/statuses to `x.com` — so
the **stored** items never depend on the instance staying alive (only the next fetch
does). Retweets and self-reply threads are attributed and kept (`--no-retweets` /
`--no-replies` to drop them); videos play in place by default — the poster placeholder
becomes a real `<video>` resolved through X's syndication CDN, and GIF tweets keep
GIF-style autoplay (`--no-videos` keeps the old clickable poster instead); `--selfhost`
downloads images, videos and posters straight from the CDN. See the script's docstring
for measured per-instance quirks (rate limits, an HTTP/2-only WAF, whitelisting).

```bash
srr feed add -t "NASA" -u "https://x.com/NASA" -i "srr-x"
```

### `srr-sitemap`

For a site that publishes no feed at all but does publish a sitemap whose entries carry
`<lastmod>` (most CMSes do). The sitemap — or a sitemap index, read one level deep,
`--sitemap-match RE` to read only some of its children — is the listing; `--match RE`
keeps the page URLs you want. Each run takes the entries newer than the watermark it
left last time (the newest lastmod emitted, carried in the response's `etag`), at most
`--limit` (30) within `--since` days (7), fetches each page once for its head metadata
and emits title, published date (`article:published_time`, else JSON-LD
`datePublished`, else the lastmod), a clickable cover (`og:image`) and the description.
The body is not extracted here: put `#readability` with a `selector=` in the feed's
pipe, as for a teaser-only RSS feed. Pages are fetched oldest first; a 404/410 is
skipped for good, any other failure stops the batch at the last success so the rest is
retried next run. Stdlib only; sitemaps are read gzip-encoded.

```bash
drr feed add -t "Example" -u "https://example.com/sitemap.xml" \
  -i "srr-sitemap --sitemap-match posts --match /posts/" \
  -p '#readability selector=article.post-body' -p '#default'
```

### `srr-devto`

For dev.to, which has a feed per author or tag but no feed of what the whole site ranks
highest. The listing is dev.to's own top-of-the-month ranking, the first `--top` (500)
entries, read with one request per run; the ranking, not reaction counts, decides what
is kept, and its order is the order of selection. `--lang en,es` keeps only articles
whose declared language is in the set (an article with no declared language is
dropped); without it every language is kept. Each article is sent once: the ids already
sent travel in the response's `etag`; an id is dropped 31 days after the article's
publication date, when it can no longer be in the month's ranking. Each new article costs one request for its body, and the
item is its title, a cover image when it has one and the full body, so no `#readability`
step is needed. It carries no date: drr drops items dated before its watermark, and a
ranking is not in date order. A 404/410 body is skipped for good; any other failure
stops the batch with the articles already fetched kept (and the run fails if there are
none, so drr records the error). No body request starts after 300 s into a run; the
rest is fetched next run. The script spaces nothing itself: how often the ranking is
scanned is drr's per-feed `--check-minutes` (the feed editor's "Check at most every"). Stdlib
only; the feed URL must be `https://dev.to/`.

```bash
drr feed add -t "dev.to top" -u "https://dev.to/" -i "srr-devto --lang en" \
  -p '#embed' -p '#default' --check-minutes 60
```

dev.to renders YouTube and Vimeo tags as iframes, which `#sanitize` removes; drr's
`#embed`, placed before `#default`, turns them into links first.

## Pipeline steps

Pipeline steps run once per *item*, not once per *feed*: `srr` execs any non-`#builtin`
`pipe` entry with the item JSON on stdin and reads the (possibly modified) item back on
stdout — a different contract from the ingest strategies above.

### `srr-gtproxy`

For a teaser-only feed whose pages refuse drr's own HTTP client from every address it has
(a bot check that `#readability` cannot pass, so it keeps the teaser). Google Translate's
website proxy fetches a page from Google's servers and answers with the page's own text (the
translation runs in the visitor's browser), so the step asks it for the item's link
(`https://www.example.com/a` → `https://www-example-com.translate.goog/a?_x_tr_sl=auto&…`),
takes out what the proxy added (its metas, scripts and toolbar font), turns every URL it
rewrote back into the original, and makes the whole page the item's content. The step only
fetches: drr's `#readability source=content` picks the article out of that page (with a
`selector=`, or its heuristic) and `#strip` removes what it keeps but should not. Only https
links are read; stdlib fetch plus bs4/html5lib.

```bash
drr feed upd <id> -p srr-gtproxy \
  -p '#readability source=content selector="article.post-body"' \
  -p '#strip select=".gallery .more"' -p '#default'
srr-gtproxy https://www.example.com/a   # manual run: the item, the whole page in its content
```

The proxy is a free Google service with no contract: if it changes, or the site starts
refusing it, the step emits nothing and the item keeps its teaser (and `#readability` notes
that its selector matched nothing). Like `srr-tts`, the step never exits non-zero on the
fetch path (drr would drop the article for good): any failure, a typo'd or 1.x flag
(`--selector`, `--drop`) included, is logged to stderr and the item passes through
unchanged. A manual run (an http(s) URL argument) reports failures with a non-zero exit.
A busy proxy (HTTP 429 or 503; it rate-limits an address that asks too often) is asked
again after 15, 45 and 90 s, or after its longer `Retry-After` (up to 90 s), so one run stays
minutes under drr's `cmd-timeout`. `--timeout` (30 s) bounds each proxy request, `--ua` sets
its User-Agent.

### `srr-tts`

Prepends a self-hosted piper TTS narration of the title + body to the article. Opt a
feed in via its `pipe` config, after `#readability` if the feed uses it and before
`#default` so the narration marker rides the normal sanitize/upload path:

```bash
srr feed upd <id> -p 'srr-tts --voice es_ES-davefx-medium' -p '#default'
# feeds using #readability: put srr-tts after it, before #default
# feeds using #filter:      put srr-tts after it — a drop short-circuits the
#                           rest of the pipe, so filtering first avoids
#                           synthesizing articles that are about to be dropped
```

Voice resolution (first hit wins):
1. `--voice <name>` — explicit piper voice, e.g. `en_US-lessac-medium`
2. `--lang-voice xx=voice` — per-feed extension/override of the table (repeatable)
3. the built-in table, keyed by the item's `lang` field — srr auto-detects it *before*
   the pipeline runs (a declared value from the ingest strategy wins), so the table
   works on plain feeds too; `--voice` forces a voice when detection is empty/wrong

None resolvable, or any other per-item failure, passes the item through unchanged
(logged to stderr) — srr-tts never fails the feed cycle. That holds for *configuration*
errors too: a typo'd flag in a feed's `pipe` complains on stderr and leaves items
alone rather than exiting non-zero, because srr reads a non-zero exit as "drop this
item" and never retries it. Run the step manually to have mistakes reported loudly.

The asset dir arrives as `$SRR_ASSET_DIR` (set by srr on the fetch path); it's absent
in `srr preview` and older backends, where the step passes through rather than
synthesizing. Narration is written as WAV under `tts/` in the asset dir and shipped
through the same `#/`-marker upload as other self-hosted media; the store-side asset
processor (webify on this deployment) transcodes it to web audio before upload.
Voice models auto-download to `~/.local/share/srr-tts/` (or `--voices-dir`) on first
use — staged in a temp dir and renamed into place, so an interrupted download can't
leave a truncated model behind.

**Backend requirement:** `$SRR_ASSET_DIR` on external pipe steps, and the pre-pipeline
`lang` stamp the voice table keys off, are both recent backend features. Against an
older `srr` the step simply passes items through unchanged — it never breaks a feed,
it just does nothing.

**The default cap is a backstop, not timeout armor.** `--max-chars` (default 32768,
truncated at a sentence boundary; `0` disables it) exists because srr *drops* an
item whose pipe step exits non-zero or times out, and keeps its guid in
`BoundaryGUIDs` — blowing the per-step `--cmd-timeout` (default 5m) loses the
article permanently, not just its narration. Real articles stay far under the
default (a full daily digest is ~8k chars); it bounds the damage of a pathological
one. Measured with a piper medium voice on Latin prose: about **1000 characters per
minute of speech and 2.6 MB of WAV per 1000 characters**, at ~41 s synthesis per
3000 chars on a 4-core ARM64 box — so an article that *fills* the default cap is
~33 min of audio / ~85 MB and ~7.5 min of synthesis, past the default timeout. On a
feed that can genuinely ship such articles, set a smaller cap (~18000 fits the 5 m
timeout with margin) or raise the timeout. The cap counts *characters*, and those
ratios are Latin-script — a CJK voice packs far more speech per character, so
re-measure before sizing a cap there.

**Pre-warm a new voice.** The first item needing a voice downloads its ~60 MB model
*inside* the same `--cmd-timeout` as its synthesis (1.5 s on a fast link, but it is
the article you'd lose on a slow one). Fetch it once with a manual run before opting
the feed in:

```bash
srr-tts --voice es_ES-davefx-medium --asset-dir /tmp/warm article.html
```

## Python setup

All Python scripts share **one** uv project: dependencies are declared once in the
repo-root `pyproject.toml` and pinned by the committed `uv.lock`. Their shebangs go
through `bin/srr-uvrun`, which locates the repo from its own resolved path and execs
`uv run --project <repo> --script`, so any invocation — the shell, srr's fetch loop,
the tests — resolves the same `.venv` from any cwd, wherever the checkout lives.
(The indirection exists because a shebang can't be script-relative: a relative
`--project` resolves against the *invoker's* cwd, which silently picks the wrong
python.) There is deliberately no `[build-system]`: uv installs only the
dependencies, never this repo as a package.

Requirements: [`uv`](https://docs.astral.sh/uv/) installed, `bin/` entry points
reachable from `PATH` (symlinked into a PATH dir, or `bin/` on `PATH` directly).

## Tests

Self-checking scripts (no pytest); each prints PASS/FAIL lines and exits non-zero on
failure:

```bash
for t in tests/test_*.py; do uv run "$t" || break; done
```

## Not committed

- `.env` — Telegram API id/hash + login session (credentials; treat the session
  string like a password)
- `tg/`, `yt/`, `x/` — runtime media caches written by the strategies' `--selfhost` runs
- `.venv/` — the uv-managed project venv
- `docs/` — local design working notes

## License

[MIT](LICENSE). One dependency is copyleft: `srr-tts` imports `piper-tts`, which is
GPL-3.0 (piper1-gpl). The toolbox's own code stays MIT; anything that distributes it
bundled with piper is under the GPL-3.0's terms.
