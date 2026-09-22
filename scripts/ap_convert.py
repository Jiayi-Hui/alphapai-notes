"""PaiPai 转记 (recording-to-notes) listing and artifact retrieval.

How the download actually works, as verified against the live site:

  1. The row's `···` action menu holds `同步至PaiWork` and `下载 >`.
  2. Hovering `下载` opens a submenu: 全部 / 转记 / AI纪要 / 录音文件.
  3. Choosing a leaf makes the app call
         GET /external/alpha/api/reading/recordconvert/download
             ?id=<opaque id>&source=<ai|mt|...>&summaryType=correct
     which returns {code: 200000, data: {type, url}} where `url` is an
     object-store key, not a link.
  4. The app then fetches
         https://alphapai-storage.rabyte.cn/summary/<urlencoded key>
     with its own credentials. Requesting that URL ourselves returns 401.

So the artifact is obtained by letting the app perform step 4 and reading the
response body. No token, cookie or header is ever touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ap_session import (STORAGE_HOST, ApiRecorder, assert_on_allowlist, goto,
                        wait_for_rows)

CONVERT_PATH = "/reading/paipai/index?menu=convert"
LIST_NEEDLE = "record/convert/task/history/list"
DOWNLOAD_NEEDLE = "reading/recordconvert/download"

# CLI name -> submenu label on the site
KINDS = {
    "ai_summary": "AI纪要",
    "transcript": "转记",
    "audio": "录音文件",
    "all": "全部",
}
# Kinds whose artifact is an OOXML document (ZIP magic) rather than media.
DOC_KINDS = {"ai_summary", "transcript", "all"}

STATUS_LABELS = {0: "processing", 1: "done", 2: "failed"}


@dataclass
class Note:
    """One 转记 record, normalised to the fields we actually rely on."""

    raw: dict = field(repr=False)

    @property
    def id(self) -> str:
        return str(self.raw.get("id") or "")

    @property
    def title(self) -> str:
        return (self.raw.get("title") or self.raw.get("uploadFileName") or "untitled").strip()

    @property
    def created(self) -> str:
        return str(self.raw.get("createTime") or "")

    @property
    def duration(self) -> str:
        return str(self.raw.get("durationDetail") or "")

    @property
    def status(self) -> str:
        return STATUS_LABELS.get(self.raw.get("status"), str(self.raw.get("status")))

    @property
    def is_example(self) -> bool:
        """AlphaPai seeds new accounts with demo rows; flag them so a caller
        can skip sample data instead of mistaking it for the user's own."""
        return bool(self.raw.get("example")) or self.title.startswith("样例")

    @property
    def source_kind(self) -> str:
        url = self.raw.get("uploadFileUrl") or ""
        if isinstance(url, str) and url.startswith("http"):
            return "link"
        return "upload"

    def available(self) -> list[str]:
        out = []
        if self.raw.get("summaryDocxUrl"):
            out.append("ai_summary")
        if self.raw.get("summaryRadioDocxUrl"):
            out.append("transcript")
        if self.raw.get("originMediaUrl"):
            out.append("audio")
        return out

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "duration": self.duration,
            "status": self.status,
            "example": self.is_example,
            "source": self.source_kind,
            "available": self.available(),
        }


def list_notes(page, scroll_rounds: int = 6) -> list[Note]:
    """Open the 转记 view and collect every row the app loads.

    The list lazy-loads, so we scroll until no new rows appear or the site
    reports 没有更多了.
    """
    recorder = ApiRecorder(page, keep_paths=(LIST_NEEDLE,))
    goto(page, CONVERT_PATH)
    rows = wait_for_rows(recorder, LIST_NEEDLE, page)

    seen = len(rows)
    for _ in range(scroll_rounds):
        if _reached_end(page):
            break
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(2200)
        rows = recorder.rows_for(LIST_NEEDLE)
        if len(rows) == seen:
            break
        seen = len(rows)

    return [Note(raw=r) for r in rows]


def _reached_end(page) -> bool:
    try:
        return page.locator("text=没有更多了").first.is_visible(timeout=800)
    except Exception:
        return False


