"""Render board rows as notes worth reading, not just lists of titles.

The first version pushed every board through one generic
title/code/org/time table. That threw away the only parts with real
information: each hot topic carries a ~400-character `summary` and its related
stocks, roadshow rows carry a `content` digest, and sector rows carry the
catalyst `event`. All of it was already captured - the renderer simply dropped
it.

So each dataset gets a renderer matched to its actual shape, chosen by the
fields the API really populates (measured, not assumed):

    hot topics          topicName + summary(~428 chars) + stock[]
    institution ranking stockName + rank + dod, grouped 公募/私募/保险
    roadshow            title + content(~164) + guest + institution + date
    research reports    title + institution + author/analysts
    sector prosperity   name + yield3D + event(~77) + date
    sector performance  name + dod
    watchlist           code only - that endpoint returns nothing else

Anything unrecognised falls back to the generic table, so a new dataset
degrades to "less pretty" rather than "silently empty".
"""

from __future__ import annotations

import json
import re


def _clean(text) -> str:
    if text is None:
        return ""
    return " ".join(str(text).split()).strip()


def _cell(text) -> str:
    return _clean(text).replace("|", "\\|") or "—"


def _pct(value) -> str:
    """AlphaPai sends day-over-day moves as a number, occasionally as a str."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:+.2f}%"


def _stock_list(value, limit: int = 8) -> str:
    """`stock` is a list of {code, name, dod}; render it inline and readable."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return _clean(value)
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("name") or item.get("stockName"))
        code = _clean(item.get("code") or item.get("stockCode"))
        move = item.get("dod")
        label = " ".join(x for x in (name, code) if x)
        if move is not None:
            label += f" ({_pct(move)})"
        if label:
            parts.append(label)
    extra = len(value) - limit
    if extra > 0:
        parts.append(f"…+{extra}")
    return "、".join(parts)


