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

**2. It never sees your password, and neither does your agent.** The
credential lives in your OS keystore — Windows Credential Manager or the macOS
Keychain. You type it once into your own console window; the skill reads it
only at the instant it fills the login form, inside a browser profile reserved
for AlphaPai. No command takes a password as an argument, and an agent driving
this only ever sees status words like `authenticated`. Everything else it
reads comes from responses the logged-in page fetched for itself.

**3. There are two different datasets.** *Your* 转记 records (things you
uploaded or recorded) and the *platform's* 发现 boards (what everyone sees).
Different commands, different folders. The first gives you full note documents;
the second only list metadata — titles, codes, times — not article full text.

**4. Everything lands in your vault.** You set the vault once. After that notes
go to `<vault>/AlphaPai/` and board snapshots to `<vault>/AlphaPai/boards/`,
named by date so they sort naturally. Until you set one, they go to
`~/AlphaPaiNotes` and every run says so.

---

## How to ask for things

Say it normally. These are the patterns that work, and what the agent will
actually run:

| What you want | Say something like | Runs |
| --- | --- | --- |
| See what's on the platform | 「看看我 AlphaPai 上有哪些转记」 | `list` |
| Sync new notes into Obsidian | 「把我 AlphaPai 上的新纪要同步到 Obsidian」 | `pull` |
| One specific meeting | 「把那个讲英伟达 GTC 的纪要拉下来」 | `pull --match "GTC"` (add `--include-examples` on a new account) |
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
- **Set the vault before your first sync.** Without one, notes land in
  `~/AlphaPaiNotes` instead — deliberately a fixed spot in your home directory
  rather than the current folder, so personal meeting notes never land inside
  whatever repository you happened to be standing in. The run says so in
  `warnings`, but it is easier to run `config --set-vault` first.
