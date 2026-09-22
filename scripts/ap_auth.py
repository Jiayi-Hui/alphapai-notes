#!/usr/bin/env python3
"""AlphaPai authentication - self-contained, credential-safe.

This module owns the whole login path for alphapai-notes so the skill runs on
its own, with no dependency on any other auth helper:

  * The secret lives in the OS keystore - Windows Credential Manager under the
    target `AlphaPai:Login`, or the macOS login keychain under the same service
    name. It is read into a local variable at the moment of form fill and
    nowhere else.
  * The browser runs in a profile dedicated to AlphaPai, so this automation
    never touches the user's own browser session.
  * Nothing here prints, logs, returns or stores a username or password. The
    CLI emits status words only. An agent driving this module sees redacted
    JSON and never the credential.
  * The user enters the credential themselves, in their own interactive
    console window (`Open-AlphaPaiCredentialPrompt.ps1` on Windows, `security
    add-generic-password` on macOS). No code path accepts a password as an
    argument, environment variable or file.

It deliberately does NOT tick the login page's agreement checkbox. Accepting
terms on someone's behalf is theirs to do; if AlphaPai ever starts enforcing
that checkbox, login fails with an explicit message instead of silently
consenting.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

TARGET_NAME = "AlphaPai:Login"
LABEL = "AlphaPai"
HOME_URL = "https://alphapai-web.rabyte.cn/reading/home/report"
LOGIN_URL = "https://alphapai-web.rabyte.cn/login"
ALLOWED_HOSTS = ("alphapai-web.rabyte.cn",)
PROFILE_ROOT_NAME = "AlphaPaiNotes"
PROFILE_FOLDER = "alphapai"

LOGOUT_WORDS = re.compile(r"退出登录|退出账号|登出|log\s*out|sign\s*out", re.I)
PASSWORD_TAB = re.compile(r"账号密码登录|密码登录|password", re.I)
SUBMIT_WORDS = re.compile(r"^(登录|登入|log\s*in|sign\s*in)$", re.I)
AGREEMENT_TEXT = re.compile(r"已阅读并同意", re.I)


class AuthError(RuntimeError):
    """Login could not be completed. Never carries credential material."""


# ---------------------------------------------------------------------------
# credential store
# ---------------------------------------------------------------------------

def _windows_store():
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    pcred = ctypes.POINTER(CREDENTIALW)
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                              wintypes.DWORD, ctypes.POINTER(pcred)]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return ctypes, api, pcred


NOT_FOUND = 1168  # ERROR_NOT_FOUND


def _windows_read(require: bool) -> tuple[str, str] | None:
    import ctypes

    ctypes_mod, api, pcred = _windows_store()
    pointer = pcred()
    if not api.CredReadW(TARGET_NAME, 1, 0, ctypes_mod.byref(pointer)):
        error = ctypes_mod.get_last_error()
        if error == NOT_FOUND:
            if require:
                raise AuthError(
                    f"no {LABEL} credential is stored under '{TARGET_NAME}'. "
                    "Run scripts/Open-AlphaPaiCredentialPrompt.ps1 and enter it "
                    "yourself - it is never passed through the agent."
                )
            return None
        raise ctypes_mod.WinError(error)
    try:
        cred = pointer.contents
        username = cred.UserName or ""
        size = int(cred.CredentialBlobSize)
        secret = ""
        if cred.CredentialBlob and size:
            secret = ctypes_mod.string_at(cred.CredentialBlob, size).decode("utf-16-le")
        if not username or not secret:
            if require:
                raise AuthError(
                    f"the stored {LABEL} credential is incomplete; re-enter it "
                    "with scripts/Open-AlphaPaiCredentialPrompt.ps1")
            return None
        return username, secret
    finally:
        api.CredFree(pointer)


def _macos_read(require: bool) -> tuple[str, str] | None:
    """Read from the login keychain via `security`.

    The password is returned on stdout of a subprocess the user's own keychain
    ACL governs; it is not written to disk and not echoed.
    """
    try:
        meta = subprocess.run(
            ["security", "find-generic-password", "-s", TARGET_NAME],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AuthError(f"could not query the macOS keychain: {exc}") from None
    if meta.returncode != 0:
        if require:
            raise AuthError(
                f"no {LABEL} credential in the login keychain under service "
                f"'{TARGET_NAME}'. Add it yourself with:\n"
                f"  security add-generic-password -s '{TARGET_NAME}' "
                f"-a '<your account>' -w"
            )
        return None
    account = ""
    for line in meta.stdout.splitlines():
        m = re.search(r'"acct"<blob>="(.*)"', line)
        if m:
            account = m.group(1)
            break
    secret_proc = subprocess.run(
        ["security", "find-generic-password", "-s", TARGET_NAME, "-w"],
        capture_output=True, text=True, timeout=20)
    secret = (secret_proc.stdout or "").rstrip("\n")
    if secret_proc.returncode != 0 or not account or not secret:
        if require:
            raise AuthError(
                f"the {LABEL} keychain entry is incomplete or access was denied")
        return None
    return account, secret


ENV_USER_KEYS = ("ALPHAPAI_USERNAME", "ALPHAPAI_ACCOUNT")
ENV_SECRET_KEY = "ALPHAPAI_PASSWORD"


def _dotenv_path() -> Path | None:
    """The .env file to consider, if any.

    Only an explicitly pointed-at file (`ALPHAPAI_ENV_FILE`) or a `.env` beside
    the skill is considered. Nothing is searched for up the tree, so a stray
    .env in some parent directory can never silently supply a password.
    """
    explicit = os.environ.get("ALPHAPAI_ENV_FILE")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    local = Path(__file__).resolve().parent.parent / ".env"
    return local if local.is_file() else None


def _is_git_tracked(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(path.parent), "ls-files", "--error-unmatch", path.name],
            capture_output=True, text=True, timeout=15)
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _load_dotenv() -> str | None:
    """Load KEY=VALUE pairs from a .env, without overriding real env vars.

    Refuses a file that git tracks: a committed password is a different and
    much worse problem than an inconvenient setup step.
    """
    path = _dotenv_path()
    if path is None:
        return None
    if _is_git_tracked(path):
        raise AuthError(
            f"{path} is tracked by git. A credential file must never be "
            "committed - run `git rm --cached .env`, confirm .gitignore covers "
            "it, and rotate that password."
        )
    loaded = None
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key in (*ENV_USER_KEYS, ENV_SECRET_KEY) and key not in os.environ:
                os.environ[key] = value
                loaded = str(path)
    except OSError as exc:
        raise AuthError(f"could not read {path}: {exc}") from None
    return loaded


def _env_read(require: bool) -> tuple[str, str] | None:
    username = next((os.environ[k] for k in ENV_USER_KEYS if os.environ.get(k)), "")
    secret = os.environ.get(ENV_SECRET_KEY, "")
    if username and secret:
        return username, secret
    if require and (username or secret):
        raise AuthError(
            f"incomplete environment credential: set both one of "
            f"{'/'.join(ENV_USER_KEYS)} and {ENV_SECRET_KEY}")
    return None


def _read_credential(require: bool = True) -> tuple[str, str] | None:
    """Resolve the credential, OS keystore first.

    Order is deliberate. The keystore is ACL-protected by the OS and cannot be
    committed, so it stays the default. Environment variables (optionally
    seeded from a .env) exist for CI, containers and Linux hosts without a
    keystore - convenient, but plaintext at rest and readable by anything
    running as the same user.
    """
    system = platform.system()
    if system == "Windows":
        found = _windows_read(require=False)
    elif system == "Darwin":
        found = _macos_read(require=False)
    else:
        found = None
    if found:
        return found

    _load_dotenv()
    found = _env_read(require=False)
    if found:
        return found

    if not require:
        return None
    if system in ("Windows", "Darwin"):
        raise AuthError(
            f"no {LABEL} credential found. Either store it in the "
            f"{'Windows Credential Manager' if system == 'Windows' else 'macOS keychain'} "
            f"under '{TARGET_NAME}' (recommended - run "
            "scripts/Open-AlphaPaiCredentialPrompt.ps1 on Windows, or "
            f"`security add-generic-password -s '{TARGET_NAME}' -a '<account>' -w` "
            f"on macOS), or set {ENV_USER_KEYS[0]} and {ENV_SECRET_KEY}."
        )
    raise AuthError(
        f"no {LABEL} credential found. {system} has no supported keystore here, "
        f"so set {ENV_USER_KEYS[0]} and {ENV_SECRET_KEY} in the environment "
        "(or a .env that git does not track)."
    )


def credential_source() -> str:
    """Where the credential would come from - never what it is."""
    system = platform.system()
    try:
        if system == "Windows" and _windows_read(require=False):
            return "os_keystore"
        if system == "Darwin" and _macos_read(require=False):
            return "os_keystore"
    except AuthError:
        pass
    try:
        _load_dotenv()
    except AuthError:
        return "dotenv_rejected"
    return "environment" if _env_read(require=False) else "none"


def credential_configured() -> bool:
    """Whether a usable credential exists - without revealing any part of it."""
    try:
        return _read_credential(require=False) is not None
    except AuthError:
        return False


def credential_target() -> dict:
    system = platform.system()
    source = credential_source()
    info = {
        "store": ("Windows Credential Manager" if system == "Windows"
                  else "macOS login keychain" if system == "Darwin"
                  else f"no keystore on {system}"),
        "target": TARGET_NAME,
        "configured": source in ("os_keystore", "environment"),
        "credential_source": source,
    }
    if source == "environment":
        info["warning"] = (
            "using an environment/.env credential: plaintext at rest and "
            "readable by any process running as you. Prefer the OS keystore "
            "on a workstation; keep this for CI or containers."
        )
    elif source == "dotenv_rejected":
        info["warning"] = "a .env credential file is tracked by git and was refused"
    return info


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

def profile_dir() -> Path:
    """The browser profile dedicated to AlphaPai automation.

    Isolated from the user's own browser so this never rides on, or disturbs,
    their personal session.
    """
    override = os.environ.get("ALPHAPAI_PROFILE_DIR")
    if override:
        return Path(override)
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    elif system == "Darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path.home() / ".local" / "share"
    return root / PROFILE_ROOT_NAME / "BrowserProfiles" / PROFILE_FOLDER


# ---------------------------------------------------------------------------
# page helpers
# ---------------------------------------------------------------------------

def safe_url(url: str) -> str:
    """URL without query or fragment - those can carry tokens."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def assert_allowed_host(page) -> None:
    host = (urlsplit(page.url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise AuthError(f"navigated outside the approved host: {host or page.url}")


def _first_visible(locators):
    for locator in locators:
        try:
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def password_input(page):
    return _first_visible([
        page.locator('input[type="password"]'),
        page.locator('input[autocomplete="current-password"]'),
    ])


def account_input(page):
    return _first_visible([
        page.locator('input[autocomplete="username"]'),
        page.locator('input[type="email"]'),
        page.locator('input[name*="account" i]'),
        page.locator('input[name*="user" i]'),
        page.locator('input[placeholder*="账号"]'),
        page.locator('input[placeholder*="手机号"]'),
        page.locator('input[placeholder*="邮箱"]'),
        page.locator('input[type="text"]'),
    ])


def submit_control(page):
    # AlphaPai renders the submit control as a styled div/span rather than a
    # <button>, so an exact-text match is needed as well.
    return _first_visible([
        page.locator('form button[type="submit"]'),
        page.locator('button[type="submit"]'),
        page.get_by_role("button", name=SUBMIT_WORDS),
        page.get_by_text(SUBMIT_WORDS, exact=True),
    ])


def activate_password_login(page) -> bool:
    """Switch to the 账号密码登录 tab.

    AlphaPai's login page opens on 短信验证码登录 (SMS code), which this skill
    cannot and should not automate - intercepting an OTP is out of scope.
    Password login sits behind a second tab.
    """
    if password_input(page):
        return True
    tab = _first_visible([
        page.get_by_role("tab", name=PASSWORD_TAB),
        page.locator(".tab-item", has_text=PASSWORD_TAB),
        page.get_by_text(PASSWORD_TAB),
    ])
    if tab:
        tab.click()
        page.wait_for_timeout(700)
    return bool(password_input(page))


def detect_login_methods(page) -> list[str]:
    methods: list[str] = []
    body = ""
    try:
        body = page.locator("body").inner_text(timeout=2000)
    except Exception:
        pass
    if password_input(page) or re.search(PASSWORD_TAB, body or ""):
        methods.append("password")
    if re.search(r"验证码|短信|verification\s*code", body or "", re.I):
        methods.append("sms_or_otp")
    if re.search(r"扫码|二维码|QR\s*code", body or "", re.I):
        methods.append("qr_code")
    if re.search(r"单点登录|\bSSO\b|企业登录|微信登录", body or "", re.I):
        methods.append("sso_or_federated")
    return sorted(set(methods)) or ["unrecognized"]


def looks_authenticated(page) -> bool:
    try:
        path = (urlsplit(page.url).path or "").lower()
        if re.search(r"(^|/)(login|signin|sign-in)(/|$)", path):
            return False
        return not password_input(page)
    except Exception:
        return False


def _agreement_blocks_login(page) -> bool:
    """Is there an unchecked agreement box gating the submit?

    This skill never ticks it. If AlphaPai starts enforcing it, the caller gets
    a clear error and the user consents in a headed window themselves.
    """
    try:
        text = _first_visible([page.get_by_text(AGREEMENT_TEXT)])
        if not text:
            return False
        box = _first_visible([
            text.locator("xpath=ancestor-or-self::label[1]").locator('input[type="checkbox"]'),
            text.locator("xpath=..").locator('input[type="checkbox"]'),
            text.locator("xpath=../*[@role='checkbox']"),
        ])
        if not box:
            return False
        tag = (box.evaluate("e => e.tagName") or "").lower()
        if tag == "input":
            return not box.is_checked()
        return box.get_attribute("aria-checked") != "true"
    except Exception:
        return False


def navigate_home(page, timeout_ms: int) -> None:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(1800)
    assert_allowed_host(page)


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------

def ensure_login(page, timeout_ms: int = 45_000) -> str:
    """Make the page authenticated. Returns a status word, never a credential.

    AlphaPai sessions expire within minutes, so callers should run this inside
    the same process that then does the work, rather than assuming a profile
    is still signed in.
    """
    navigate_home(page, timeout_ms)
    if looks_authenticated(page):
        return "already_authenticated"

    if not activate_password_login(page):
        raise AuthError(
            "the password login form was not found; detected methods: "
            f"{','.join(detect_login_methods(page))}. SMS/OTP and QR login are "
            "out of scope - sign in once by hand in a headed window instead."
        )

    account = account_input(page)
    secret_field = password_input(page)
    submit = submit_control(page)
    if not account or not secret_field or not submit:
        missing = [n for n, v in (("account", account), ("password", secret_field),
                                  ("submit", submit)) if not v]
        raise AuthError(f"login form incomplete; could not find: {', '.join(missing)}")

    if _agreement_blocks_login(page):
        raise AuthError(
            "the login page shows an unticked agreement checkbox. This skill "
            "does not accept terms on your behalf - tick it once yourself in a "
            "headed window (`probe --headed`), then retry."
        )

    username, secret = _read_credential(require=True)
    try:
        account.fill(username)
        secret_field.fill(secret)
        submit.click()
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            page.wait_for_timeout(500)
            assert_allowed_host(page)
            if looks_authenticated(page):
                return "authenticated"
        raise AuthError(
            "submitted the login form but never reached an authenticated page. "
            "The password may be wrong or the account may require a code; this "
            "helper does not retry, to avoid a lockout."
        )
    finally:
        # drop references promptly; do not let them reach a traceback
        username = secret = ""
        del username, secret


def logout(page, timeout_ms: int = 45_000) -> str:
    """Sign out. Only call when the user explicitly asked for it."""
    navigate_home(page, timeout_ms)
    if not looks_authenticated(page):
        return "already_logged_out"
    direct = _first_visible([
        page.get_by_role("button", name=LOGOUT_WORDS),
        page.get_by_role("link", name=LOGOUT_WORDS),
        page.get_by_text(LOGOUT_WORDS, exact=True),
    ])
    if not direct:
        menu = _first_visible([
            page.get_by_role("button", name=re.compile(r"account|profile|user|avatar|我的|个人|账户", re.I)),
            page.locator('[aria-label*="account" i]'),
            page.locator('[aria-label*="user" i]'),
        ])
        if not menu:
            raise AuthError("the account menu was not recognized, so logout was not attempted")
        menu.click()
        page.wait_for_timeout(600)
        direct = _first_visible([
            page.get_by_role("button", name=LOGOUT_WORDS),
            page.get_by_role("menuitem", name=LOGOUT_WORDS),
            page.get_by_text(LOGOUT_WORDS, exact=True),
        ])
        if not direct:
            raise AuthError("the logout command was not recognized")
    direct.click()
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        assert_allowed_host(page)
        if not looks_authenticated(page):
            return "logged_out"
    raise AuthError("logout could not be verified")


def probe(page, timeout_ms: int = 45_000) -> dict:
    """Report session and login-method state. Submits nothing."""
    navigate_home(page, timeout_ms)
    authenticated = looks_authenticated(page)
    methods: list[str] = []
    if not authenticated:
        before = detect_login_methods(page)
        activate_password_login(page)
        methods = sorted(set(before) | set(detect_login_methods(page)))
        if "unrecognized" in methods and len(methods) > 1:
            methods.remove("unrecognized")
    return {
        "authenticated": authenticated,
        "page": safe_url(page.url),
        "login_methods": methods,
        **credential_target(),
    }


# ---------------------------------------------------------------------------
# standalone CLI - useful for setup and diagnosis
# ---------------------------------------------------------------------------

def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1))


