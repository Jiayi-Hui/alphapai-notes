# alphapai-notes

**Turns your AlphaPai meeting notes into Obsidian notes.**

You record or upload a meeting to AlphaPai (Alpha派). It produces an AI summary
and a transcript — which then live on someone else's website, in `.docx`, where
your notes and your search can't reach them. This skill pulls them into your
vault as Markdown with proper frontmatter, so they become part of your own
knowledge base instead of a browser tab you have to remember.

It also captures the 发现 discovery boards (机构热议, 推荐, 分析师, 自选, 板块)
as dated list snapshots, so you can see what institutions were talking about on
a given day without opening the site.

---

## The mental model

Four things to internalise; the rest follows.

**1. You talk to an agent; the agent runs the tool.** This is a skill, not an
app. You say what you want in plain language and the agent picks the command
and the flags. The CLI below is for scripting and scheduling — you don't need
to memorise it.

**2. It never sees your password.** Login is delegated to a separate helper
(`viaim-auth`) that keeps your credential in Windows Credential Manager and
types it into a dedicated browser profile. This tool only reads what the
already-logged-in page fetched for itself. It has no code path that touches a
password, token or cookie — deliberately, so don't add one.

**3. There are two different datasets.** *Your* 转记 records (things you
uploaded or recorded) and the *platform's* 发现 boards (what everyone sees).
Different commands, different folders. The first gives you full note documents;
the second only list metadata — titles, codes, times — not article full text.

**4. Everything lands in your vault.** You set the vault once. After that notes
go to `<vault>/AlphaPai/` and board snapshots to `<vault>/AlphaPai/boards/`,
named by date so they sort naturally.

---

## How to ask for things

Say it normally. These are the patterns that work, and what the agent will
actually run:

| What you want | Say something like | Runs |
| --- | --- | --- |
| See what's on the platform | 「看看我 AlphaPai 上有哪些转记」 | `list` |
| Sync new notes into Obsidian | 「把我 AlphaPai 上的新纪要同步到 Obsidian」 | `pull` |
| One specific meeting | 「把那个讲英伟达 GTC 的纪要拉下来」 | `pull --match "GTC"` |
| Summary *and* transcript | 「纪要和逐字稿都要」 | `pull --kinds ai_summary,transcript` |
| Keep the original Word file | 「md 之外也留一份 docx」 | `pull --format md,docx` |
| Somewhere other than the vault | 「先放到桌面一个临时文件夹」 | `pull --out <path>` |
| Today's institutional chatter | 「今天机构热议在讨论什么」 | `boards --board hot_topics` |
| All the discovery boards | 「发现页那几个板块都抓一份」 | `boards` |
| Automate it | 「每天早上八点半自动同步到 Obsidian」 | `schedule install --apply` |
| Check it still works | 「AlphaPai 抓取还正常吗」 | `probe` |
| It stopped finding pages | 「页面好像改版了,重新找一下路由」 | `probe` (re-discovers, then caches) |

### What makes a good request here

- **Name meetings by title, not by ID.** AlphaPai re-encrypts record IDs on
  every session, so an ID from yesterday is already dead. 「那个讲光模块的」
  works; a copied ID does not.
- **Say if you want the transcript.** The default is the AI summary only. The
  transcript (逐字稿) is the raw speaker-by-speaker text, a separate artifact —
  useful when you want the actual quote rather than the summary's paraphrase.
- **Re-running is safe.** Notes already in the vault are skipped, so 「同步一下」
  is a cheap incremental operation, not a re-download.
- **Ask for the analysis, not just the download.** The requests that actually
  earn their keep chain this skill into what the agent does next:
  - 「把上周的会议纪要都同步下来,然后告诉我哪几个提到了定价压力」
  - 「抓一下今天的机构热议,跟昨天比有什么新题材」
  - 「把这个电话会的逐字稿拉下来,找出管理层对毛利率的原话」

---

## What you get

One note per record, with frontmatter Obsidian can filter and query on:

```markdown
---
title: "2024英伟达GTC大会 _ 黄仁勋Keynote演讲.mp3"
alphapai_id: "eD0eHgjxHCG2..."
created: "2024-03-01 00:00:00"
duration: "7230"
status: "done"
source: "upload"
kind: "ai_summary"
captured_at: "2026-09-22T11:43:31+08:00"
origin: "AlphaPai PaiPai 转记"
tags:
  - alphapai
  - meeting-notes
  - ai_summary
---

## 会议要点

### 1. 英伟达的技术创新与发展

英伟达自1993年成立以来，经历了多次重要的技术创新……
```

