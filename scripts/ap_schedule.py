"""Recurring capture: Windows Task Scheduler, macOS launchd, Linux cron.

Installing a scheduled task is persistent machine configuration, so every
function here is dry-run by default and only writes when `apply=True` is
passed explicitly (the CLI requires `--apply`).

One operational constraint worth knowing before scheduling: the AlphaPai
password lives in Windows Credential Manager under the interactive user, and
the browser profile is that user's too. The task therefore has to run as the
logged-in user and only while they are logged in - a "run whether user is
logged on or not" task would need the account password stored with the task
and still could not reach the per-user credential store reliably.
"""

from __future__ import annotations

import os
import platform
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TASK_NAME_WINDOWS = "AlphaPaiNotes-Capture"
LAUNCHD_LABEL = "com.alphapai.notes.capture"
CRON_MARKER = "# alphapai-notes capture"


class ScheduleError(RuntimeError):
    pass


@dataclass
class Plan:
    platform: str
    mechanism: str
    command: list[str]
    install: list[str] | None
    notes: list[str]
    artifact_path: str | None = None
    artifact_body: str | None = None

    def describe(self) -> dict:
        return {
            "platform": self.platform,
            "mechanism": self.mechanism,
            "capture_command": " ".join(_quote(c) for c in self.command),
            "install_command": (" ".join(_quote(c) for c in self.install)
                                if self.install else None),
            "writes_file": self.artifact_path,
            "notes": self.notes,
        }


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part and not part.startswith('"') else part


def cli_path() -> Path:
    return (Path(__file__).parent / "alphapai_notes.py").resolve()


def capture_command(kinds: list[str], formats: list[str],
                    include_boards: bool, offscreen: bool = True) -> list[str]:
    """The command the scheduler will run.

    `--offscreen` is on by default: downloads need a headed browser, and a
    window appearing over the user's work every morning is not acceptable for
    an unattended job.
    """
    cmd = [sys.executable, str(cli_path()), "pull",
           "--kinds", ",".join(kinds),
           "--format", ",".join(formats),
           "--quiet"]
    if offscreen:
        cmd.append("--offscreen")
    if include_boards:
        cmd.append("--with-boards")
    return cmd


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

def _windows_plan(command: list[str], time_hhmm: str, frequency: str,
                  task_name: str) -> Plan:
    runner = " ".join(f'\\"{c}\\"' if " " in c else c for c in command)
    install = [
        "schtasks", "/Create",
        "/TN", task_name,
        "/TR", runner,
        "/SC", frequency.upper(),
        "/ST", time_hhmm,
        "/RL", "LIMITED",
        "/F",
    ]
    return Plan(
        platform="Windows",
        mechanism="Task Scheduler (schtasks)",
        command=command,
        install=install,
        notes=[
            "Runs as the current interactive user; it fires only while that "
            "user is logged on, which is required to reach Credential Manager "
            "and the dedicated browser profile.",
            f"Inspect later with: schtasks /Query /TN {task_name} /V /FO LIST",
            f"Remove with: schtasks /Delete /TN {task_name} /F",
        ],
    )


# --------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------

def _macos_plan(command: list[str], time_hhmm: str, label: str) -> Plan:
    hour, minute = (int(x) for x in time_hhmm.split(":"))
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    payload = {
        "Label": label,
        "ProgramArguments": command,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "StandardOutPath": str(Path.home() / "Library" / "Logs" / f"{label}.log"),
        "StandardErrorPath": str(Path.home() / "Library" / "Logs" / f"{label}.err"),
    }
    body = plistlib.dumps(payload).decode("utf-8")
    return Plan(
        platform="macOS",
        mechanism="launchd user agent",
        command=command,
        install=["launchctl", "load", "-w", str(plist_path)],
        notes=[
            "A user LaunchAgent runs in the user's GUI session, which is what "
            "the browser profile needs.",
            "On macOS the credential comes from the login keychain; add it "
            "with `security add-generic-password -s 'AlphaPai:Login' -a "
            "'<account>' -w` before scheduling.",
            f"Remove with: launchctl unload -w {plist_path} && rm {plist_path}",
        ],
        artifact_path=str(plist_path),
        artifact_body=body,
    )


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------

