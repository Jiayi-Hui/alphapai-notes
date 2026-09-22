"""Route resolution with self-healing.

AlphaPai is a SPA whose paths have already moved once: several plausible
guesses (`/reading/discover/index`, `/reading/stock/index`) now return
`/error/404`, while the live views sit at `/reading/home/my-focus` and
`/reading/home/stock`.

So a hard-coded path is treated as a hint, never as truth. A section resolves
in this order:

    1. the path this machine discovered last time (cached in user config)
    2. the known-good default
    3. exploration: click the sidebar entry by its label
    4. deep exploration: try every short sidebar label and keep whichever one
       makes the section's own API call appear

A candidate only counts as resolved when the page both stays off /error/404
and /login *and* issues the API request that section is defined by - a view
that renders but loads nothing is not the right view. Whatever wins is written
back to the config, so a site redesign costs one slower run instead of a code
change.
"""

from __future__ import annotations

from dataclasses import dataclass

from ap_session import ApiRecorder, goto

BAD_PATH_MARKERS = ("/error/404", "/error/403", "/login")


@dataclass(frozen=True)
class Section:
    key: str
    labels: tuple[str, ...]      # sidebar / tab text that opens this view
    default: str                 # last known-good path
    needle: str                  # API path fragment proving the view loaded
    description: str = ""


SECTIONS: dict[str, Section] = {
    "convert": Section(
        key="convert",
        labels=("转记",),
        default="/reading/paipai/index?menu=convert",
        needle="record/convert/task/history/list",
        description="PaiPai 转记 records (your own uploads, links and recordings)",
    ),
    "discover": Section(
        key="discover",
        labels=("发现",),
        default="/reading/home/my-focus",
        needle="information/flow",
        description="发现 feed: followed stocks/sectors, 纪要/点评/研报, 机构榜单",
    ),
    "stock": Section(
        key="stock",
        labels=("个股",),
        default="/reading/home/stock",
        needle="information/flow/stock/follow",
        description="个股 view incl. 全部自选 and 优秀分析师 tabs",
    ),
    "report": Section(
        key="report",
        labels=("研报",),
        default="/reading/home/report",
        needle="reading/report/list",
        description="研报库 listing",
    ),
    "sector": Section(
        key="sector",
        labels=("板块",),
        default="/reading/home/segment",
        needle="reading/sector",
        description="板块 prosperity and performance tables",
    ),
}


class RouteError(RuntimeError):
    pass


def _path_looks_bad(url: str) -> bool:
    return any(marker in url for marker in BAD_PATH_MARKERS)


def _relative(url: str) -> str:
    for prefix in ("https://alphapai-web.rabyte.cn", "http://alphapai-web.rabyte.cn"):
        if url.startswith(prefix):
            return url[len(prefix):] or "/"
    return url


def _attempt(page, section: Section, action, wait_rounds: int = 8) -> bool:
    """Run `action` with the recorder already listening, then verify.

    Order matters: the view's API calls fire during navigation, so a recorder
    attached afterwards would miss them and report every route as broken.
    """
    with ApiRecorder(page, keep_paths=(section.needle,)) as recorder:
        try:
            action()
        except Exception:
            return False
        if _path_looks_bad(page.url):
            return False
        for _ in range(wait_rounds):
            if any(r["status"] == 200 for r in recorder.matching(section.needle)):
                return True
            page.wait_for_timeout(1000)
        return False


def _try_path(page, section: Section, path: str) -> bool:
    return _attempt(page, section, lambda: goto(page, path))


def _sidebar_labels(page, limit: int = 40) -> list[str]:
    """Short, visible, left-edge labels - the navigation rail."""
    try:
        return page.evaluate(
            """(limit) => {
                const out = [];
                const seen = new Set();
                document.querySelectorAll('div,span,a,li').forEach(e => {
                    const r = e.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) return;
                    if (r.x > 220) return;
                    const t = (e.innerText || '').trim();
                    if (!t || t.length > 8) return;
                    if (t.indexOf(String.fromCharCode(10)) >= 0) return;
                    if (seen.has(t)) return;
                    seen.add(t);
                    out.push(t);
                });
                return out.slice(0, limit);
            }""", limit)
    except Exception:
        return []


def _click_label(page, label: str) -> None:
    target = page.get_by_text(label, exact=True).first
    target.click(timeout=6000)
    page.wait_for_timeout(5000)


def _try_label(page, section: Section, label: str, wait_rounds: int = 8) -> bool:
    return _attempt(page, section, lambda: _click_label(page, label),
                    wait_rounds=wait_rounds)


def resolve(page, section_key: str, cfg, deep: bool = True,
            log=lambda msg: None) -> str:
    """Return a working path for `section_key`, healing and caching as needed."""
    section = SECTIONS.get(section_key)
    if section is None:
        raise RouteError(f"unknown section '{section_key}'. "
                         f"Known: {sorted(SECTIONS)}")

    attempts: list[str] = []
    cached = cfg.cached_route(section_key)
    for path in [p for p in (cached, section.default) if p]:
        if path in attempts:
            continue
        attempts.append(path)
        log(f"route[{section_key}]: trying {path}")
        if _try_path(page, section, path):
            cfg.remember_route(section_key, _relative(page.url))
            return _relative(page.url)

    # the known paths are gone - go look for the view in the UI
    log(f"route[{section_key}]: known paths failed, exploring the sidebar")
    for label in section.labels:
        if _try_label(page, section, label):
            found = _relative(page.url)
            cfg.remember_route(section_key, found)
            log(f"route[{section_key}]: found via label '{label}' -> {found}")
            return found

    if deep:
        labels = [x for x in _sidebar_labels(page) if x not in section.labels]
        log(f"route[{section_key}]: deep scan over {len(labels)} sidebar labels")
        for label in labels:
            if _try_label(page, section, label, wait_rounds=4):
                found = _relative(page.url)
                cfg.remember_route(section_key, found)
                log(f"route[{section_key}]: found via label '{label}' -> {found}")
                return found

    cfg.forget_route(section_key)
    raise RouteError(
        f"could not locate the '{section_key}' view ({section.description}). "
        f"Tried paths {attempts} and sidebar labels {list(section.labels)}. "
        f"Sidebar currently shows: {_sidebar_labels(page)}. "
        "The site layout likely changed - run `probe --deep` and, if a new "
        "path is found, it will be cached automatically."
    )


def resolve_all(page, cfg, log=lambda msg: None) -> dict[str, str | None]:
    """Resolve every known section; used by `probe` to report health."""
    out: dict[str, str | None] = {}
    for key in SECTIONS:
        try:
            out[key] = resolve(page, key, cfg, log=log)
        except RouteError as exc:
            log(f"route[{key}]: FAILED - {exc}")
            out[key] = None
    return out