def _trigger_box_for_title(page, title: str) -> dict | None:
    """Locate the action trigger belonging to the row that shows `title`.

    Indexing `.th-more__trigger` by position is wrong: the DOM order of those
    triggers does not follow the visible row order, so nth(0) can belong to a
    different record than rows[0]. Anchoring on the row's own text is the only
    reliable mapping - verified after an index-based click downloaded the
    wrong record's summary.
    """
    return page.evaluate(
        """(title) => {
            const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
            const needle = norm(title).slice(0, 20);
            if (!needle) return null;

            // Locate the leaf element that actually renders this title.
            // Walking UP from a trigger to an ancestor "containing" the title
            // is wrong: a few levels up the ancestor is the whole table and
            // contains every row's text, so every title matches row one.
            let titleEl = null;
            for (const el of document.querySelectorAll('div,span,a,p,td')) {
                if (el.children.length !== 0) continue;
                const r = el.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                if (norm(el.textContent).includes(needle)) { titleEl = el; break; }
            }
            if (!titleEl) return null;

            // Same row == same horizontal band.
            const tr = titleEl.getBoundingClientRect();
            const rowCenter = tr.y + tr.height / 2;
            let best = null, bestDist = Infinity;
            for (const trig of document.querySelectorAll('.th-more__trigger')) {
                const r = trig.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const dist = Math.abs((r.y + r.height / 2) - rowCenter);
                if (dist < bestDist) {
                    bestDist = dist;
                    best = {x: r.x, y: r.y, width: r.width, height: r.height,
                            dist: dist};
                }
            }
            // rows are ~60px apart; anything further is a different row
            return (best && best.dist <= 28) ? best : null;
        }""", title)


def _open_row_menu(page, title: str) -> dict:
    """Open the `···` menu for the row whose text contains `title`."""
    box = _trigger_box_for_title(page, title)
    if not box:
        raise RuntimeError(
            f"could not find the action trigger for row titled {title[:50]!r}")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(900)
    return box


def _hover_download(page, box: dict) -> dict[str, list[int]]:
    """Walk the pointer onto `下载` so its submenu opens, then report the
    submenu leaf positions. Element-UI closes the submenu if the pointer
    jumps, so the move is done in steps."""
    for dx, dy in ((-40, 40), (-70, 62), (-80, 68)):
        page.mouse.move(box["x"] + dx, box["y"] + dy)
        page.wait_for_timeout(350)
    page.wait_for_timeout(1200)
    return page.evaluate(
        """() => {
            const out = {};
            document.querySelectorAll('.th-more__subitem').forEach(e => {
                const r = e.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) return;
                out[(e.innerText || '').trim()] =
                    [Math.round(r.x + r.width / 2), Math.round(r.y + r.height / 2)];
            });
            return out;
        }"""
    )


def _click_leaf_at(page, x: float, y: float) -> str | None:
    """Click the submenu leaf that is actually visible at (x, y).

    Two traps make the obvious implementation download the wrong record:

      * A synthetic pointer click never lands - the submenu overlay
        intercepts pointer events, so the element is "visible, enabled and
        stable" yet receives nothing. The handler only fires for an event
        dispatched on the node itself.
      * Selecting that node by text across the document is wrong, because
        every row keeps its own menu in the DOM and their DOM order does not
        follow the visible row order. Picking the first `.th-more__subitem`
        whose text matched therefore clicked a *different* row's menu, which
        produced notes whose body belonged to the neighbouring record.

    Resolving the element from the coordinates of the menu we just opened
    avoids both. The clicked label is returned so the caller can verify it.
    """
    return page.evaluate(
        """([x, y]) => {
            const el = document.elementFromPoint(x, y);
            if (!el) return null;
            const target = el.closest('.th-more__subitem') || el;
            const label = (target.innerText || '').trim();
            target.dispatchEvent(new MouseEvent('click',
                {bubbles: true, cancelable: true, view: window}));
            return label;
        }""", [x, y])


def _close_menu(page) -> None:
    try:
        page.keyboard.press("Escape")
        page.mouse.move(20, 600)
        page.wait_for_timeout(400)
    except Exception:
        pass