def _linux_plan(command: list[str], time_hhmm: str) -> Plan:
    hour, minute = (int(x) for x in time_hhmm.split(":"))
    line = f"{minute} {hour} * * * {' '.join(command)}  {CRON_MARKER}"
    return Plan(
        platform="Linux",
        mechanism="cron (user crontab)",
        command=command,
        install=None,
        notes=[
            "Add this line with `crontab -e`:",
            line,
            "A headless run still needs a browser profile with a valid "
            "session; on Linux there is no OS keystore here, so the credential "
            "must come from ALPHAPAI_USERNAME / ALPHAPAI_PASSWORD.",
        ],
    )


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def build_plan(kinds: list[str], formats: list[str], time_hhmm: str,
               frequency: str = "daily", include_boards: bool = False,
               task_name: str = TASK_NAME_WINDOWS,
               offscreen: bool = True) -> Plan:
    _validate_time(time_hhmm)
    if frequency.lower() not in ("daily", "weekly", "hourly"):
        raise ScheduleError("frequency must be daily, weekly or hourly")
    command = capture_command(kinds, formats, include_boards, offscreen)
    system = platform.system()
    if system == "Windows":
        return _windows_plan(command, time_hhmm, frequency, task_name)
    if system == "Darwin":
        return _macos_plan(command, time_hhmm, LAUNCHD_LABEL)
    return _linux_plan(command, time_hhmm)


def _validate_time(value: str) -> None:
    try:
        hour, minute = value.split(":")
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise ScheduleError(f"--time must be HH:MM, got {value!r}") from None


def install(plan: Plan, apply: bool = False) -> dict:
    """Write the schedule. Without apply=True this only reports the plan."""
    result = plan.describe()
    if not apply:
        result["applied"] = False
        result["reason"] = "dry run - pass --apply to install"
        return result

    if plan.artifact_path and plan.artifact_body:
        path = Path(plan.artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plan.artifact_body, encoding="utf-8")
        result["wrote"] = str(path)

    if plan.install is None:
        result["applied"] = False
        result["reason"] = ("this platform needs a manual crontab edit; the "
                            "line to add is in notes")
        return result

    proc = subprocess.run(plan.install, capture_output=True, text=True)
    result["applied"] = proc.returncode == 0
    result["exit_code"] = proc.returncode
    output = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    result["output"] = output[:400]
    return result


def status(task_name: str = TASK_NAME_WINDOWS) -> dict:
    system = platform.system()
    if system == "Windows":
        proc = subprocess.run(["schtasks", "/Query", "/TN", task_name, "/FO", "LIST"],
                              capture_output=True, text=True)
        return {"platform": "Windows", "installed": proc.returncode == 0,
                "detail": ((proc.stdout or proc.stderr) or "").strip()[:600]}
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        proc = subprocess.run(["launchctl", "list", LAUNCHD_LABEL],
                              capture_output=True, text=True)
        return {"platform": "macOS", "plist_exists": plist.exists(),
                "loaded": proc.returncode == 0,
                "detail": ((proc.stdout or proc.stderr) or "").strip()[:600]}
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [ln for ln in (proc.stdout or "").splitlines() if CRON_MARKER in ln]
    return {"platform": "Linux", "installed": bool(lines), "detail": lines}


def remove(task_name: str = TASK_NAME_WINDOWS, apply: bool = False) -> dict:
    system = platform.system()
    if system == "Windows":
        cmd = ["schtasks", "/Delete", "/TN", task_name, "/F"]
    elif system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        cmd = ["launchctl", "unload", "-w", str(plist)]
    else:
        return {"applied": False,
                "reason": f"remove the crontab line marked '{CRON_MARKER}' with crontab -e"}
    if not apply:
        return {"applied": False, "would_run": " ".join(cmd),
                "reason": "dry run - pass --apply to remove"}
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = {"applied": proc.returncode == 0, "exit_code": proc.returncode,
           "output": ((proc.stdout or proc.stderr) or "").strip()[:300]}
    if system == "Darwin" and proc.returncode == 0:
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if plist.exists():
            os.remove(plist)
            out["removed_file"] = str(plist)
    return out
