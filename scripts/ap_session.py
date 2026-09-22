"""Browser session layer for alphapai-notes.

Credential policy (hard constraint):
    This module never reads, stores, derives or logs a username, password,
    token, cookie, Authorization header, localStorage entry or signed URL.
    Login belongs to `ap_auth`, which reads the secret from the OS keystore at
    the moment it fills the form. Everything here rides on the browser's own
    authenticated context: the page issues its own requests and this module
    only reads the resulting response bodies.

AlphaPai sessions are short-lived - a profile that authenticated minutes ago
is often bounced back to /login. So every run logs in inside the same process
that scrapes, instead of assuming a leftover session.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import ap_auth

BASE_URL = "https://alphapai-web.rabyte.cn"
ALLOWED_HOSTS = ("alphapai-web.rabyte.cn",)
STORAGE_HOST = "alphapai-storage.rabyte.cn"
API_MARKER = "/external/alpha/api/"

DEFAULT_TIMEOUT_MS = 45_000
NAV_SETTLE_MS = 8_000


class SessionError(RuntimeError):
    """Raised when the browser session cannot be established."""


def profile_dir() -> Path:
    """The browser profile dedicated to AlphaPai automation."""
    return ap_auth.profile_dir()


def assert_on_allowlist(page) -> None:
    try:
        ap_auth.assert_allowed_host(page)
    except ap_auth.AuthError as exc:
        raise SessionError(str(exc)) from None


@contextmanager
def session(headless: bool = True, timeout_ms: int = DEFAULT_TIMEOUT_MS,
            viewport: tuple[int, int] = (1600, 1000), offscreen: bool = False):
    """Yield (context, page, auth_state) with the profile already logged in.

    The login attempt happens once per run: `ap_auth` does not loop on a
    rejected credential, and this wrapper does not retry it either, so a wrong
    password cannot turn into a lockout.

    `offscreen` keeps a headed browser out of the way: file downloads only
    arrive in headed mode, but a window popping up over the user's work every
    run is intrusive, so it is positioned outside the visible desktop.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SessionError("Python package 'playwright' is not installed") from exc

    pdir = profile_dir()
    pdir.mkdir(parents=True, exist_ok=True)

    args = [
        "--disable-blink-features=AutomationControlled",
        # Edge otherwise opens edge://downloads-hub after a download, which
        # leaves an extra target alive and slows profile release.
        "--disable-features=msDownloadsHub,DownloadBubble,DownloadBubbleV2",
    ]
    if offscreen and not headless:
        # Far-negative coordinates make Edge exit during startup ("target
        # closed"), so nudge the window mostly below the desktop instead.
        args.append("--window-position=0,2000")

    with sync_playwright() as pw:
        ctx = _launch_with_retry(pw, pdir, headless, viewport, args)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(timeout_ms)
            try:
                state = ap_auth.ensure_login(page, timeout_ms)
            except ap_auth.AuthError as exc:
                raise SessionError(str(exc)) from None
            assert_on_allowlist(page)
            yield ctx, page, state
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def _launch_with_retry(pw, pdir: Path, headless: bool,
                       viewport: tuple[int, int], args: list[str],
                       attempts: int = 3):
    """Launch the profile, tolerating a previous run still shutting down.

    A headed Edge that has not fully exited still owns the profile; the new
    instance then hands off and immediately dies, which surfaces later as
    "Target page, context or browser has been closed" in the middle of a
    scrape. Detecting it at startup and waiting is far clearer than failing
    mid-download.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        ctx = None
        try:
            ctx = pw.chromium.launch_persistent_context(
                str(pdir),
                channel="msedge",
                headless=headless,
                accept_downloads=True,
                viewport={"width": viewport[0], "height": viewport[1]},
                args=args,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            # health check: a hijacked launch fails here rather than later
            page.evaluate("() => 1")
            return ctx
        except Exception as exc:
            last_error = exc
            if ctx is not None:
                try:
                    ctx.close()
                except Exception:
                    pass
            if attempt < attempts:
                import time
                time.sleep(4 * attempt)
    raise SessionError(
        "could not start a usable browser on the AlphaPai profile after "
        f"{attempts} attempts ({type(last_error).__name__}: {last_error}). "
        "A previous automation run may still be shutting down - wait a few "
        "seconds and retry, or close any leftover Edge process using "
        f"{pdir}."
    )


class ApiRecorder:
    """Collects the app's own JSON API responses.

    This is the read path for every list-style dataset: the page asks for its
    data with its own credentials, and we read what came back. No request is
    forged and no header is inspected.
    """

    def __init__(self, page, keep_paths: tuple[str, ...] = ()):
        self.page = page
        self.keep_paths = keep_paths
        self.records: list[dict] = []
        self._attached = False
        self.attach()

    def attach(self) -> None:
        if not self._attached:
            self.page.on("response", self._on_response)
            self._attached = True

    def detach(self) -> None:
        """Stop listening. Route probing creates a recorder per attempt, so
        leaving listeners attached would pile them up on a long-lived page."""
        if self._attached:
            try:
                self.page.remove_listener("response", self._on_response)
            except Exception:
                pass
            self._attached = False

    def __enter__(self) -> "ApiRecorder":
        self.attach()
        return self

    def __exit__(self, *exc) -> None:
        self.detach()

    def _on_response(self, response) -> None:
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            url = response.url
            if API_MARKER not in url:
                return
            path = url.split("?")[0].split(API_MARKER)[-1]
            if self.keep_paths and not any(k in path for k in self.keep_paths):
                return
            try:
                payload = response.json()
            except Exception:
                return
            self.records.append({
                "path": path,
                "method": response.request.method,
                "status": response.status,
                "payload": payload,
            })
        except Exception:
            # An interception failure must never abort a scrape.
            pass

    def matching(self, needle: str) -> list[dict]:
        return [r for r in self.records if needle in r["path"]]

    def rows_for(self, needle: str) -> list[dict]:
        """Flatten the common AlphaPai envelopes into a list of rows."""
        rows: list[dict] = []
        for rec in self.matching(needle):
            rows.extend(extract_rows(rec["payload"]))
        return dedupe_rows(rows)

    def clear(self) -> None:
        self.records.clear()


def extract_rows(payload) -> list[dict]:
    """Pull the row list out of AlphaPai's several response envelopes.

    Observed shapes:
        {code, data: {data: [...], totalSize}}   paged list
        {code, data: {list: [...]}}              simple list
        {code, data: [...]}                      bare list
        {code, data: {groupA: [...], groupB: [...]}}   several named lists

    The last shape is not a corner case: the analyst feed returns
    informationFlow/roadshowSummary/comment/report side by side, and the
    institution rankings return publicList/privateList/insuranceList. Treating
    only the single-list shapes as valid made those boards look empty.
    Rows from a grouped envelope carry `_group` so the origin survives.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("data", "list", "records", "items", "rows", "content"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
        grouped: list[dict] = []
        for key, value in data.items():
            if not isinstance(value, list):
                continue
            for row in value:
                if isinstance(row, dict):
                    enriched = dict(row)
                    enriched.setdefault("_group", key)
                    grouped.append(enriched)
        return grouped
    return []


def envelope_ok(payload) -> bool:
    """AlphaPai signals success with code 200000 (web) or 0/200 on some paths."""
    if not isinstance(payload, dict):
        return False
    return payload.get("code") in (0, 200, 200000, "200000")


def dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for r in rows:
        key = r.get("id") or r.get("recordId") or r.get("uuid")
        if key is None:
            key = repr(sorted((k, str(v)[:40]) for k, v in r.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def goto(page, path_or_url: str, settle_ms: int = NAV_SETTLE_MS) -> None:
    url = path_or_url if path_or_url.startswith("http") else BASE_URL + path_or_url
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(settle_ms)
    assert_on_allowlist(page)


def wait_for_rows(recorder: ApiRecorder, needle: str, page,
                  attempts: int = 12, interval_ms: int = 1000) -> list[dict]:
    """Poll until the page's own request for `needle` has produced rows."""
    for _ in range(attempts):
        rows = recorder.rows_for(needle)
        if rows:
            return rows
        page.wait_for_timeout(interval_ms)
    return recorder.rows_for(needle)