def _browser(headless: bool, timeout_ms: int):
    from playwright.sync_api import sync_playwright

    pdir = profile_dir()
    pdir.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(pdir), channel="msedge", headless=headless, accept_downloads=True,
        viewport={"width": 1600, "height": 1000},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(timeout_ms)
    return pw, ctx, page


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AlphaPai authentication helper (credential-safe)")
    parser.add_argument("command", choices=["status", "probe", "login", "logout"])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    timeout_ms = max(5, min(args.timeout_seconds, 120)) * 1000

    if args.command == "status":
        info = credential_target()
        _emit({"status": "configured" if info["configured"] else "not_configured",
               **info, "profile_dir": str(profile_dir())})
        return 0 if info["configured"] else 2

    pw = ctx = None
    try:
        pw, ctx, page = _browser(not args.headed, timeout_ms)
        if args.command == "probe":
            _emit({"status": "probe_succeeded", **probe(page, timeout_ms)})
        elif args.command == "login":
            _emit({"status": ensure_login(page, timeout_ms), "authenticated": True})
        else:
            _emit({"status": logout(page, timeout_ms), "authenticated": False})
        return 0
    except AuthError as exc:
        _emit({"status": "failed", "error": str(exc)})
        return 1
    except Exception as exc:
        _emit({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
