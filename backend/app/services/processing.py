"""Background processing: thumbnails, CSV parsing and format metadata.

`process_file` is the job entry point. It is import-safe for an RQ worker
(`rq worker docvault`) and for the in-process fallback thread pool.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import zipfile
from pathlib import Path

from . import storage
from .validation import category_for

log = logging.getLogger("docvault.processing")

THUMB_SIZE = (480, 480)
CSV_PREVIEW_ROWS = 20
CSV_MAX_SCAN_BYTES = 8 * 1024 * 1024


# ------------------------------------------------------------------ images
def make_image_thumbnail(src: Path, dest: Path) -> dict:
    from PIL import Image, ImageOps

    with Image.open(src) as im:
        width, height = im.size
        fmt = im.format
        mode = im.mode
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
        dest.parent.mkdir(parents=True, exist_ok=True)
        im.save(dest, "JPEG", quality=82, optimize=True)
    return {"width": width, "height": height, "format": fmt, "mode": mode}


# -------------------------------------------------------------------- pdf
def pdf_metadata(src: Path) -> dict:
    """Page count + title without a heavy dependency."""
    data = src.read_bytes()
    pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
    if not pages:
        m = re.search(rb"/Count\s+(\d+)", data)
        pages = int(m.group(1)) if m else 0
    title = None
    m = re.search(rb"/Title\s*\((.{0,200}?)\)", data, re.S)
    if m:
        title = m.group(1).decode("latin-1", "ignore").strip() or None
    version = data[:8].decode("latin-1", "ignore").replace("%PDF-", "").strip()
    return {"pages": pages, "title": title, "pdf_version": version}


def make_pdf_thumbnail(src: Path, dest: Path) -> bool:
    """Render page 1 if pypdfium2 is installed; otherwise skip silently."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return False
    try:
        pdf = pdfium.PdfDocument(str(src))
        page = pdf[0]
        image = page.render(scale=1.6).to_pil()
        image.thumbnail(THUMB_SIZE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(dest, "JPEG", quality=82)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF thumbnail failed: %s", exc)
        return False


# -------------------------------------------------------------------- csv
def process_csv(src: Path) -> dict:
    raw = src.read_bytes()[:CSV_MAX_SCAN_BYTES]
    truncated = src.stat().st_size > CSV_MAX_SCAN_BYTES
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        encoding = "latin-1"

    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    headers: list[str] = []
    preview: list[list[str]] = []
    row_count = 0
    column_counts: dict[int, int] = {}

    for i, row in enumerate(reader):
        if i == 0:
            headers = [h.strip() for h in row]
            continue
        if not any(cell.strip() for cell in row):
            continue
        row_count += 1
        column_counts[len(row)] = column_counts.get(len(row), 0) + 1
        if len(preview) < CSV_PREVIEW_ROWS:
            preview.append([cell[:120] for cell in row[: len(headers) or 30]])

    ragged = len(column_counts) > 1
    empty_headers = sum(1 for h in headers if not h)
    return {
        "kind": "csv",
        "encoding": encoding,
        "delimiter": delimiter,
        "columns": len(headers),
        "headers": headers[:60],
        "row_count": row_count,
        "preview": preview,
        "truncated": truncated,
        "warnings": [
            *(["Rows have inconsistent column counts."] if ragged else []),
            *([f"{empty_headers} header cell(s) are empty."] if empty_headers else []),
            *(["File was large; only the first 8 MB were scanned."] if truncated else []),
        ],
    }


# ------------------------------------------------------------------- docx
def docx_metadata(src: Path) -> dict:
    meta: dict = {"kind": "docx"}
    try:
        with zipfile.ZipFile(src) as z:
            names = set(z.namelist())
            if "docProps/app.xml" in names:
                app = z.read("docProps/app.xml").decode("utf-8", "ignore")
                for key, tag in (("pages", "Pages"), ("words", "Words"), ("characters", "Characters")):
                    m = re.search(rf"<{tag}>(\d+)</{tag}>", app)
                    if m:
                        meta[key] = int(m.group(1))
            if "docProps/core.xml" in names:
                core = z.read("docProps/core.xml").decode("utf-8", "ignore")
                for key, tag in (("title", "dc:title"), ("author", "dc:creator")):
                    m = re.search(rf"<{tag}>(.*?)</{tag}>", core, re.S)
                    if m and m.group(1).strip():
                        meta[key] = m.group(1).strip()[:200]
            meta["embedded_media"] = sum(1 for n in names if n.startswith("word/media/"))
    except zipfile.BadZipFile:
        raise ValueError("DOCX file is corrupt or not a valid Office document") from None
    return meta


# ----------------------------------------------------------------- driver
def analyze(path: Path, ext: str, file_id: str) -> tuple[dict, str | None]:
    """Return (extra_metadata, thumbnail_relative_path)."""
    category = category_for(ext)
    thumb_rel: str | None = None
    extra: dict = {"kind": category}

    if category == "image":
        dest = storage.thumb_file(file_id)
        extra.update(make_image_thumbnail(path, dest))
        thumb_rel = dest.name
    elif category == "pdf":
        extra.update(pdf_metadata(path))
        dest = storage.thumb_file(file_id)
        if make_pdf_thumbnail(path, dest):
            thumb_rel = dest.name
    elif category == "csv":
        extra = process_csv(path)
    elif category == "docx":
        extra = docx_metadata(path)

    return extra, thumb_rel


def process_file(file_id: str) -> str:
    """Job entry point — runs in the RQ worker or the fallback thread."""
    from ..database import SessionLocal
    from ..models import FileRecord, FileStatus

    db = SessionLocal()
    try:
        record = db.get(FileRecord, file_id)
        if record is None:
            return "missing"

        path = storage.blob_file(record.sha256)
        if not path.exists():
            record.status = FileStatus.failed
            record.status_detail = "Stored file is missing from disk"
            db.commit()
            return "failed"

        try:
            extra, thumb = analyze(path, record.extension, str(record.id))
            record.extra = json.loads(json.dumps(extra, default=str))
            record.thumbnail_path = thumb
            record.status = FileStatus.ready
            record.status_detail = ""
            db.commit()
            log.info("Processed %s (%s)", record.filename, record.extension)
            return "ready"
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            record = db.get(FileRecord, file_id)
            if record:
                record.status = FileStatus.failed
                record.status_detail = str(exc)[:400]
                db.commit()
            log.exception("Processing failed for %s", file_id)
            return "failed"
    finally:
        db.close()
