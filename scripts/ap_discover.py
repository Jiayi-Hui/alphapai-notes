"""Discovery-side boards: 机构热议 / 推荐 / 分析师 / 自选 / 板块.

These are read-only list captures. The app requests its own feed with its own
credentials and this module reads the rows out of the response - deliberately
metadata only (title, source, time, identifiers), not article or report full
text. Keeping it to list level is both lighter and avoids hoovering up
licensed research bodies.

Each board is defined by the section it lives in plus the API fragments that
identify its data, so a cosmetic UI change does not silently produce an empty
capture: if none of a board's needles are seen, the board reports `found: 0`
rather than pretending success.
"""

from __future__ import annotations

from dataclasses import dataclass

from ap_routes import resolve
from ap_session import ApiRecorder, dedupe_rows, extract_rows, goto

# Fields worth surfacing in a summary; boards disagree on their names, so we
# probe a list of candidates rather than assuming one schema.
TITLE_KEYS = ("title", "name", "topicName", "stockName", "analystName",
              "reportTitle", "topic", "secName", "sectorName")
TIME_KEYS = ("publishTime", "createTime", "actualPublishTime", "updateTime",
             "time", "date", "batchTime")
CODE_KEYS = ("stockCode", "code", "secCode", "symbol", "sectorCode")
ORG_KEYS = ("institutionName", "orgName", "institution", "company",
            "brokerName", "source")


@dataclass(frozen=True)
class Board:
    key: str
    label: str
    section: str
    needles: tuple[str, ...]
    tabs: tuple[str, ...] = ()
    note: str = ""


BOARDS: dict[str, Board] = {
    "hot_topics": Board(
        key="hot_topics",
        label="机构热议",
        section="discover",
        needles=("mix/hot/topic/current/batch/list",
                 "mix/hot/topic/stock/list",
                 "mix/hot/topic/report/latest"),
        tabs=("公募榜", "私募榜", "保险榜"),
        note="institution-level hot topics and the 公募/私募/保险 rankings",
    ),
    "recommend": Board(
        key="recommend",
        label="推荐",
        section="discover",
        needles=("reading/report/hot/recommend",
                 "reading/roadshow/summary/hot/recommend",
                 "reading/stock/hot/recommend"),
        note="recommended reports and roadshow summaries",
    ),
    "analyst": Board(
        key="analyst",
        label="分析师",
        section="discover",
        needles=("information/flow/analyst/information/list",),
        note="analyst information flow",
    ),
    "watchlist": Board(
        key="watchlist",
        label="自选",
        section="discover",
        needles=("information/flow/stock/follow/query",
                 "information/flow/stock/follow/list",
                 "information/flow/stock/information/list",
                 "reading/stock/follow/group"),
        note="followed stocks and their groups",
    ),
    "sector": Board(
        key="sector",
        label="板块",
        section="sector",
        needles=("reading/sector/prosperity/list",
                 "reading/sector/market/performance",
                 "reading/sector/chain/top/list"),
        note="sector prosperity and performance tables",
    ),
}


def _click_tab(page, label: str) -> bool:
    try:
        page.get_by_text(label, exact=True).first.click(timeout=5000)
        page.wait_for_timeout(4000)
        return True
    except Exception:
        return False


def capture_board(page, cfg, board: Board, include_tabs: bool = True,
                  log=lambda m: None) -> dict:
    """Visit the board's view and collect whatever rows the app loads."""
    recorder = ApiRecorder(page, keep_paths=board.needles)
    path = resolve(page, board.section, cfg, log=log)
    # resolve() may already have navigated; make sure the view is freshly
    # loaded so the feed requests fire with the recorder attached
    goto(page, path)

    groups: dict[str, list[dict]] = {}

    def harvest(tag: str) -> None:
        for needle in board.needles:
            rows: list[dict] = []
            for rec in recorder.matching(needle):
                rows.extend(extract_rows(rec["payload"]))
            if rows:
                bucket = groups.setdefault(f"{needle}" if tag == "default" else f"{needle}@{tag}", [])
                bucket.extend(rows)

    for _ in range(8):
        if recorder.records:
            break
        page.wait_for_timeout(1000)
    harvest("default")

    if include_tabs and board.tabs:
        for tab in board.tabs:
            recorder.clear()
            if not _click_tab(page, tab):
                log(f"board[{board.key}]: tab '{tab}' not clickable, skipped")
                continue
            for _ in range(6):
                if recorder.records:
                    break
                page.wait_for_timeout(1000)
            harvest(tab)

    datasets = {k: dedupe_rows(v) for k, v in groups.items()}
    total = sum(len(v) for v in datasets.values())
    result = {
        "board": board.key,
        "label": board.label,
        "section": board.section,
        "path": path,
        "note": board.note,
        "found": total,
        "datasets": datasets,
    }
    if total == 0:
        # Distinguish "the feed is genuinely empty for this account" from
        # "we are looking in the wrong place" - the analyst feed, for
        # instance, returns null groups until some analysts are followed.
        answered = any(recorder.matching(n) for n in board.needles)
        result["hint"] = (
            "the view loaded and its endpoint answered, but returned no rows "
            "- this feed is empty for this account (e.g. nothing followed yet)"
            if answered else
            "the board's endpoints were never called - the view or its API "
            "path has probably changed; run `probe` to re-discover routes"
        )
        log(f"board[{board.key}]: 0 rows - {result['hint']}")
    return result


def _pick(row: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = row.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            return str(v).strip()
    return None


def summarize_rows(rows: list[dict], limit: int = 50) -> list[dict]:
    """Compact, human-readable view of heterogeneous board rows."""
    out = []
    for row in rows[:limit]:
        out.append({
            "group": row.get("_group"),
            "title": _pick(row, TITLE_KEYS),
            "code": _pick(row, CODE_KEYS),
            "org": _pick(row, ORG_KEYS),
            "time": _pick(row, TIME_KEYS),
        })
    return out


def resolve_boards(names: list[str] | None) -> list[Board]:
    if not names:
        return list(BOARDS.values())
    picked = []
    for n in names:
        board = BOARDS.get(n)
        if board is None:
            raise ValueError(f"unknown board '{n}'. Known: {sorted(BOARDS)}")
        picked.append(board)
    return picked