def _names(value, limit: int = 6) -> str:
    """Several fields ship as [{code, name}] or a JSON string of the same."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                value = json.loads(stripped)
            except ValueError:
                return _clean(stripped)
        else:
            return _clean(stripped)
    if isinstance(value, dict):
        return _clean(value.get("name"))
    if not isinstance(value, list):
        return _clean(value)
    out = []
    for item in value[:limit]:
        if isinstance(item, dict):
            name = _clean(item.get("name") or item.get("analystName"))
        else:
            name = _clean(item)
        if name:
            out.append(name)
    return "、".join(out)


def _meta_line(parts) -> str:
    """Join metadata, dropping repeats that differ only in separators.

    `author` and `analysts` frequently carry the same people with different
    punctuation ("胡博新 吴景欢" vs "胡博新、吴景欢"), which printed the names twice.
    """
    out, seen = [], set()
    for part in parts:
        text = _clean(part)
        if not text:
            continue
        key = re.sub(r"[\s、,，/·]+", "", text)
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return " · ".join(out)


def _table(rows: list[dict], columns: list[tuple[str, callable]]) -> list[str]:
    """Render a table, dropping columns this feed never populates.

    These endpoints disagree about which fields they fill, so a fixed column
    set leaves whole columns of "—" - which is how the first version ended up
    with an org/time column that was blank for every row.
    """
    cells = [[_cell(fn(row)) for _, fn in columns] for row in rows]
    keep = [i for i in range(len(columns))
            if any(row[i] != "—" for row in cells)]
    if not keep:
        return []
    head = [columns[i][0] for i in keep]
    out = ["| " + " | ".join(head) + " |",
           "| " + " | ".join(["---"] * len(head)) + " |"]
    for row in cells:
        out.append("| " + " | ".join(row[i] for i in keep) + " |")
    out.append("")
    return out


# ---------------------------------------------------------------------------
# per-dataset renderers
# ---------------------------------------------------------------------------

def render_topics(rows: list[dict]) -> list[str]:
    """Hot topics: the summary is the whole point of this board."""
    out: list[str] = []
    for row in rows:
        title = _clean(row.get("topicName") or row.get("title")) or "（无标题）"
        out.append(f"### {title}")
        out.append("")
        summary = _clean(row.get("summary"))
        if summary:
            out.extend([summary, ""])
        stocks = _stock_list(row.get("stock"))
        if stocks:
            out.extend([f"**相关个股**：{stocks}", ""])
    return out


def render_ranking(rows: list[dict]) -> list[str]:
    """Institution rankings, kept grouped: 公募 / 私募 / 保险 are different lists."""
    labels = {"publicList": "公募榜", "privateList": "私募榜",
              "insuranceList": "保险榜"}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row.get("_group") or "", []).append(row)
    out: list[str] = []
    for key, items in groups.items():
        if len(groups) > 1 or key:
            out.extend([f"**{labels.get(key, key or '榜单')}**", ""])
        items = sorted(items, key=lambda r: r.get("rank") or 999)
        out.extend(_table(items, [
            ("排名", lambda r: r.get("rank")),
            ("个股", lambda r: r.get("stockName")),
            ("代码", lambda r: r.get("stockCode")),
            ("涨跌", lambda r: _pct(r.get("dod"))),
            ("机构类型", lambda r: r.get("institutionType")),
        ]))
    return out


def render_roadshows(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for row in rows:
        out.append(f"### {_clean(row.get('title')) or '（无标题）'}")
        out.append("")
        meta = _meta_line([
            row.get("publishInstitution"),
            _clean(row.get("roadshowDate") or row.get("date"))[:16],
            _names(row.get("industry")),
        ])
        if meta:
            out.extend([meta, ""])
        guest = _clean(row.get("guest"))
        if guest:
            out.extend([f"**嘉宾**：{guest}", ""])
        body = _clean(row.get("content") or row.get("aiSummary")
                      or row.get("mtSummary"))
        if body:
            out.extend([body, ""])
        stocks = _stock_list(row.get("stock"))
        if stocks:
            out.extend([f"**相关个股**：{stocks}", ""])
    return out


def render_reports(rows: list[dict]) -> list[str]:
    out: list[str] = []
    for row in rows:
        out.append(f"### {_clean(row.get('title') or row.get('otitle')) or '（无标题）'}")
        out.append("")
        meta = _meta_line([
            _names(row.get("institution")),
            _names(row.get("author")),
            _names(row.get("analysts")),
            _clean(row.get("recTime") or row.get("date"))[:16],
            _names(row.get("swIndustries") or row.get("industry")),
        ])
        if meta:
            out.extend([meta, ""])
        body = _clean(row.get("summaryCnHtml") or row.get("contentCn")
                      or row.get("content"))
        if body:
            out.extend([body[:1200], ""])
    return out


def render_sector_prosperity(rows: list[dict]) -> list[str]:
    out = _table(rows, [
        ("板块", lambda r: r.get("name")),
        ("3日涨跌", lambda r: _pct(r.get("yield3D") and r["yield3D"] * 100)),
        ("AlphaPai指数", lambda r: r.get("alphaPaiIndex")),
        ("龙头股", lambda r: _stock_list(r.get("leadingStocks"), limit=4)),
        ("日期", lambda r: r.get("date")),
    ])
    events = [r for r in rows if _clean(r.get("event"))]
    if events:
        out.extend(["**最新催化事件**", ""])
        for row in events:
            date = _clean(row.get("eventDate") or row.get("date"))[:10]
            out.append(f"- **{_clean(row.get('name'))}**"
                       f"{f'（{date}）' if date else ''}：{_clean(row.get('event'))}")
        out.append("")
    return out


def render_sector_performance(rows: list[dict]) -> list[str]:
    return _table(rows, [
        ("板块", lambda r: r.get("name")),
        ("涨跌", lambda r: _pct(r.get("dod"))),
        ("净流入", lambda r: r.get("netInflow")),
        ("上涨/下跌", lambda r: (f"{r.get('riseNum')}/{r.get('fallNum')}"
                                if r.get("riseNum") is not None else None)),
    ])


def render_chain(rows: list[dict]) -> list[str]:
    names = [_clean(r.get("name") or r.get("stockShort")) for r in rows]
    names = [n for n in names if n]
    return ["、".join(names), ""] if names else []


def render_codes(rows: list[dict]) -> list[str]:
    """The follow/query endpoint returns only {code, follow} - say so."""
    codes = [_clean(r.get("code") or r.get("stockCode")) for r in rows]
    codes = [c for c in codes if c]
    out = [f"共 {len(codes)} 只（该接口只返回代码，不含名称）", ""]
    out.extend(["、".join(codes[i:i + 12]) for i in range(0, len(codes), 12)])
    out.append("")
    return out


def render_generic(rows: list[dict]) -> list[str]:
    from ap_discover import summarize_rows

    summary = summarize_rows(rows, limit=200)
    return _table(summary, [
        ("group", lambda r: r.get("group")),
        ("title", lambda r: r.get("title")),
        ("code", lambda r: r.get("code")),
        ("org", lambda r: r.get("org")),
        ("time", lambda r: r.get("time")),
    ])


RENDERERS = [
    ("hot/topic/current/batch/list", render_topics),
    ("hot/topic/stock/list", render_ranking),
    ("hot/topic/report/latest", render_reports),
    ("roadshow/summary/hot/recommend", render_roadshows),
    ("report/hot/recommend", render_reports),
    ("stock/hot/recommend", render_ranking),
    ("analyst/information/list", render_reports),
    ("sector/prosperity/list", render_sector_prosperity),
    ("sector/market/performance", render_sector_performance),
    ("sector/chain/top/list", render_chain),
    ("stock/follow/query", render_codes),
    ("stock/information/list", render_reports),
]


def render_dataset(key: str, rows: list[dict]) -> list[str]:
    base = key.partition("@")[0]
    for needle, renderer in RENDERERS:
        if needle in base:
            try:
                return renderer(rows)
            except Exception:
                # A renderer must never cost you the capture; fall back.
                break
    return render_generic(rows)
