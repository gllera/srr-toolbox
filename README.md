# srr-toolbox

External ingest strategies for **SRR** (Static RSS Reader — the Go `srr` backend +
TypeScript frontend living in `~/ws/srr`): the `srr-*` scripts that teach `srr` to
ingest sources that don't speak usable RSS.

## Layout

```
bin/srr-telegram  ingest strategy: Telegram channel (incl. private) -> SRR items
bin/srr-youtube   ingest strategy: YouTube channel Atom feed -> SRR items
bin/srr-x         ingest strategy: X/Twitter account (via a Nitter instance) -> SRR items
bin/srr-tts       pipeline step: prepend a piper TTS narration to the article
bin/srr-uvrun     shebang wrapper that runs the Python scripts in this repo's uv venv
tests/            self-checking test scripts + fixtures
pyproject.toml    shared dependencies for every bin/ script, pinned by uv.lock
```

Every `bin/` entry point must be reachable from `PATH` — `srr` resolves ingest
strategies by bare name, and `srr-uvrun` shebangs rely on it too. Symlinks into a
PATH dir work fine (`srr-uvrun` sees through them via `readlink -f`), as does
putting `bin/` on `PATH` directly. A missing entry fails loudly, never silently
wrong.

## Ingest strategies

Each is an executable that `srr` drives through the external-ingest contract: a JSON
request on stdin, SRR items as JSON on stdout. Subscribe a channel to one with
`-i "<strategy> [flags]"`; all three also accept the URL as a CLI argument for manual
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
  three `SRR_TG_*` values where the fetch loop can see them. (A user account, not a
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

## Pipeline steps

Pipeline steps run once per *item*, not once per *feed*: `srr` execs any non-`#builtin`
`pipe` entry with the item JSON on stdin and reads the (possibly modified) item back on
stdout — a different contract from the ingest strategies above.

### `srr-tts`

Prepends a self-hosted piper TTS narration of the title + body to the article. Opt a
feed in via its `pipe` config, after `#readability` if the feed uses it and before
`#default` so the narration marker rides the normal sanitize/upload path:

```bash
srr feed upd <id> -p 'srr-tts --voice es_ES-davefx-medium' -p '#default'
# feeds using #readability: put srr-tts after it, before #default
```

Voice resolution (first hit wins):
1. `--voice <name>` — explicit piper voice, e.g. `en_US-lessac-medium`
2. `--lang-voice xx=voice` — per-feed extension/override of the table (repeatable)
3. the built-in table, keyed by the item's `lang` field — only present when an earlier
   ingest strategy or pipeline step set it (srr's own language detection stamps *after*
   this step, so plain feeds need an explicit `--voice`)

None resolvable, or any other per-item failure, passes the item through unchanged
(logged to stderr) — srr-tts never fails the feed cycle.

The asset dir arrives as `$SRR_ASSET_DIR` (set by srr on the fetch path); it's absent
in `srr preview` and older backends, where the step passes through rather than
synthesizing. Narration is written as WAV under `tts/` in the asset dir and shipped
through the same `#/`-marker upload as other self-hosted media; the store-side asset
processor (webify on this deployment) transcodes it to web audio before upload.
`--max-chars` (default 10000, truncated at a sentence boundary; 0 disables) bounds
synthesis time against srr's per-step `--cmd-timeout`. Voice models auto-download to
`~/.local/share/srr-tts/` (or `--voices-dir`) on first use.

```bash
srr-tts --voice es_ES-davefx-medium --asset-dir /tmp/store article.html
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
