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

The account owner authorised this skill to tick the login page's 已阅读并同意
checkbox on their behalf (2026-09-22). That authorisation covers **only** that
one control on the login form - never a cookie banner, consent dialog or any
other terms prompt on the site. Do not widen it.

Implementation note: AlphaPai uses Element UI, so the real
`input[type=checkbox]` is `.el-checkbox__original` at 0x0 and is never
"visible"; the state is the `is-checked` class on the wrapping
`label.el-checkbox`. The box also arrives already ticked, so this is not a
gate - a failure to operate it is recorded, not raised, and login proceeds.

Do not add code that reads `localStorage`, cookies, or request headers. Two
shortcuts were tried and both correctly fail: requesting the storage URL
directly returns 401, and fetching it from page context is blocked by CORS.
The supported path is to let the app fetch its own file.

## First run is a conversation, not a command list

On a machine that has never run this, **start with `doctor`** and work through
what it reports. Do not paste a setup checklist and wait.

`doctor` splits its findings for exactly this purpose:

- `run_these_yourself` — fixes the agent should just run (installing
  `playwright` / `python-docx` with the current interpreter, running `probe` to
  cache routes). Say what you are installing, run it, move on.
- `needs_the_user` — things only they can do: installing Microsoft Edge,
  typing the credential into their own console, choosing a vault. Walk them
  through these one at a time; do not dump the list.

Then re-run `doctor` and confirm `blocking` is empty before trying to capture
anything. A typical cold start is: install two pip packages (agent), install
Edge if absent (user), store the credential via the prompt script (user),
choose a vault (user answers in chat, agent applies), `probe` (agent).

`playwright install` is normally unnecessary - this skill drives the installed
Edge through `channel="msedge"` rather than a downloaded browser.

For the vault, in order:

1. Run `config --detect-vaults` yourself.
2. Show the candidates and ask which one they want, or invite them to paste a
   path. Their working directory and its parents are checked first, so the
   vault they are standing in usually appears at the top.
3. Run `config --set-vault "<their answer>" --set-subdir AlphaPai` for them.
4. Confirm with `config` and tell them where notes will now land.

A `warnings` entry saying no vault is configured is a cue to start that
conversation, not something to relay verbatim.

The credential is the one deliberate exception. A vault path is not a secret
and belongs in chat; a password is, and must never be typed to an agent or
passed as an argument. For that one, point the user at
`Open-AlphaPaiCredentialPrompt.ps1` and let them type it into their own
window. The asymmetry is the point: ask for what is safe to ask for, and hand
off only what is not.

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

**Downloads need a headed browser, parked off-screen.** The app delivers a
file by navigating to its object store, and headless Edge silently drops that
download. `pull` therefore runs headed, with the window positioned past the
bottom of the virtual desktop so it never appears over the user's work - a
fixed offset was not enough on a tall/multi-monitor layout, so it is measured.
`--visible` shows it; `--headless` exists but usually returns nothing. `list`
and `boards` are headless already.

**Sessions are short-lived.** A profile that authenticated minutes ago is
often bounced back to `/login`, so every command logs in inside the same
process that scrapes. Do not build anything on "it is already logged in".

A headed Edge also dies mid-run occasionally - two restarts in one run is
normal, not exceptional. `pull` recovers: it rebuilds the browser and resumes
the outstanding items, up to `--max-restarts` (default 4). A run reporting
`restarts: 2` with `status: ok` completed fine; an exhausted budget is reported
per item.

Filenames: the kind tag (`-transcript`, `-bundle`) is reserved out of the path
budget and only the title is squeezed. Never append the tag after truncating -
that made both artifacts resolve to one filename, and the second was skipped as
"already present" while the run still reported `status: ok`.

When no vault is configured, output falls back to a folder inside the skill.
Commands report that in `warnings`; do not let it pass silently.

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
- Board captures take what the feed returns in its list response - including
  the summary text it carries (hot topics ~400 chars, roadshow digests, sector
  catalyst events). They do not open individual items to fetch full article or
  report bodies.
- Every board writes a `.json` sidecar by default; markdown is the lossy view.
  Rendering is per-dataset (`ap_render.py`) because one generic table dropped
  exactly the fields worth having.
- A board reporting `rows: 0` says which case it is: the feed answered and was
  empty, or its endpoint was never called (a route change).
- Captured notes are personal meeting content. Keep them in the vault; do not
  commit them to a repository. `config.git_exposure()` checks whether the
  output directory sits in a git repo that does not ignore it, and every
  command surfaces that in `warnings` - act on it rather than relaying it.
  Check with a concrete sample path, never a bare directory: `git check-ignore`
  on a directory can match a blank .gitignore line and report "ignored" when
  nothing is.

## Verification

After a change, confirm all four:

0. `doctor` reports no blocking items on a fresh machine.
1. `probe` resolves every section.
2. `list` returns records with sensible `available` kinds.
3. `pull --include-examples --match <title>` writes a note whose **body
   matches its filename** - the row-to-menu mapping has broken this twice, and
   the failure is silent: you get a real note about the wrong meeting.
4. `pull --kinds ai_summary,transcript` on a **long-titled** record into a
   **deep** output path writes *two* files, both non-empty, with the
   `-transcript` tag intact. A bogus `skipped: "already present"` here means
   the filename budget regressed.
5. `boards` returns non-zero rows for `hot_topics`.
