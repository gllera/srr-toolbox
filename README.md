# srr-toolbox

Companion `srr-*` scripts for **SRR** (Static RSS Reader — the Go `srr` backend +
TypeScript frontend living in `~/ws/srr`): the ingest strategies that teach `srr` to
read sources that don't speak usable RSS, a pipeline step, and a tool that drives the
`srr` CLI from the outside.

## Layout

```
bin/srr-telegram    ingest strategy: Telegram channel (incl. private) -> SRR items
bin/srr-youtube     ingest strategy: YouTube channel Atom feed -> SRR items
bin/srr-x           ingest strategy: X/Twitter account (via a Nitter instance) -> SRR items
bin/srr-tts         pipeline step: prepend a piper TTS narration to the article
bin/srr-digest-gen  store tool: Claude-written daily digest of the store, back into it
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

## Store tools

Not driven by `srr` — they drive `srr`. Everything store-shaped (where the store
lives, its credentials, its endpoint) stays inside the backend: these scripts only
call the CLI.

### `srr-digest-gen`

Reads the last N hours of articles out of the store (`srr feed ls` + `srr art`),
has `claude -p` write an editorial digest of them, and hands the resulting rolling
14-day RSS feed back to `srr syndicate push`, which writes it into the store as
`out/<name>.rss`. Subscribe the store to that feed's public URL and the digest shows up in
the reader like any other feed — date-keyed GUIDs mean one article per day. Article
text is untrusted, so every claude call runs with all tools disabled (`--tools ""`): a
prompt injection in an article has nothing to reach for.

```bash
srr-digest-gen                     # today's digest, pushed to out/digest.rss
srr-digest-gen --dry-run           # print the RSS instead; touch nothing
srr-digest-gen --dump              # print the collected articles as JSON; no claude call
srr-digest-gen --hours 72 --tag news
srr-digest-gen --no-history        # bootstrap: the feed does not exist yet
srr-digest-gen --force             # replace a day the feed already carries
```

**It keeps no local state.** The previous days come from `srr syndicate fetch` — the
published feed *is* the history, so any box that can reach the store can run it, and
there is no second copy to drift out of sync. `--no-history` is the one-time bootstrap
for a feed that does not exist yet; it rewrites the feed with today alone.

That feed also dates the last edition, so **the window is the gap since it** rather
than a fixed 24 h: a run missed at 07:00 and caught up at 19:00 covers both days
instead of dropping one. Floored at 24 h (a same-day rerun still digests a full day),
capped by `--max-hours` (default 72 — after a longer outage the missing days stay
missing, loudly, rather than becoming one enormous edition). `--hours N` overrides it;
`--dump` never computes it, so the query path needs no backend support.

A quiet day (≤300k chars of prompt payload) is one claude call. A busy one goes
map-reduce: ~150k-char chunks of full-text articles are each condensed into
relevance-scored (`* [1-5]`) plain-text notes by one `--map-model` call (default
`sonnet` — condensing is mechanical, and three chunks are in flight at a time), notes
below the floor are dropped, and a final `--model` call writes the digest from the
survivors. So full article text informs the result even on days that wouldn't fit one
context window. If a map call comes back mostly unscored the filter fails open and
keeps everything, rather than gutting the digest on a format drift.

Deployment knowledge is all flags, none of it baked in: `--name` (syndication feed
name), `--link` (the feed's public URL, for the RSS channel's `<link>` — omitted, no
`<link>` is emitted; the items deliberately get none, or every digest in the
reader would open the raw feed file), `--tz` (which day an entry is dated, default the machine's),
`--exclude-tag` (default `digest`, so the digest never digests itself), `--lang` (the
language the digest is written in, default `English` — free-form, anything that names
a language to claude; the whole edition follows it, `Top:`/`Also:` labels included).
Run it from a timer for a daily edition.

Failure discipline: any error — store unreachable, claude failure, non-HTML output,
push refused — aborts *before* the store is touched and prints the reason, never a
traceback. Yesterday's feed stays published, and SRR sees unchanged GUIDs and no-ops.
Four deliberate refusals to guess:

- **The window is covered or the run fails.** `srr art` returns a page at a time,
  so collection pages back until it sees an article older than the window. `--limit`
  is a safety cap, and hitting it before the window is covered is an error — a digest
  that silently omits half its day is worse than no digest.
- **A day already in the feed is not republished** without `--force`. SRR has almost
  certainly ingested that GUID already and will not re-read it, so a quiet rerun would
  look like it worked while readers kept the old version.
- **An empty window is an error**, not a quiet day. With feeds that work, "no articles
  at all in the last 24 h" means the fetch loop is broken — exiting 0 would hide that
  for as long as it takes someone to notice a stale feed. `--allow-empty` opts out.
- **The output has to be shaped like a digest** — the `<p><strong>` opener paragraph
  (the check is structural, since the labels follow `--lang`) and at least three
  paragraphs. The input is untrusted text, so the likeliest bad day is a refusal or an
  error page, and `<p>I can't help with that…</p>` clears any mere length floor. Same
  gate on the map phase: notes that contain no `* ` lines are not notes.

Two things do *not* abort the run. A failed map chunk: up to a third may be lost and
the day is still digested from the rest, rather than throwing away the calls the other
chunks already paid for. And a failed single-pass or reduce call gets one retry —
those are the calls whose loss wastes every other call. Each run logs what it cost
(`run: 4 claude calls, 380k prompt chars, 252s`) on the way out, including when it
failed.

**Backend requirement:** `srr syndicate push` / `srr syndicate fetch`, and the output
feed must exist as an *external* slot — SRR reserves it but never generates its bytes:

```bash
srr syndicate set digest -f rss -x      # once, per deployment
srr-digest-gen --no-history             # first run: nothing published to read back yet
```

Against a backend without those commands the run fails when it reads the feed back,
before spending a claude call; `--dump` works regardless (it never touches the feed),
and `--dry-run --no-history` exercises everything up to the push.

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