Headings, lists and tables survive the conversion from `.docx`. The transcript
variant lands beside it as `<name>-transcript.md` and keeps speaker labels
(`讲话人1：`). `--format md,docx,pdf` also gives you the original and a PDF;
Markdown alone is the default because it's the version you can search, link and
quote from.

---

## Setup

Once, before anything works:

```bash
pip install playwright python-docx
python -m playwright install          # uses your installed Edge, not a bundled browser
```

Point it at your vault (it can go looking for one):

```bash
python scripts/alphapai_notes.py config --detect-vaults
python scripts/alphapai_notes.py config --set-vault "D:/Obsidian/MyVault" --set-subdir AlphaPai
python scripts/alphapai_notes.py probe
```

You also need `viaim-auth` installed and holding your AlphaPai credential. If
it isn't a sibling directory, set `VIAIM_AUTH_SCRIPTS` to its `scripts` folder.

Settings live in your own config directory (`%APPDATA%/alphapai-notes/` on
Windows), never in this repo — so the repo stays portable and your paths stay
yours.

---

## Things that will confuse you if nobody says them

**A browser window opens when downloading. That is not a bug.** AlphaPai
delivers files by navigating to its object store, and a headless browser
silently drops that download — verified, along with both obvious workarounds:
requesting the storage URL directly returns 401, and fetching it from page
context is blocked by CORS. So downloads run headed. `--offscreen` (which
scheduled runs use by default) parks the window below the desktop where you
won't see it. Listing and boards are headless and invisible.

**A fresh account shows two demo records.** AlphaPai seeds every account with
样例 rows about GTC and GPT-o1. They're hidden by default; `--include-examples`
brings them back, which is the easiest way to test an install before you have
real recordings.

**`restarts: 1` in the output does not mean failure.** A headed browser
occasionally dies mid-run; the tool rebuilds it and resumes the outstanding
items. If the status says `ok`, it finished.

**An empty 分析师 board usually just means you follow no analysts.** The tool
distinguishes the two cases and says which: the feed answered and was empty, or
its endpoint was never called (a route change — run `probe`).

**The OpenPai API is off on purpose.** There is an official API, but an account
without granted interface permissions answers every endpoint with
`code=401001 saas用户暂无接口调用权限`. Rather than retry that forever, this
skill never probes it; `api-status` states the requirement. If you want it, ask
an Alpha派 administrator to grant access, then supply `ALPHAPAI_API_KEY`.

**Sessions expire within minutes.** Every command logs in again inside the same
process that scrapes. Don't build anything on "it should still be logged in".

---

## Scheduling

```bash
python scripts/alphapai_notes.py schedule plan --time 08:30          # just show the plan
python scripts/alphapai_notes.py schedule install --time 08:30 --apply
python scripts/alphapai_notes.py schedule status
python scripts/alphapai_notes.py schedule remove --apply
```

Windows uses Task Scheduler, macOS a launchd user agent, Linux prints a cron
line. Nothing is written until `--apply` — a scheduled task is a persistent
change to your machine, so it asks first by design.

One real constraint: the task runs **as you, while you're logged in**. Your
credential and browser profile are per-user and the download needs a live
session, so a "run whether the user is logged on or not" task will not work.

---

## Scope

Read-only. It never uploads, renames, shares, syncs to PaiWork or deletes
anything on AlphaPai. Board captures take list metadata, not article full text.

Your captured notes are personal meeting content: they belong in your vault,
not in a git repository. The `.gitignore` here already excludes them.

---

## For maintainers

- [`SKILL.md`](SKILL.md) — the agent-facing contract, including the
  verification checklist to run after any change.
- [`references/endpoints.md`](references/endpoints.md) — verified endpoints,
  the four response envelope shapes, and the DOM traps behind the download flow.

One warning worth repeating from those docs: the row action menus have DOM
order that does not follow visible row order, and every row keeps a hidden menu
in the DOM. Selecting a menu item by index, or by a document-wide text match,
downloads the **neighbouring record's** document under the correct filename — a
silent, entirely plausible-looking wrong answer. Rows are therefore matched by
the vertical position of their title, and the submenu leaf via
`elementFromPoint`. After any change to that code, verify that a note's body
matches its filename.
