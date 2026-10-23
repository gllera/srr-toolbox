# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Companion scripts for SRR (the Go `srr` backend + TS frontend in
`~/ws/srr`, which has its own CLAUDE.md — backend/frontend *code* changes go there,
not here). `bin/` holds three kinds. Exec'd by `drr` by bare name, resolved from
`PATH`: **`srr-telegram` / `srr-youtube` / `srr-x` / `srr-sitemap` / `srr-devto`** (python ingest
strategies — one process per feed, JSON stdin -> JSON stdout) and **`srr-tts`**
(python pipeline step — one process per item, item JSON stdin -> item JSON stdout,
prepends a piper TTS narration). Running the other way round, driving the `drr` CLI as
a subprocess: **`drr-digest-gen`** (store tool — collects recent articles via `drr`, has
`claude -p` write a digest, writes it to a feed file in `--out-dir`). Plus the
`srr-uvrun` shebang wrapper. On the server `bin/` reaches `PATH` as the
`/opt/drr/bin` mirror, refreshed via `drr-update toolbox`; for dev, put
`~/ws/srr-toolbox/bin` on `PATH` (this repo is destined to be public, so it only
states the generic PATH contract).

## Commands

```bash
# Tests: self-checking plain-python scripts (no pytest). Run one file = one test module.
uv run tests/test_x_content.py
for t in tests/test_*.py; do uv run "$t" || break; done   # all of them

# Manual run of an ingest strategy (CLI params instead of the stdin JSON srr sends;
# pretty-prints the response):
srr-youtube "https://www.youtube.com/feeds/videos.xml?channel_id=UC..."
srr-x --instance https://nitter.poast.org "@handle"

# Manual run of the srr-tts pipeline step (HTML file instead of the stdin item JSON):
srr-tts --voice en_US-lessac-medium --asset-dir /tmp/store article.html

# Manual run of the digest generator without spending claude calls or touching the store:
drr-digest-gen --dump --hours 2      # collected articles as JSON
drr-digest-gen --dry-run --no-history  # full run, RSS to stdout, nothing written

# Run the reader's CLI directly (nothing wraps it). On the server it is drr's binary
# as the drr user against the live data dir (read commands need no config):
sudo -u drr /opt/drr/bin/drr -o /srv/drr ...   # live store on the server — reads only
~/ws/drr/dist/drr -o <scratch-dir> ...         # dev build against a scratch data dir
```

There is no build/lint step. The Python scripts have no `.py` extension; tests import
them by path via `importlib.machinery.SourceFileLoader`.

## Architecture

**The external-ingest contract** (shared shape of all five strategies): `srr` writes
one JSON request to stdin — `{url, etag, last_modified, asset_dir, max_asset_size}` —
and reads one JSON response from stdout: `{"items": [...], "etag": …, "last_modified": …}`
or `{"not_modified": true}`. srr persists the returned etag/last_modified cursors and
echoes them in the next request; they need not be real HTTP validators — srr-telegram
repurposes `etag` as its last-seen-message-id watermark — and answering `not_modified`
preserves the stored cursor + dedup state. Every item is
`{guid, title, content, link, published}`: `guid` a stable fnv1a32 int (the dedup key —
changing how it's derived re-imports that feed's backlog), `content` HTML that must
survive srr's `#sanitize` (img src / a href allowlisted; no iframes/scripts),
`published` ISO 8601 or null. `asset_dir` is where self-hosted media goes; absent
asset_dir (e.g. `srr preview`) means hotlink/placeholder, never download. Each
script also maps CLI parameters onto the *same* request dict for manual testing —
keep the two entry paths building identical requests.

**Store tools drive the CLI, never the store** (`drr-digest-gen`): they read through
`drr feed ls` / `drr art`, and publish by writing `<out-dir>/<name>.rss` atomically
(`--out-dir`; no syndication). `--drr-cmd` is the command prefix (default `drr`), for a
store owned by another user, e.g. `sudo -n -u drr /opt/drr/bin/drr -o /srv/drr`. The
store's location and credentials are the backend's business — a script here must never
open the store's config or database, or learn a credential. Whatever the deployment
still needs to say (public feed URL, timezone, feed name, out dir) is a flag with a
generic default or none at all; nothing in this repo names the owner's infrastructure.
They also keep **no local state**: what was already published is read back out of the
feed file, so that file stays the single source of truth and the tool runs from any box
that can read it.

**Python env plumbing**: one root `pyproject.toml` declares deps for every `bin/`
script, pinned by the committed `uv.lock`. Shebangs are `#!/usr/bin/env srr-uvrun`;
that wrapper self-locates the repo from its own resolved path and execs
`uv run --project <repo> --script`, so the same `.venv` resolves from any cwd. Don't
give a script its own inline deps or venv, and don't add a `[build-system]` — its
absence is deliberate (uv must install only the deps, never this repo as a package).

**The fetch loop** (`srr-fetch` user service) runs `srr` directly with
`SRR_CONFIG=…/srr.yaml` in its unit — restart it after a backend or config update.

**One Telethon client per session** (drr D103): account-mode `srr-telegram` takes a
blocking `fcntl.flock` on `lock_path()` (`SRR_TG_LOCK_FILE`, else the session file +
`.lock`, else `~/.cache/srr-telegram.lock`) for the whole poll, so concurrent feeds,
previews and CLI runs queue instead of sharing the StringSession. Web mode needs none.

## Conventions

- One explicit flag per mode, never env-sniffing or implicit fallback (e.g.
  `--no-auth`, `--selfhost`). Which mode is the default is a product decision the
  owner makes — ask before flipping one.
- Rewritten/stored URLs must be canonical and instance-independent (see `srr-x`:
  stored items must not depend on the nitter instance staying alive).
- `.env` holds the Telegram `SRR_TG_*` credentials (gitignored); the session string
  is a password-equivalent. `tg/`, `yt/`, `x/` are runtime media caches; `docs/` is
  local working notes — all gitignored, none are code.
- The drr config embeds live secrets (Telegram session) and lives outside this
  repo. Never commit it here: this repo is destined to be public.
