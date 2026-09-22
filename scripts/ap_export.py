"""Export captured notes into an Obsidian vault.

Markdown is the primary target: AlphaPai only ever hands out .docx, so the
document is converted here rather than left for the reader to open in Word.
The original .docx is kept alongside it when requested, because a conversion
is a lossy derivative and the archive copy should stay byte-exact.

PDF is produced by driving an installed converter (Word on Windows,
LibreOffice elsewhere). When neither exists the export says so instead of
writing a fake file.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

HEADING_PREFIX = {f"Heading {i}": "#" * min(i + 1, 6) for i in range(1, 7)}
LIST_STYLES = ("List Paragraph", "List Bullet", "List Number")


class ExportError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# docx -> markdown
# --------------------------------------------------------------------------

def docx_to_markdown(path: Path) -> str:
    """Convert a .docx to markdown, preserving headings, lists and tables.

    Walks the document body in document order so tables stay where the author
    put them - iterating paragraphs then tables separately would reorder the
    content.
    """
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ExportError(
            "python-docx is required for markdown export. Install it with "
            "`pip install python-docx`, or export with --format docx only."
        ) from exc

    doc = Document(str(path))
    body = doc.element.body
    lines: list[str] = []

    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            lines.extend(_paragraph_lines(para))
        elif tag == "tbl":
            table = Table(child, doc)
            lines.extend(_table_lines(table))

    return _tidy("\n".join(lines))


def _paragraph_lines(para) -> list[str]:
    text = (para.text or "").strip()
    if not text:
        return [""]
    style = (getattr(para.style, "name", "") or "").strip()

    prefix = HEADING_PREFIX.get(style)
    if prefix:
        return [f"{prefix} {text}", ""]
    if style.startswith("Title"):
        return [f"# {text}", ""]
    if any(style.startswith(s) for s in LIST_STYLES):
        return [f"- {text}"]
    return [text, ""]


def _table_lines(table) -> list[str]:
    rows = []
    for row in table.rows:
        cells = [(c.text or "").strip().replace("\n", " ").replace("|", "\\|")
                 for c in row.cells]
        rows.append(cells)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["", "| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for r in rows[1:]:
        out.append("| " + " | ".join(r) + " |")
    out.append("")
    return out


def _tidy(text: str) -> str:
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln and out and not out[-1]:
            continue          # collapse blank runs
        out.append(ln)
    return "\n".join(out).strip() + "\n"


# --------------------------------------------------------------------------
# Obsidian note assembly
# --------------------------------------------------------------------------

def _yaml_scalar(value) -> str:
    """Quote conservatively: titles contain colons, quotes and brackets."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return '""'
    return json.dumps(str(value), ensure_ascii=False)


def frontmatter(meta: dict, tags: list[str]) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {_yaml_scalar(v)}" for v in value)
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    if tags:
        lines.append("tags:")
        lines.extend(f"  - {t}" for t in tags)
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def build_note(meta: dict, body_md: str, tags: list[str] | None = None) -> str:
    return frontmatter(meta, tags or []) + body_md


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def docx_to_pdf(src: Path, dest: Path) -> Path:
    """Convert via Word (Windows) or LibreOffice; raise if neither is present."""
    if platform.system() == "Windows":
        try:
            return _pdf_via_word(src, dest)
        except ExportError:
            pass  # fall through to LibreOffice if it happens to be installed
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        return _pdf_via_soffice(soffice, src, dest)
    raise ExportError(
        "no PDF converter available. Install Microsoft Word (Windows) or "
        "LibreOffice, or export with --format md,docx."
    )


def _pdf_via_word(src: Path, dest: Path) -> Path:
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:
        raise ExportError("pywin32 not installed; cannot drive Word") from exc

    word = None
    try:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        doc = word.Documents.Open(str(src.resolve()), ReadOnly=True)
        doc.SaveAs(str(dest.resolve()), FileFormat=17)  # wdFormatPDF
        doc.Close(False)
        return dest
    except Exception as exc:
        raise ExportError(f"Word conversion failed: {exc}") from exc
    finally:
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass


def _pdf_via_soffice(soffice: str, src: Path, dest: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            capture_output=True, text=True, timeout=180,
        )
        produced = Path(tmp) / (src.stem + ".pdf")
        if not produced.exists():
            raise ExportError(
                f"LibreOffice produced no PDF (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '').strip()[:200]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, dest)
    return dest


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

MAX_PATH = 250   # stay under the classic Windows 260-char limit


def fit_path(out_dir: Path, stem: str, suffix: str) -> Path:
    """Shorten the stem until the full path fits.

    AlphaPai titles run long ("OpenAI's New AI GPT-o1 STUNS The ENTIRE
    INDUSTRY..."), and a deep vault path plus that title exceeded MAX_PATH,
    which surfaced as a bare FileNotFoundError on write. The date prefix is
    preserved so the note stays sortable and identifiable.
    """
    dest = out_dir / f"{stem}{suffix}"
    overflow = len(str(dest)) - MAX_PATH
    if overflow <= 0:
        return dest
    keep = max(12, len(stem) - overflow - 1)
    return out_dir / f"{stem[:keep].rstrip(' ._-')}{suffix}"


def write_artifact(out_dir: Path, stem: str, blob: bytes, suffix: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = fit_path(out_dir, stem, suffix)
    dest.write_bytes(blob)
    return dest


def write_text(out_dir: Path, stem: str, text: str, suffix: str = ".md") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = fit_path(out_dir, stem, suffix)
    dest.write_text(text, encoding="utf-8")
    return dest
