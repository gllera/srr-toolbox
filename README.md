# srr-toolbox

Ops tooling and external ingest strategies for **SRR** (Static RSS Reader — the Go
`srrb` backend + TypeScript frontend living in `~/ws/srr`). This repo is what runs
*around* SRR: the single command that operates the two local deployments, and the
`srr-*` scripts that teach `srrb` to ingest sources that don't speak usable RSS.

## Layout

```
bin/srr           the SRR ops command (bash) — run/deploy/reset the prod & dev envs
bin/srr-telegram  ingest strategy: Telegram channel (incl. private) -> SRR items
bin/srr-youtube   ingest strategy: YouTube channel Atom feed -> SRR items
bin/srr-x         ingest strategy: X/Twitter account (via a Nitter instance) -> SRR items
bin/srr-uvrun     shebang wrapper that runs the Python scripts in this repo's uv venv
tests/            self-checking test scripts + fixtures
pyproject.toml    shared dependencies for every bin/ script, pinned by uv.lock
```

`bin/` must be on `PATH` — `srrb` resolves ingest strategies by bare name, and
`srr-uvrun` shebangs rely on it too. A missing PATH fails loudly, never silently wrong.

## `srr` — the ops command

One tool drives both **fully independent** local deployments (they share nothing;
the only link is the explicit `reset-dev`):

| Env | Dir | Config | Binary it runs |
|---|---|---|---|
| **prod** | `~/public/srr/` | `~/.config/srr/srr.prod.yaml` | static `~/.local/lib/srr/srrb` (frozen; only `deploy-be` updates it) |
| **dev** | `~/public/srr.tmp/` | `~/.config/srr/srr.yaml` | live build `~/ws/srr/dist/srrb` (changes every `make build-be`) |

The env token is always explicit — there is no default env, so no way to hit prod by
omission. `srr prod` also scrubs any lingering `SRR_CONFIG`/`SRR_CONFIG_INLINE` from
the shell: always prod, both binary and config.

```bash
srr dev  [srrb args...]   # run the backend: live dev build + dev config (everyday command)
srr prod [srrb args...]   # run the backend: static prod binary + prod config — live users!
srr status                # both envs + both binaries at a glance
srr config <prod|dev>     # print the resolved config for an env
srr reset-dev             # wipe dev, re-seed it from current prod, repoint the baked cdn-url
srr rebuild-dev           # re-create the dev store from scratch: prod's channels (config
                          # only), fresh fetch with the dev binary, gen bump
srr build-fe <prod|dev>   # build the frontend against that env's config and deploy it
srr deploy-be             # build the backend and install it as the static prod binary
srr fetch <prod|dev>      # `art fetch` into that env's packs (labelled form of `srr <env> art fetch`)
```

Typical flow: develop and test everything with `srr dev …`; when a backend build is
vetted, `srr deploy-be` promotes it — until then prod keeps running the
previously-deployed binary, never a half-finished dev build.

## Ingest strategies

Each is an executable that `srrb` drives through the external-ingest contract: a JSON
request on stdin, SRR items as JSON on stdout. Subscribe a channel to one with
`-i "<strategy> [flags]"`; all three also accept the URL as a CLI argument for manual
testing (the result is pretty-printed).

### `srr-telegram`

Turns each message (or album) of a Telegram channel into an article, self-hosting its
photos and videos into the SRR store. Two modes:

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
srr dev chan add -t "My private channel" -u "https://t.me/c/1234567890" -i "srr-telegram"
srr dev chan add -t "Durov" -u "https://t.me/s/durov" -i "srr-telegram --no-auth --selfhost"
```

### `srr-youtube`

YouTube already serves a valid Atom feed, but the description and thumbnail live inside
`<media:group>`, which plain RSS handling leaves empty. This strategy synthesizes a
clickable-thumbnail + description body that survives `#sanitize`. Stdlib only.
Thumbnails hotlink to `i.ytimg.com` by default; `--selfhost` downloads them into the
store, falling back to the hotlink if a download fails (those URLs are public and
stable, so degrading beats failing the cycle). The video itself is never downloaded.

```bash
srr dev chan add -t "Veritasium" \
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
`--no-replies` to drop them); videos become a clickable poster linking to the tweet;
`--selfhost` downloads images straight from the CDN. See the script's docstring for
measured per-instance quirks (rate limits, an HTTP/2-only WAF, whitelisting).

```bash
srr dev chan add -t "NASA" -u "https://x.com/NASA" -i "srr-x"
```

## Python setup

All Python scripts share **one** uv project: dependencies are declared once in the
repo-root `pyproject.toml` and pinned by the committed `uv.lock`. Their shebangs go
through `bin/srr-uvrun`, which locates the repo from its own resolved path and execs
`uv run --project <repo> --script`, so any invocation — the shell, srrb's fetch loop,
the tests — resolves the same `.venv` from any cwd, wherever the checkout lives.
(The indirection exists because a shebang can't be script-relative: a relative
`--project` resolves against the *invoker's* cwd, which silently picks the wrong
python.) There is deliberately no `[build-system]`: uv installs only the
dependencies, never this repo as a package.

Requirements: [`uv`](https://docs.astral.sh/uv/) installed, `bin/` on `PATH`.

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