- **Say which artifacts you want.** There are four kinds: `ai_summary` (the
  default), `transcript` (逐字稿 — raw speaker-by-speaker text, worth asking for
  when you want the actual quote rather than the summary's paraphrase), `audio`
  (the original recording, when the record has one), and `all` (AlphaPai's own
  bundle).
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
alphapai_session_id: "eD0eHgjxHCG2..."
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

`alphapai_session_id` is named for what it is: AlphaPai re-encrypts record ids
on every session, so it identifies the fetch, not the meeting. Do not build
dedupe or links on it — the filename and `title` are the stable handles.

Headings, lists and tables survive the conversion from `.docx`. The transcript
variant lands beside it as `<name>-transcript.md` and keeps speaker labels
(`讲话人1：`). Long titles get squeezed to fit the path limit, but the
`-transcript` tag is always preserved, so the two never collide. `--format md,docx,pdf` also gives you the original and a PDF;
Markdown alone is the default because it's the version you can search, link and
quote from.

A board snapshot looks different — it is a dated table per feed, not prose:

```markdown
---
title: "AlphaPai 机构热议"
board: "hot_topics"
rows: 55
captured_at: "2026-09-22T14:10:03+08:00"
origin: "AlphaPai 发现"
---

## 当期热议话题

| group | title | code | org | time |
| --- | --- | --- | --- | --- |
|  | 长鑫G5平台DRAM正式宣布量产 |  |  |  |
|  | Agent负载推动服务器CPU需求重估 |  |  |  |

## 机构榜单个股（公募榜）

| group | title | code | org | time |
| --- | --- | --- | --- | --- |
| publicList | 中际旭创 | 300308.SZ |  |  |
```

Each feed is rendered for what it actually carries, not through one generic
table: hot topics keep their full ~400-character summary and the related
stocks with day moves, roadshows keep their digest and guest list, sector rows
keep the catalyst event. Columns a feed never populates are dropped rather than
printed as a column of dashes.

A `.json` sidecar is written next to every board note by default. The markdown
is a readable view; the JSON is the complete captured rows, and a past day
cannot be re-scraped. Use `--no-json` if you only want the note.

---

## Setup

Once, before anything works:

```bash
pip install playwright python-docx
python -m playwright install          # uses your installed Edge, not a bundled browser
```

Store your AlphaPai login. This opens a separate window — you type it there,
and it goes straight into the OS keystore:

```bash
# Windows
powershell -ExecutionPolicy Bypass -File scripts/Open-AlphaPaiCredentialPrompt.ps1

# macOS
security add-generic-password -s 'AlphaPai:Login' -a '<your account>' -w

# either platform: confirm it landed, without revealing it
python scripts/alphapai_notes.py auth status
```

Then point it at your vault — and for this part, just tell the agent:

> 「帮我把 AlphaPai 的笔记配到我的 Obsidian vault」

It will look for your vaults, show you what it found, and ask which one you
want — you paste a path or pick one, and it sets it for you. A vault path is
not a secret, so there is no reason to make you type a command for it.

If you would rather do it yourself:

```bash
python scripts/alphapai_notes.py config --detect-vaults
python scripts/alphapai_notes.py config --set-vault "D:/Obsidian/MyVault" --set-subdir AlphaPai
python scripts/alphapai_notes.py probe
```

Note the asymmetry with the credential above: the password you type into your
own window because an agent must never handle it; the vault path you just say
in chat, because it is ordinary configuration.

### Can I just put my login in a `.env`?

You can, and the skill will read `ALPHAPAI_USERNAME` / `ALPHAPAI_PASSWORD` from
the environment or from an untracked `.env` (copy `.env.example`). But it is
the *second* choice on purpose, and it's worth knowing why.

A `.env` is plaintext at rest. Anything running as you can read it, it follows
the folder into backups and sync clients, and the classic accident — committing
it — publishes your password to everyone with repo access. The OS keystore has
none of those properties: it is encrypted per-user, ACL-protected, and there is
no path by which it ends up in git.

The industry convention is roughly:

| Where | Normal practice |
| --- | --- |
| Your own workstation | OS keystore, or a password manager's CLI |
| CI / containers | secrets injected as environment variables by the platform |
| Servers / production | a secret manager (Vault, AWS/Azure) with rotation and audit |
| `.env` files | non-secret config, or low-value local dev credentials |

Environment *variables* as the injection channel are standard — that part of
twelve-factor is fine. What isn't standard is a long-lived personal password
sitting in a file next to your code. So: keystore on your laptop, `.env` when
there's no keystore to use (a container, a Linux box, CI).

Two guardrails are built in: the loader refuses a `.env` that git tracks, and
`auth status` reports which source a login would come from, so an accidental
downgrade to plaintext is visible rather than silent.

Settings live in your own config directory (`%APPDATA%/alphapai-notes/` on
Windows), never in this repo — so the repo stays portable and your paths stay
yours.

---

## Reading the output

Every command prints JSON. The fields that matter:

| Field | Means |
| --- | --- |
| `status` | `ok`, `partial` (something failed but the rest worked), or `failed` |
| `warnings` | non-fatal things you should know — e.g. no vault configured |
| `records[].kinds[<kind>].written` | the files actually written, by format |
| `served_as` | the filename AlphaPai served, useful to confirm you got the right record |
| `skipped: "already present"` | that note is already in the vault, so it wasn't re-downloaded |
| `skipped: "not available for this record"` | the record has no such artifact (check `list` → `available`) |
| `failed_artifacts` | how many downloads failed; `0` with `status: ok` means a clean run |
| `restarts` | how many times the browser had to be rebuilt; not a failure |
| `boards[].rows` | distinct rows captured; `0` comes with a `hint` explaining which kind of empty |

If `pull` reports `records: []` with `status: ok`, nothing matched your filter —
most often because the only records present are AlphaPai's hidden demo rows.
Run `list --include-examples` to see everything the account actually has.

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
items. If the status says `ok`, it finished. This is not rare — two restarts in
a single run is normal, which is why the budget is 4 (`--max-restarts`). If it
is exhausted, the remaining items say so explicitly and re-running picks them
up.

**An empty 分析师 board usually just means you follow no analysts.** The tool
distinguishes the two cases and says which: the feed answered and was empty, or
its endpoint was never called (a route change — run `probe`).

**The OpenPai API is off on purpose.** There is an official API, but an account
without granted interface permissions answers every endpoint with
`code=401001 saas用户暂无接口调用权限`. Rather than retry that forever, this
skill never probes it; `api-status` states the requirement. If you want it, ask
an Alpha派 administrator to grant access, then supply `ALPHAPAI_API_KEY`.

**It ticks the login page's "已阅读并同意" box for you.** You asked for that,
and the authorisation is scoped to exactly that one checkbox on the login form
— no cookie banner, consent dialog or other terms prompt is ever accepted on
your behalf. In practice AlphaPai ships it pre-ticked, so most runs report
`already_accepted`; `auth login` prints the outcome either way.

**The first run on a new machine needs the credential prompt, not a config
file.** There is no password field anywhere in the repo or its config; the
setup step opens a console window for you instead. That is the whole
onboarding cost, and it is once per machine.

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
not in a git repository. This repo's own `.gitignore` excludes them — but a
vault is often itself a repo, so the skill checks: if notes are landing inside
a git repository that does not ignore them, every run says so and tells you the
one line to add. Do not ignore that warning; meeting content in a pushed repo
is hard to take back.

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
