# alphapai-notes

Capture [AlphaPai](https://alphapai-web.rabyte.cn) content into an Obsidian
vault: PaiPai 转记 records (AI 纪要, 逐字稿, recordings) as Markdown with YAML
frontmatter, plus read-only list captures of the 发现 boards (机构热议, 推荐,
分析师, 自选, 板块).

Markdown is the default output; `.docx` originals and PDF are optional.

## How it authenticates

It does not. Login is delegated to the **viaim-auth** helper, which keeps the
credential in Windows Credential Manager and types it into a dedicated browser
profile. This tool never reads a password, token, cookie or `Authorization`
header — it only reads responses that the already-logged-in page fetched for
itself.

## Install

```bash
pip install playwright python-docx
python -m playwright install        # Edge channel is used, not a bundled browser
```

Then point it at a vault:

```bash
python scripts/alphapai_notes.py config --detect-vaults
python scripts/alphapai_notes.py config --set-vault "/path/to/vault"
python scripts/alphapai_notes.py probe
```

Set `VIAIM_AUTH_SCRIPTS` if viaim-auth is not a sibling directory.

## Use

```bash
python scripts/alphapai_notes.py list
python scripts/alphapai_notes.py pull
python scripts/alphapai_notes.py pull --match "GTC" --kinds ai_summary,transcript --format md,docx
python scripts/alphapai_notes.py boards --board hot_topics,recommend,watchlist
python scripts/alphapai_notes.py schedule install --time 08:30 --apply
```

Select records with `--match`, not `--id`: AlphaPai re-encrypts record ids
every session.

## Known constraints

- **Downloads require a headed browser.** The app delivers files by navigating
  to its object store and headless Edge drops that download silently. `pull`
  runs headed by default; `--offscreen` keeps the window out of the way.
  `list` and `boards` are fine headless.
- **Sessions are short-lived**, so every command logs in within the same
  process that scrapes.
- A headed browser occasionally dies mid-run; `pull` rebuilds it and resumes
  (`--max-restarts`, default 2).
- The OpenPai API route is optional, off by default, and never probed. An
  unprovisioned key returns `code=401001 saas用户暂无接口调用权限`; confirm
  entitlement with an Alpha派 administrator first. See `api-status`.

`references/endpoints.md` documents the verified endpoints, response shapes
and the DOM traps behind the download flow.

## Scope

Read-only. It never uploads, renames, shares, syncs to PaiWork or deletes.
Board captures take list metadata, not article full text. Captured notes are
personal meeting content — keep them in your vault, not in a repository.
