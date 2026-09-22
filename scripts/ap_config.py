"""User configuration for alphapai-notes.

Machine-specific settings (which Obsidian vault, where notes land, discovered
routes) live in the user's own config directory - never in the repository, so
the skill stays portable and nothing local leaks into git.

  Windows : %APPDATA%\\alphapai-notes\\config.json
  macOS   : ~/Library/Application Support/alphapai-notes/config.json
  Linux   : ~/.config/alphapai-notes/config.json

Nothing secret belongs in this file. The AlphaPai password stays in the OS
keystore (see `ap_auth`), and an OpenPai API key is read from the environment
at call time.
"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "alphapai-notes"

DEFAULTS = {
    "vault_path": None,        # absolute path to the Obsidian vault root
    "notes_subdir": "AlphaPai",  # folder inside the vault
    "formats": ["md"],         # md | docx | pdf
    "skip_examples": True,     # ignore AlphaPai's seeded 样例 rows
    "kinds": ["ai_summary"],   # ai_summary | transcript | audio | all
    "routes": {},              # section -> path, refreshed by route discovery
    "boards": ["hot_topics", "recommend", "analyst", "watchlist"],
}


def config_dir() -> Path:
    override = os.environ.get("ALPHAPAI_NOTES_CONFIG_DIR")
    if override:
        return Path(override)
    system = platform.system()
    if system == "Windows":
        root = os.environ.get("APPDATA") or str(Path.home())
        return Path(root) / APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


@dataclass
class Config:
    data: dict = field(default_factory=lambda: dict(DEFAULTS))

    # ---------- persistence ----------

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        merged = dict(DEFAULTS)
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    merged.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (json.JSONDecodeError, OSError):
                # A corrupt config must not block a scrape; defaults still work.
                pass
        return cls(data=merged)

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path

    # ---------- vault ----------

    @property
    def vault_path(self) -> Path | None:
        v = self.data.get("vault_path")
        return Path(v) if v else None

    def set_vault(self, path: str | Path) -> Path:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"vault path does not exist: {p}")
        if not p.is_dir():
            raise ValueError(f"vault path is not a directory: {p}")
        self.data["vault_path"] = str(p)
        return p

    def vault_looks_real(self) -> bool:
        """An Obsidian vault is any folder, but `.obsidian/` confirms it."""
        v = self.vault_path
        return bool(v and (v / ".obsidian").exists())

    def output_dir(self, override: str | Path | None = None) -> Path:
        """Where note files are written.

        Precedence: explicit --out, then vault/notes_subdir, then ./alphapai_notes.
        """
        if override:
            return Path(override).expanduser().resolve()
        env = os.environ.get("ALPHAPAI_NOTES_OUT")
        if env:
            return Path(env).expanduser().resolve()
        v = self.vault_path
        if v:
            return v / str(self.data.get("notes_subdir") or "AlphaPai")
        return Path.cwd() / "alphapai_notes"

    # ---------- routes ----------

    def cached_route(self, section: str) -> str | None:
        routes = self.data.get("routes") or {}
        value = routes.get(section)
        return value if isinstance(value, str) and value else None

    def remember_route(self, section: str, path: str) -> None:
        routes = self.data.setdefault("routes", {})
        if routes.get(section) != path:
            routes[section] = path

    def forget_route(self, section: str) -> None:
        (self.data.get("routes") or {}).pop(section, None)

    # ---------- reporting ----------

    def describe(self) -> dict:
        v = self.vault_path
        return {
            "config_file": str(config_path()),
            "exists": config_path().exists(),
            "vault_path": str(v) if v else None,
            "vault_present": bool(v and v.exists()),
            "vault_has_obsidian_dir": self.vault_looks_real(),
            "notes_subdir": self.data.get("notes_subdir"),
            "resolved_output_dir": str(self.output_dir()),
            "formats": self.data.get("formats"),
            "kinds": self.data.get("kinds"),
            "skip_examples": self.data.get("skip_examples"),
            "boards": self.data.get("boards"),
            "cached_routes": self.data.get("routes"),
        }


def guess_vaults(limit: int = 12) -> list[str]:
    """Look for Obsidian vaults so setup can suggest instead of interrogate.

    Reads Obsidian's own vault registry when present, then falls back to a
    shallow scan of the usual document folders for `.obsidian/` markers.
    """
    found: list[str] = []
    system = platform.system()

    if system == "Windows":
        reg = Path(os.environ.get("APPDATA", "")) / "obsidian" / "obsidian.json"
    elif system == "Darwin":
        reg = Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    else:
        reg = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "obsidian" / "obsidian.json"

    if reg.exists():
        try:
            blob = json.loads(reg.read_text(encoding="utf-8"))
            for entry in (blob.get("vaults") or {}).values():
                p = entry.get("path")
                if p and Path(p).exists() and p not in found:
                    found.append(p)
        except (json.JSONDecodeError, OSError):
            pass

    if len(found) < limit:
        roots = [Path.home(), Path.home() / "Documents", Path.home() / "Downloads",
                 Path.home() / "OneDrive"]
        for root in roots:
            if not root.exists():
                continue
            try:
                for marker in root.glob("*/.obsidian"):
                    p = str(marker.parent)
                    if p not in found:
                        found.append(p)
                    if len(found) >= limit:
                        break
            except OSError:
                continue
    return found[:limit]
