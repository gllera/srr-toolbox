# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Ops tooling + external ingest strategies for SRR (the Go `srrb` backend + TS frontend
in `~/ws/srr`, which has its own CLAUDE.md — backend/frontend *code* changes go there,
not here). Two kinds of things live in `bin/`:

- **`srr`** (bash) — the single ops command driving the two local deployments
  (prod `~/public/srr/`, dev `~/public/srr.tmp/`).
- **`srr-telegram` / `srr-youtube` / `srr-x`** (python) — external ingest strategies
  that `srrb` execs by bare name, resolved from `PATH`. `bin/` must therefore be on
  `PATH` (the shell and the `srrb-prod-fetch` service both set it).

## Commands

```bash
# Tests: self-checking plain-python scripts (no pytest). Run one file = one test module.
uv run tests/test_x_content.py
for t in tests/test_*.py; do uv run "$t" || break; done   # all of them

# Manual run of an ingest strategy (CLI params instead of the stdin JSON srrb sends;
# pretty-prints the response):
srr-youtube "https://www.youtube.com/feeds/videos.xml?channel_id=UC..."
srr-x --instance https://nitter.poast.org "@handle"

# Ops (see bin/srr --help; the `srr` skill is the full runbook):
srr status            # both envs + both binaries
srr dev  [srrb args]  # everyday command: live dev build + dev config
srr prod [srrb args]  # static deployed binary + prod config — mutates what live users see
```

There is no build/lint step. The Python scripts have no `.py` extension; tests import
them by path via `importlib.machinery.SourceFileLoader`.

## Architecture

**The external-ingest contract** (shared shape of all three strategies): `srrb` writes
one JSON request to stdin — `{url, etag, last_modified, asset_dir, max_asset_size}` —
and reads one JSON response from stdout: `{"items": [...], "etag": …, "last_modified": …}`
or `{"not_modified": true}`. srrb persists the returned etag/last_modified cursors and
echoes them in the next request; they need not be real HTTP validators — srr-telegram
repurposes `etag` as its last-seen-message-id watermark — and answering `not_modified`
preserves the stored cursor + dedup state. Every item is
`{guid, title, content, link, published}`: `guid` a stable fnv1a32 int (the dedup key —
changing how it's derived re-imports that feed's backlog), `content` HTML that must
survive srrb's `#sanitize` (img src / a href allowlisted; no iframes/scripts),
`published` ISO 8601 or null. `asset_dir` is where self-hosted media goes; absent
asset_dir (e.g. `srr dev preview`) means hotlink/placeholder, never download. Each
script also maps CLI parameters onto the *same* request dict for manual testing —
keep the two entry paths building identical requests.

**Python env plumbing**: one root `pyproject.toml` declares deps for every `bin/`
script, pinned by the committed `uv.lock`. Shebangs are `#!/usr/bin/env srr-uvrun`;
that wrapper self-locates the repo from its own resolved path and execs
`uv run --project <repo> --script`, so the same `.venv` resolves from any cwd. Don't
give a script its own inline deps or venv, and don't add a `[build-system]` — its
absence is deliberate (uv must install only the deps, never this repo as a package).

**The `srr` tool's key design points** (preserve these when editing it):

- The env token is explicit and mandatory — no default env, no way to hit prod by
  omission. `srr prod` scrubs `SRR_CONFIG`/`SRR_CONFIG_INLINE` from the environment.
- dev and prod run *different binaries*: dev = live `~/ws/srr/dist/srrb`, prod =
  static `~/.local/lib/srr/srrb` that only `deploy-be` updates (atomically, via
  temp + `mv`). Prod never executes a half-finished dev build.
- `reset-dev` is destructive to dev only; there is deliberately **no** prod
  wipe/reset — don't add one.
- Never touch the binary `.gz` packs (`db.gz`, `idx/`, `data/`) with text tools;
  `reset-dev`'s `sed` is scoped to top-level `index.html` + `*.js` only, because
  the frontend's `cdn-url` is baked in at build time.

## Conventions

- One explicit flag per mode, never env-sniffing or implicit fallback (e.g.
  `--no-auth`, `--selfhost`). Which mode is the default is a product decision the
  owner makes — ask before flipping one.
- Rewritten/stored URLs must be canonical and instance-independent (see `srr-x`:
  stored items must not depend on the nitter instance staying alive).
- `.env` holds the Telegram `SRR_TG_*` credentials (gitignored); the session string
  is a password-equivalent. `tg/`, `yt/`, `x/` are runtime media caches; `docs/` is
  local working notes — all gitignored, none are code.
