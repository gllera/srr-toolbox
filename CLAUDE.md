# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Companion scripts for SRR (the Go `srr` backend + TS frontend in
`~/ws/srr`, which has its own CLAUDE.md — backend/frontend *code* changes go there,
not here). `bin/` holds three kinds. Exec'd by `srr` by bare name, resolved from
`PATH`: **`srr-telegram` / `srr-youtube` / `srr-x`** (python ingest
strategies — one process per feed, JSON stdin -> JSON stdout) and **`srr-tts`**
(python pipeline step — one process per item, item JSON stdin -> item JSON stdout,
prepends a piper TTS narration). Running the other way round, driving the `srr` CLI as
a subprocess: **`srr-digest-gen`** (store tool — collects recent articles, has
`claude -p` write a digest, pushes it back as a syndication feed). Plus the
`srr-uvrun` shebang wrapper. On this machine `bin/` reaches `PATH` via
`~/.local/bin` symlinks managed from the private srr-config repo; the `srr`
skill is the full ops runbook (this repo is destined to be public, so it only
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
srr-digest-gen --dump --hours 2      # collected articles as JSON
srr-digest-gen --dry-run             # full run, RSS to stdout, nothing pushed

# Run the backend directly (nothing wraps it). There is ONE srr: ~/.config/srr/srr.yaml
# is the only config and also the SRR_CONFIG-unset default, so bare `srr` operates on
# the live deployment — point SRR_CONFIG at a scratch config when experimenting.
srr ...                    # deployed backend (what the srr-fetch service runs)
~/ws/srr/dist/srr ...      # live dev build, for testing backend changes
```

There is no build/lint step. The Python scripts have no `.py` extension; tests import
them by path via `importlib.machinery.SourceFileLoader`.

## Architecture

**The external-ingest contract** (shared shape of all three strategies): `srr` writes
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

**Store tools drive the CLI, never the store** (`srr-digest-gen`): they read through
`srr feed ls` / `srr art` / `srr syndicate fetch <name>` and write through
`srr syndicate push <name> -`. The store's location, endpoint and credentials are the
backend's business — a script here must never open `srr.yaml`, sign an S3 request, or
learn a bucket key. Whatever the deployment still needs to say (public feed URL,
timezone, syndication name) is a flag with a generic default or none at all; nothing
in this repo names the owner's infrastructure. They also keep **no local state**: what
was already published is read back out of the store, so the store stays the single
source of truth and the tool runs from any box.

**Python env plumbing**: one root `pyproject.toml` declares deps for every `bin/`
script, pinned by the committed `uv.lock`. Shebangs are `#!/usr/bin/env srr-uvrun`;
that wrapper self-locates the repo from its own resolved path and execs
`uv run --project <repo> --script`, so the same `.venv` resolves from any cwd. Don't
give a script its own inline deps or venv, and don't add a `[build-system]` — its
absence is deliberate (uv must install only the deps, never this repo as a package).

**The fetch loop** (`srr-fetch` user service) runs `srr` directly with
`SRR_CONFIG=…/srr.yaml` in its unit — restart it after a backend or config update.

## Conventions

- One explicit flag per mode, never env-sniffing or implicit fallback (e.g.
  `--no-auth`, `--selfhost`). Which mode is the default is a product decision the
  owner makes — ask before flipping one.
- Rewritten/stored URLs must be canonical and instance-independent (see `srr-x`:
  stored items must not depend on the nitter instance staying alive).
- `.env` holds the Telegram `SRR_TG_*` credentials (gitignored); the session string
  is a password-equivalent. `tg/`, `yt/`, `x/` are runtime media caches; `docs/` is
  local working notes — all gitignored, none are code.
- The srr configs (`~/.config/srr/*.yaml`) embed live secrets (Telegram session,
  R2 keys) and ride their own PRIVATE repo — `gllera/srr-config`, cloned at
  `~/.config/srr`. Never commit them into this repo: it is destined to be public.