def _is_artifact(response, expect_doc: bool) -> bool:
    """Tell the wanted artifact apart from ordinary page assets.

    The storage host also serves avatars and thumbnails, so a response only
    counts when its bytes look right: ZIP magic for .docx, non-image content
    for media.
    """
    if STORAGE_HOST not in response.url:
        return False
    ctype = (response.header_value("content-type") or "").lower()
    if ctype.startswith("image/"):
        return False
    try:
        body = response.body()
    except Exception:
        return False
    if not body:
        return False
    if expect_doc:
        return body[:2] == b"PK"
    return body[:2] != b"PK" or len(body) > 200_000


class ArtifactUnavailable(RuntimeError):
    """The menu worked but no file materialised."""


def fetch_artifact(page, title: str, kind: str,
                   timeout_ms: int = 60_000) -> tuple[bytes, str | None]:
    """Drive the UI for one row+kind and return (bytes, suggested_filename).

    The app hands the file over as a browser download, not as a readable
    fetch: it calls its own download endpoint for an object-store key and then
    *navigates* to the storage URL. Consequences, both established by testing:

      * A `download` event is the real delivery mechanism - so that is the
        primary path here.
      * Requesting the storage URL ourselves fails (401 without the app's
        auth) and fetching it from page context fails too (CORS), so neither
        shortcut can replace the event.
      * Headless Edge does not surface the download at all; headed does.
        Hence `ap_session.session(headless=False)` for pull operations.

    A response-based capture is kept as a fallback in case a future build
    serves the file through XHR instead.
    """
    label = KINDS[kind]
    expect_doc = kind in DOC_KINDS
    captured: list[bytes] = []

    def handler(response):
        if not captured and _is_artifact(response, expect_doc):
            try:
                captured.append(response.body())
            except Exception:
                pass

    page.on("response", handler)
    try:
        box = _open_row_menu(page, title)
        leaves = _hover_download(page, box)
        if label not in leaves:
            raise ArtifactUnavailable(
                f"'{label}' not offered for {title[:40]!r} "
                f"(menu showed: {sorted(leaves)})")

        x, y = leaves[label]
        try:
            with page.expect_download(timeout=timeout_ms) as info:
                clicked = _click_leaf_at(page, x, y)
                if clicked != label:
                    raise ArtifactUnavailable(
                        f"menu drifted while opening {title[:40]!r}: expected "
                        f"'{label}' at that position but hit {clicked!r}")
            download = info.value
            blob = _read_download(download)
            if blob:
                return blob, download.suggested_filename
        except ArtifactUnavailable:
            raise
        except Exception:
            # no download event - fall through to the response fallback
            pass

        waited = 0
        while not captured and waited < 8000:
            page.wait_for_timeout(1000)
            waited += 1000
        assert_on_allowlist(page)
        if captured:
            return captured[0], None

        raise ArtifactUnavailable(
            f"no file arrived for '{label}' on {title[:40]!r} within "
            f"{timeout_ms // 1000}s. Headless Edge does not deliver these "
            f"downloads - run without --headless."
        )
    finally:
        page.remove_listener("response", handler)
        _close_menu(page)


def _read_download(download) -> bytes | None:
    """Materialise a Playwright download into memory."""
    import tempfile

    try:
        failure = download.failure()
        if failure:
            return None
    except Exception:
        pass
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / (download.suggested_filename or "artifact.bin")
            download.save_as(str(dest))
            return dest.read_bytes()
    except Exception:
        return None


SAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(text: str, limit: int = 90) -> str:
    """Filename that survives Windows, git and an Obsidian vault."""
    cleaned = SAFE_NAME.sub("_", text or "untitled").strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    # '[[' would become a wiki-link in Obsidian; '#' and '^' are also special
    cleaned = cleaned.replace("[", "(").replace("]", ")").replace("#", "＃").replace("^", "")
    return (cleaned[:limit].strip() or "untitled")


def artifact_extension(kind: str, blob: bytes) -> str:
    if blob[:2] == b"PK":
        return ".docx"
    if kind == "audio":
        if blob[:3] == b"ID3" or blob[:2] in (b"\xff\xfb", b"\xff\xf3"):
            return ".mp3"
        if blob[4:8] == b"ftyp":
            return ".m4a"
        return ".audio"
    return ".bin"
