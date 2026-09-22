#!/usr/bin/env python3
"""alphapai-notes - capture AlphaPai notes into an Obsidian vault.

Commands
    config     inspect or set the vault, formats and kinds
    probe      log in and verify every view still resolves
    list       list 转记 records (metadata only)
    pull       download notes and write them into the vault
    boards     capture 机构热议 / 推荐 / 分析师 / 自选 / 板块 list data
    schedule   plan, install, inspect or remove a recurring capture
    api-status report whether the optional OpenPai API route is usable

Credential policy: this tool never handles a password, token, cookie or
Authorization header. Login is delegated to the viaim-auth helper, which
types a Credential Manager secret into a dedicated browser profile; every
data read here comes from responses the logged-in page fetched itself.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ap_config import Config, guess_vaults  # noqa: E402
from ap_convert import (KINDS, Note, artifact_extension, fetch_artifact,  # noqa: E402
                        list_notes, safe_filename)
from ap_discover import BOARDS, capture_board, resolve_boards, summarize_rows  # noqa: E402
from ap_export import (ExportError, build_note, docx_to_markdown,  # noqa: E402
                       docx_to_pdf, fit_path, write_artifact, write_text)
from ap_routes import RouteError, SECTIONS, resolve, resolve_all  # noqa: E402
from ap_schedule import ScheduleError, build_plan, install, remove, status  # noqa: E402
from ap_session import SessionError, session  # noqa: E402

VALID_FORMATS = ("md", "docx", "pdf")


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1))


def make_logger(quiet: bool):
    def log(message: str) -> None:
        if not quiet:
            print(message, file=sys.stderr)
    return log


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def cmd_config(args) -> int:
    cfg = Config.load()
    changed = False

    if args.detect_vaults:
        emit({"status": "ok", "detected_vaults": guess_vaults(),
              "hint": "set one with: config --set-vault <path>"})
        return 0

    if args.set_vault:
        try:
            resolved = cfg.set_vault(args.set_vault)
        except ValueError as exc:
            emit({"status": "failed", "error": str(exc)})
            return 2
        changed = True
        if not cfg.vault_looks_real():
            print(f"note: {resolved} has no .obsidian/ folder - it will still "
                  "be used, but confirm it is the vault root",
                  file=sys.stderr)
    if args.set_subdir:
        cfg.data["notes_subdir"] = args.set_subdir
        changed = True
    if args.set_formats:
        fmts = _split(args.set_formats)
        bad = [f for f in fmts if f not in VALID_FORMATS]
        if bad:
            emit({"status": "failed", "error": f"unknown formats {bad}",
                  "valid": list(VALID_FORMATS)})
            return 2
        cfg.data["formats"] = fmts
        changed = True
    if args.set_kinds:
        kinds = _split(args.set_kinds)
        bad = [k for k in kinds if k not in KINDS]
        if bad:
            emit({"status": "failed", "error": f"unknown kinds {bad}",
                  "valid": sorted(KINDS)})
            return 2
        cfg.data["kinds"] = kinds
        changed = True
    if args.include_examples is not None:
        cfg.data["skip_examples"] = not args.include_examples
        changed = True
    if args.clear_routes:
        cfg.data["routes"] = {}
        changed = True

    saved = str(cfg.save()) if changed else None
    emit({"status": "ok", "changed": changed, "saved_to": saved,
          "config": cfg.describe()})
    return 0


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def cmd_probe(args) -> int:
    cfg = Config.load()
    log = make_logger(args.quiet)
    with session(headless=not args.headed) as (ctx, page, state):
        routes = resolve_all(page, cfg, log=log)
        cfg.save()
    ok = [k for k, v in routes.items() if v]
    emit({"status": "ok" if len(ok) == len(SECTIONS) else "partial",
          "login": state, "routes": routes,
          "resolved": len(ok), "of": len(SECTIONS),
          "config_file": str(cfg.describe()["config_file"])})
    return 0 if ok else 2


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def _select_notes(notes: list[Note], cfg: Config, args) -> list[Note]:
    skip_examples = cfg.data.get("skip_examples", True)
    if args.include_examples:
        skip_examples = False
    picked = [n for n in notes if not (skip_examples and n.is_example)]
    if args.id:
        wanted = set(_split(args.id))
        picked = [n for n in picked if n.id in wanted]
    if getattr(args, "match", None):
        needle = args.match.casefold()
        picked = [n for n in picked if needle in n.title.casefold()]
    if args.limit:
        picked = picked[: args.limit]
    return picked


def cmd_list(args) -> int:
    cfg = Config.load()
    log = make_logger(args.quiet)
    with session(headless=not args.headed) as (ctx, page, state):
        resolve(page, "convert", cfg, log=log)
        notes = list_notes(page)
        cfg.save()
    selected = _select_notes(notes, cfg, args)
    emit({"status": "ok", "login": state,
          "total_seen": len(notes), "selected": len(selected),
          "examples_hidden": len(notes) - len(selected) if not args.include_examples else 0,
          "notes": [n.summary() for n in selected]})
    return 0


# ---------------------------------------------------------------------------
# pull
# ---------------------------------------------------------------------------

def _note_stem(note: Note, kind: str) -> str:
    date = (note.created or "")[:10].replace("/", "-") or "undated"
    stem = f"{date}-{safe_filename(note.title)}"
    if kind == "transcript":
        stem += "-transcript"
    elif kind == "all":
        stem += "-bundle"
    return stem


def _note_meta(note: Note, kind: str) -> dict:
    return {
        "title": note.title,
        "alphapai_id": note.id,
        "created": note.created,
        "duration": note.duration,
        "status": note.status,
        "source": note.source_kind,
        "kind": kind,
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "origin": "AlphaPai PaiPai 转记",
    }


def _export_one(note: Note, kind: str, blob: bytes, out_dir: Path,
                formats: list[str], log) -> dict:
    stem = _note_stem(note, kind)
    suffix = artifact_extension(kind, blob)
    written: dict[str, str] = {}
    warnings: list[str] = []

    is_doc = suffix == ".docx"
    # the raw artifact is needed on disk for md/pdf conversion anyway
    docx_path = None
    if is_doc and ("docx" in formats or "md" in formats or "pdf" in formats):
        docx_path = write_artifact(out_dir, stem, blob, ".docx")

    if not is_doc:
        # audio or any non-document artifact: store as-is
        media = write_artifact(out_dir, stem, blob, suffix)
        written[suffix.lstrip(".")] = str(media)
        return {"written": written, "warnings": warnings}

    if "md" in formats:
        try:
            body = docx_to_markdown(docx_path)
            note_text = build_note(_note_meta(note, kind), body,
                                   tags=["alphapai", "meeting-notes", kind])
            written["md"] = str(write_text(out_dir, stem, note_text))
        except ExportError as exc:
            warnings.append(f"markdown export failed: {exc}")

    if "pdf" in formats:
        try:
            written["pdf"] = str(docx_to_pdf(docx_path, fit_path(out_dir, stem, ".pdf")))
        except ExportError as exc:
            warnings.append(f"pdf export failed: {exc}")

    if "docx" in formats:
        written["docx"] = str(docx_path)
    elif docx_path and docx_path.exists():
        # docx was only an intermediate; do not leave it in the vault
        try:
            docx_path.unlink()
        except OSError:
            written["docx"] = str(docx_path)

    return {"written": written, "warnings": warnings}


def cmd_pull(args) -> int:
    cfg = Config.load()
    log = make_logger(args.quiet)
    formats = _split(args.format) or list(cfg.data.get("formats") or ["md"])
    bad = [f for f in formats if f not in VALID_FORMATS]
    if bad:
        emit({"status": "failed", "error": f"unknown formats {bad}",
              "valid": list(VALID_FORMATS)})
        return 2
    kinds = _split(args.kinds) or list(cfg.data.get("kinds") or ["ai_summary"])
    bad = [k for k in kinds if k not in KINDS]
    if bad:
        emit({"status": "failed", "error": f"unknown kinds {bad}",
              "valid": sorted(KINDS)})
        return 2

    out_dir = cfg.output_dir(args.out)
    results: list[dict] = []
    boards_result: list[dict] = []

    # Downloads only materialise in a headed browser (headless Edge drops
    # them), so pull runs headed by default and parks the window off-screen
    # unless the user asked to watch it.
    headless = bool(args.headless)
    offscreen = bool(getattr(args, "offscreen", False)) and not headless
    if headless:
        log("pull: --headless requested; note that AlphaPai downloads usually "
            "do not arrive without a headed browser")

    # A headed Edge occasionally dies mid-scrape. Rather than losing the whole
    # run, each pass works through what is still outstanding and a new session
    # picks up the remainder. Records are re-matched by title because AlphaPai
    # re-encrypts record ids on every session.
    outstanding: list[tuple[str, str]] | None = None   # (title, kind)
    by_title: dict[str, dict] = {}
    restarts = 0
    state = "unknown"
    session_notes: list = []

    while restarts <= args.max_restarts:
        crashed = False
        with session(headless=headless, offscreen=offscreen) as (ctx, page, state):
            resolve(page, "convert", cfg, log=log)
            session_notes = list_notes(page)
            selected = _select_notes(session_notes, cfg, args)
            if outstanding is None:
                outstanding = [(n.title, k) for n in selected for k in kinds]
                log(f"pull: {len(selected)} of {len(session_notes)} record(s) "
                    f"selected -> {out_dir}")
            else:
                log(f"pull: resuming, {len(outstanding)} item(s) left")

            lookup = {n.title: n for n in selected}
            remaining: list[tuple[str, str]] = []
            for pos, (title, kind) in enumerate(outstanding):
                if crashed:
                    remaining.extend(outstanding[pos:])
                    break
                note = lookup.get(title)
                entry = by_title.setdefault(title, {
                    "id": note.id if note else None, "title": title,
                    "created": note.created if note else None, "kinds": {}})
                if note is None:
                    entry["kinds"][kind] = {"error": "record no longer listed"}
                    continue
                entry["id"], entry["created"] = note.id, note.created

                if kind != "all" and kind not in note.available():
                    entry["kinds"][kind] = {"skipped": "not available for this record"}
                    continue
                stem = _note_stem(note, kind)
                if args.skip_existing and fit_path(out_dir, stem, ".md").exists():
                    entry["kinds"][kind] = {"skipped": "already present"}
                    continue
                try:
                    log(f"  fetching {kind} for '{title[:40]}'")
                    blob, filename = fetch_artifact(page, title, kind,
                                                    timeout_ms=args.timeout * 1000)
                    record = _export_one(note, kind, blob, out_dir, formats, log)
                    if filename:
                        record["served_as"] = filename
                    entry["kinds"][kind] = record
                except Exception as exc:
                    if "has been closed" in str(exc) or "Target closed" in str(exc):
                        crashed = True
                        remaining.append((title, kind))
                        log("  ! browser session ended; will restart and resume")
                        continue
                    entry["kinds"][kind] = {"error": f"{type(exc).__name__}: {exc}"}
                    log(f"  ! {kind} failed: {exc}")

            outstanding = remaining
            if not crashed and args.with_boards:
                for board in resolve_boards(_split(args.boards) or None):
                    try:
                        captured = capture_board(page, cfg, board, log=log)
                        boards_result.append(_write_board(captured, out_dir, args))
                    except Exception as exc:
                        boards_result.append({"board": board.key,
                                              "error": f"{type(exc).__name__}: {exc}"})
            cfg.save()

        if not outstanding:
            break
        restarts += 1
        if restarts > args.max_restarts:
            for title, kind in outstanding:
                by_title.setdefault(title, {"title": title, "kinds": {}})
                by_title[title]["kinds"][kind] = {
                    "error": "browser kept ending before this item completed"}
            break
        log(f"pull: restarting browser ({restarts}/{args.max_restarts})")

    results = list(by_title.values())
    failures = sum(1 for r in results for v in r["kinds"].values() if "error" in v)
    payload = {"status": "ok" if not failures else "partial",
               "login": state, "output_dir": str(out_dir),
               "formats": formats, "kinds": kinds, "restarts": restarts,
               "records": results, "boards": boards_result,
               "failed_artifacts": failures}
    emit(payload)
    return 0 if not failures else 2


# ---------------------------------------------------------------------------
# boards
# ---------------------------------------------------------------------------

def _board_markdown(captured: dict) -> str:
    meta = {
        "title": f"AlphaPai {captured['label']}",
        "board": captured["board"],
        "path": captured["path"],
        "rows": captured["found"],
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "origin": "AlphaPai 发现",
    }
    lines = []
    for dataset, rows in captured["datasets"].items():
        lines.append(f"## {dataset}")
        lines.append("")
        rows_summary = summarize_rows(rows)
        if not rows_summary:
            lines.extend(["_no rows_", ""])
            continue
        cols = ("group", "title", "code", "org", "time")
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for r in rows_summary:
            cells = [str(r.get(k) or "").replace("|", "\\|") for k in cols]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    return build_note(meta, "\n".join(lines),
                      tags=["alphapai", "discover", captured["board"]])


def _write_board(captured: dict, out_dir: Path, args) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d")
    stem = f"{stamp}-alphapai-{captured['board']}"
    target = out_dir / "boards"
    written = {}
    if captured["found"]:
        written["md"] = str(write_text(target, stem, _board_markdown(captured)))
        if args.raw_json:
            written["json"] = str(write_text(
                target, stem,
                json.dumps(captured["datasets"], ensure_ascii=False, indent=1),
                suffix=".json"))
    record = {"board": captured["board"], "label": captured["label"],
              "path": captured["path"], "rows": captured["found"],
              "written": written}
    if captured.get("hint"):
        record["hint"] = captured["hint"]
    return record


def cmd_boards(args) -> int:
    cfg = Config.load()
    log = make_logger(args.quiet)
    out_dir = cfg.output_dir(args.out)
    boards = resolve_boards(_split(args.board) or None)
    out: list[dict] = []
    with session(headless=not args.headed) as (ctx, page, state):
        for board in boards:
            try:
                captured = capture_board(page, cfg, board, log=log)
                record = _write_board(captured, out_dir, args)
                if args.preview:
                    record["preview"] = {
                        k: summarize_rows(v, limit=5)
                        for k, v in captured["datasets"].items()}
                out.append(record)
            except RouteError as exc:
                out.append({"board": board.key, "error": str(exc)})
            except Exception as exc:
                out.append({"board": board.key,
                            "error": f"{type(exc).__name__}: {exc}"})
        cfg.save()
    empty = [b["board"] for b in out if not b.get("rows")]
    emit({"status": "ok" if not empty else "partial", "login": state,
          "output_dir": str(out_dir), "boards": out, "empty": empty})
    return 0 if not empty else 2


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

def cmd_schedule(args) -> int:
    cfg = Config.load()
    if args.action == "status":
        emit({"status": "ok", "schedule": status()})
        return 0
    if args.action == "remove":
        emit({"status": "ok", "removal": remove(apply=args.apply)})
        return 0

    kinds = _split(args.kinds) or list(cfg.data.get("kinds") or ["ai_summary"])
    formats = _split(args.format) or list(cfg.data.get("formats") or ["md"])
    try:
        plan = build_plan(kinds, formats, args.time, args.frequency,
                          include_boards=args.with_boards,
                          offscreen=not args.visible)
    except ScheduleError as exc:
        emit({"status": "failed", "error": str(exc)})
        return 2

    warnings = []
    if not cfg.vault_path:
        warnings.append("no vault configured - scheduled runs would write to "
                        f"{cfg.output_dir()}. Set one with `config --set-vault`.")
    if args.action == "plan":
        emit({"status": "ok", "plan": plan.describe(), "warnings": warnings})
        return 0
    result = install(plan, apply=args.apply)
    emit({"status": "ok" if result.get("applied") or not args.apply else "failed",
          "result": result, "warnings": warnings})
    return 0


# ---------------------------------------------------------------------------
# api-status
# ---------------------------------------------------------------------------

def cmd_api_status(args) -> int:
    """Report on the optional OpenPai API route without probing it.

    The browser route is the supported path. The OpenPai API is only usable
    when the account has been granted endpoint permissions, so this command
    states what the user must confirm instead of firing calls that would just
    come back 401001.
    """
    key_present = bool(os.environ.get("ALPHAPAI_API_KEY"))
    emit({
        "status": "ok",
        "primary_route": "headless browser (viaim-auth login + response capture)",
        "api_key_env_set": key_present,
        "api_route_enabled": False,
        "requirement": (
            "The OpenPai API is optional and off by default. Before using it, "
            "confirm with your Alpha派 administrator that your account has "
            "interface permissions - an unprovisioned key returns "
            "'code=401001 saas用户暂无接口调用权限'. Then set ALPHAPAI_API_KEY "
            "and pass --use-api to the commands that support it."
        ),
        "note": ("This skill does not probe the API route on its own, so a "
                 "missing entitlement never turns into a silent retry loop."),
    })
    return 0


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    # Shared flags are declared on a parent parser so they work either before
    # or after the subcommand - the scheduled command line puts them last.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--headed", action="store_true",
                        help="show the browser window (debugging)")
    common.add_argument("--quiet", action="store_true",
                        help="suppress progress logs")

    p = argparse.ArgumentParser(
        prog="alphapai_notes",
        parents=[common],
        description="Capture AlphaPai notes into an Obsidian vault")
    sub = p.add_subparsers(dest="command", required=True)

    def add(name: str, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    c = add("config", help="inspect or change settings")
    c.add_argument("--show", action="store_true", help="(default) print config")
    c.add_argument("--set-vault", metavar="PATH", help="Obsidian vault root")
    c.add_argument("--set-subdir", metavar="NAME", help="folder inside the vault")
    c.add_argument("--set-formats", metavar="LIST", help="md,docx,pdf")
    c.add_argument("--set-kinds", metavar="LIST",
                   help="ai_summary,transcript,audio,all")
    c.add_argument("--detect-vaults", action="store_true",
                   help="look for Obsidian vaults on this machine")
    c.add_argument("--include-examples", dest="include_examples",
                   action="store_true", default=None,
                   help="stop hiding AlphaPai's 样例 rows")
    c.add_argument("--clear-routes", action="store_true",
                   help="drop cached routes so they are rediscovered")
    c.set_defaults(func=cmd_config)

    pr = add("probe", help="log in and verify all routes resolve")
    pr.set_defaults(func=cmd_probe)

    ls = add("list", help="list 转记 records")
    ls.add_argument("--id", help="only these record ids (comma separated)")
    ls.add_argument("--match", metavar="TEXT",
                    help="only records whose title contains TEXT "
                         "(ids change every session, so prefer this)")
    ls.add_argument("--limit", type=int)
    ls.add_argument("--include-examples", action="store_true")
    ls.set_defaults(func=cmd_list)

    pl = add("pull", help="download notes into the vault")
    pl.add_argument("--id", help="only these record ids (comma separated)")
    pl.add_argument("--match", metavar="TEXT",
                    help="only records whose title contains TEXT "
                         "(ids change every session, so prefer this)")
    pl.add_argument("--kinds", help="ai_summary,transcript,audio,all")
    pl.add_argument("--format", help="md,docx,pdf")
    pl.add_argument("--out", help="output directory (overrides the vault)")
    pl.add_argument("--limit", type=int)
    pl.add_argument("--timeout", type=int, default=60,
                    help="seconds to wait per artifact (default 60)")
    pl.add_argument("--max-restarts", type=int, default=2,
                    help="how many times to rebuild the browser if it "
                         "dies mid-run (default 2)")
    pl.add_argument("--include-examples", action="store_true")
    pl.add_argument("--headless", action="store_true",
                    help="force a headless browser; AlphaPai downloads "
                         "generally fail this way (default: headed)")
    pl.add_argument("--offscreen", action="store_true",
                    help="push the headed window below the desktop so it does "
                         "not interrupt you (useful for scheduled runs)")
    pl.add_argument("--no-skip-existing", dest="skip_existing",
                    action="store_false", default=True,
                    help="re-download notes already in the vault")
    pl.add_argument("--with-boards", action="store_true",
                    help="also capture the discovery boards")
    pl.add_argument("--boards", help="which boards, when --with-boards is set")
    pl.add_argument("--raw-json", action="store_true",
                    help="also write each board's raw rows as JSON")
    pl.set_defaults(func=cmd_pull)

    bd = add("boards", help="capture discovery board lists")
    bd.add_argument("--board", help=f"comma separated: {','.join(sorted(BOARDS))}")
    bd.add_argument("--out", help="output directory (overrides the vault)")
    bd.add_argument("--raw-json", action="store_true")
    bd.add_argument("--preview", action="store_true",
                    help="include a few sample rows in the result")
    bd.set_defaults(func=cmd_boards)

    sc = add("schedule", help="recurring capture")
    sc.add_argument("action", choices=["plan", "install", "status", "remove"])
    sc.add_argument("--time", default="08:30", metavar="HH:MM")
    sc.add_argument("--frequency", default="daily",
                    choices=["daily", "weekly", "hourly"])
    sc.add_argument("--kinds")
    sc.add_argument("--format")
    sc.add_argument("--with-boards", action="store_true")
    sc.add_argument("--visible", action="store_true",
                    help="let the scheduled run show its browser window "
                         "(default: off-screen)")
    sc.add_argument("--apply", action="store_true",
                    help="actually write the schedule (otherwise dry run)")
    sc.set_defaults(func=cmd_schedule)

    ap = add("api-status",
             help="report whether the optional OpenPai API is usable")
    ap.set_defaults(func=cmd_api_status)

    return p


def main() -> int:
    _utf8_stdout()
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except SessionError as exc:
        emit({"status": "failed", "stage": "session", "error": str(exc)})
        return 3
    except RouteError as exc:
        emit({"status": "failed", "stage": "route", "error": str(exc)})
        return 4
    except KeyboardInterrupt:
        emit({"status": "aborted"})
        return 130
    except Exception as exc:
        emit({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
