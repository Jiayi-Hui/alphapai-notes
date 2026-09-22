---
name: alphapai-notes
description: Capture AlphaPai (Alpha派) content into an Obsidian vault - PaiPai 转记 records as Markdown/docx/PDF notes, plus read-only list captures of the 发现 boards (机构热议, 推荐, 分析师, 自选, 板块). Use when the user wants to download, sync, archive or schedule AlphaPai notes, meeting transcripts, AI summaries or discovery feeds into Obsidian. Self-contained login reads the secret from the OS keystore and never exposes it. Do not use for posting, deleting or modifying anything on AlphaPai.
---

# AlphaPai Notes

Pull AlphaPai content onto disk in a form that is useful later: Obsidian
Markdown with YAML frontmatter by default, the original `.docx` when an
archive copy matters, PDF on request.

Two datasets, one browser session:

| What | Command | Source |
| --- | --- | --- |
| PaiPai 转记 records (AI 纪要 / 逐字稿 / 录音) | `list`, `pull` | your own uploads, links and recordings |
| 发现 boards: 机构热议 / 推荐 / 分析师 / 自选 / 板块 | `boards` | the discovery feeds, list metadata only |

## Credential boundary

Login lives in `scripts/ap_auth.py` and is self-contained - the skill has no
dependency on another auth helper. The secret is read from the OS keystore
(Windows Credential Manager or macOS login keychain, target `AlphaPai:Login`)
at the moment the form is filled, inside a browser profile dedicated to
AlphaPai. Nothing prints, logs, returns or stores it; an agent driving this
sees status words only.

The user enters the credential themselves in their own console window
(`scripts/Open-AlphaPaiCredentialPrompt.ps1`). No code path accepts a password
as an argument or reads one from a repository file. `ALPHAPAI_USERNAME` /
`ALPHAPAI_PASSWORD` (optionally seeded from an untracked `.env`) exist as a
second-priority source for CI and containers; the loader refuses a `.env` that
git tracks, and `auth status` reports which source is in use.

Never tick the login page's agreement checkbox in code - accepting terms is
the user's act. If AlphaPai starts enforcing it, login fails with an explicit
message instead.

Do not add code that reads `localStorage`, cookies, or request headers. Two
shortcuts were tried and both correctly fail: requesting the storage URL
directly returns 401, and fetching it from page context is blocked by CORS.
The supported path is to let the app fetch its own file.

## Setup

```bash
# 1. store the credential (you type it in your own window)
powershell -ExecutionPolicy Bypass -File scripts/Open-AlphaPaiCredentialPrompt.ps1
python scripts/alphapai_notes.py auth status

# 2. point it at your vault (or let it look for one)
python scripts/alphapai_notes.py config --detect-vaults
python scripts/alphapai_notes.py config --set-vault "D:/Obsidian/MyVault" --set-subdir AlphaPai

# 3. confirm login works and every view still resolves
python scripts/alphapai_notes.py probe
```

Config lives in the user's own config dir (`%APPDATA%/alphapai-notes/config.json`
on Windows), never in the repo. `ALPHAPAI_NOTES_OUT` or `--out` override the
destination for one run.

Requirements: Windows or macOS, Microsoft Edge, Python 3 with `playwright` and
`python-docx`. `auth` subcommands (`status`, `setup`, `probe`, `login`,
`logout`) manage the session; `ap_auth.py` also runs standalone for diagnosis.

## Everyday use

```bash
# what is there (metadata only, nothing downloaded)
python scripts/alphapai_notes.py list

# capture new notes into the vault; already-present notes are skipped
python scripts/alphapai_notes.py pull

# one record, both artefacts, three formats
python scripts/alphapai_notes.py pull --match "GTC" --kinds ai_summary,transcript --format md,docx,pdf

# discovery boards
python scripts/alphapai_notes.py boards --board hot_topics,recommend,watchlist
```

Select records with `--match <text>` rather than `--id`: AlphaPai re-encrypts
record ids on every session, so an id copied from an earlier run is already
stale. Kinds are `ai_summary`, `transcript`, `audio`, `all`.

AlphaPai seeds every account with 样例 demo rows. They are hidden by default;
`--include-examples` brings them back, which is also the easiest way to smoke
test a fresh install.

## Two constraints worth knowing

**Downloads need a headed browser.** The app delivers a file by navigating to
its object store, and headless Edge silently drops that download. `pull`
therefore runs headed by default; `--offscreen` parks the window below the
desktop for unattended runs. `list` and `boards` work fine headless because
they only read API responses. `--headless` is available for `pull` but will
usually come back empty-handed.

**Sessions are short-lived.** A profile that authenticated minutes ago is
often bounced back to `/login`, so every command logs in inside the same
process that scrapes. Do not build anything on "it is already logged in".

A headed Edge also dies mid-run occasionally. `pull` recovers: it rebuilds the
browser and resumes the outstanding items, up to `--max-restarts` (default 2).
A run that reports `restarts: 1` still completed.

## Scheduling

```bash
python scripts/alphapai_notes.py schedule plan --time 08:30
python scripts/alphapai_notes.py schedule install --time 08:30 --apply
python scripts/alphapai_notes.py schedule status
python scripts/alphapai_notes.py schedule remove --apply
```

Windows uses Task Scheduler, macOS a launchd user agent, Linux prints a cron
line. Installing is a persistent machine change, so every action is a dry run
until `--apply`.

The task must run **as the logged-in user, while they are logged in**: the
credential store and the browser profile are per-user, and the download needs
a real session. A "run whether the user is logged on or not" task will not
work. Pair scheduling with `--offscreen` so the window does not interrupt.

## Route self-healing

AlphaPai's paths have already moved once, so they are treated as hints. A
section resolves from the cached path, then the known default, then by
clicking its sidebar label, then by trying every sidebar label - and a
candidate only counts when the view's own API call actually fires. Whatever
works is written back to the config, so a redesign costs one slow run instead
of a code change. `probe` re-checks all of them; `config --clear-routes`
forces rediscovery.

## The optional OpenPai API

The browser is the supported route. An OpenPai API key is **off by default and
never probed**, because an unprovisioned key answers every endpoint with
`code=401001 saas用户暂无接口调用权限`. Before relying on it, confirm with an
Alpha派 administrator that the account actually has interface permissions,
then supply `ALPHAPAI_API_KEY`. `api-status` reports the situation without
making a call.

## Boundaries

- Read-only. It never uploads, renames, shares, syncs to PaiWork or deletes.
- Board captures take list metadata (title, code, org, time) - not article or
  report full text.
- A board reporting `rows: 0` says which case it is: the feed answered and was
  empty, or its endpoint was never called (a route change).
- Captured notes are personal meeting content. Keep them in the vault; do not
  commit them to a repository.

## Verification

After a change, confirm all four:

1. `probe` resolves every section.
2. `list` returns records with sensible `available` kinds.
3. `pull --include-examples --match <title>` writes a note whose **body
   matches its filename** - the row-to-menu mapping has broken this twice, and
   the failure is silent: you get a real note about the wrong meeting.
4. `boards` returns non-zero rows for `hot_topics`.
