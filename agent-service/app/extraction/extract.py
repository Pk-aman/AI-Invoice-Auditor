"""Text extraction: PDF (PyMuPDF direct-first, per-page Tesseract fallback), DOCX (python-docx), images (Tesseract + OpenCV preprocessing)."""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PDF_EXT = {".pdf"}
DOCX_EXT = {".docx"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_EXT = PDF_EXT | DOCX_EXT | IMAGE_EXT


FAILED_METHOD = "failed"


class ExtractionError(RuntimeError):
    """Extraction could not produce text. `method` is what gets reported in the processing event."""

    method = FAILED_METHOD


@dataclass
class ExtractionResult:
    text: str
    method: str  # pymupdf | tesseract | pymupdf+tesseract | docx  (FAILED_METHOD is only ever reported, never stored)
    ocr_confidence: float | None = None  # 0..1 mean word confidence, when OCR was used


def detect_type(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in DOCX_EXT:
        return "docx"
    if ext in IMAGE_EXT:
        return "image"
    return None


# ---- images ------------------------------------------------------------------
def preprocess_image(img):
    """PIL image -> cleaned grayscale PIL image (EXIF-rotate, downscale/upscale, denoise, deskew, threshold)."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(img).convert("L")
    w, h = img.size
    longest = max(w, h)
    if longest > 2800:  # keep denoise tractable for phone photos
        scale = 2800 / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    elif longest < 1200:
        scale = 1600 / longest
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    arr = np.array(img)
    arr = cv2.fastNlMeansDenoising(arr, None, h=10, templateWindowSize=7, searchWindowSize=21)

    # deskew from the dark-pixel mass; ignore unreliable / tiny angles
    inv = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) > 500:
        angle = cv2.minAreaRect(coords.astype("float32"))[-1]
        angle = -(90 + angle) if angle < -45 else -angle if angle < 45 else 90 - angle
        if 0.5 < abs(angle) < 15:
            hh, ww = arr.shape
            m = cv2.getRotationMatrix2D((ww / 2, hh / 2), angle, 1.0)
            arr = cv2.warpAffine(arr, m, (ww, hh), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    arr = cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 15)
    return Image.fromarray(arr)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(img, langs: str, *, preprocess: bool = True) -> tuple[str, float | None]:
    if not tesseract_available():
        raise ExtractionError("Tesseract is not installed (OCR required for this document)")
    import pytesseract

    work = preprocess_image(img) if preprocess else img
    config = "--oem 1 --psm 6"
    try:
        text = pytesseract.image_to_string(work, lang=langs, config=config)
        data = pytesseract.image_to_data(work, lang=langs, config=config, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError as exc:
        raise ExtractionError(f"Tesseract failed (language packs {langs!r} installed?): {exc}") from exc
    confs = [float(c) for c, t in zip(data["conf"], data["text"], strict=False) if str(t).strip() and float(c) >= 0]
    return text, (sum(confs) / len(confs) / 100.0 if confs else None)


def extract_image(path: Path, langs: str) -> ExtractionResult:
    from PIL import Image

    with Image.open(path) as img:
        text, conf = ocr_image(img, langs)
    return ExtractionResult(text=text, method="tesseract", ocr_confidence=conf)


# ---- pdf ---------------------------------------------------------------------
def _render_page(page, dpi: int = 300):
    """Render ONE PDF page to a PIL image (PyMuPDF; no Poppler needed)."""
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def extract_pdf(path: Path, langs: str, min_chars: int = 30) -> ExtractionResult:
    """Direct-first: PyMuPDF text layer per page; pages with too little text are rendered and OCR'd individually.

    Page order is preserved. method = pymupdf (no OCR) | tesseract (every page OCR'd) | pymupdf+tesseract (mixed).
    """
    import pymupdf

    pages: list[str] = []
    ocr_pages = 0
    confs: list[float] = []
    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:  # noqa: BLE001 - malformed pdf
        raise ExtractionError(f"cannot read PDF: {type(exc).__name__}: {exc}") from exc
    try:
        if doc.needs_pass:
            raise ExtractionError("cannot read PDF: it is password-protected")
        for number, page in enumerate(doc, start=1):
            text = (page.get_text("text") or "").strip()
            if len(text) < min_chars:
                if not tesseract_available():
                    raise ExtractionError(f"page {number} has no text layer and Tesseract is not installed")
                try:
                    image = _render_page(page)
                except Exception as exc:  # noqa: BLE001 - corrupt page
                    raise ExtractionError(f"cannot render page {number} for OCR: {exc}") from exc
                text, conf = ocr_image(image, langs)
                ocr_pages += 1
                if conf is not None:
                    confs.append(conf)
            pages.append(text)
    except ExtractionError:
        raise
    except Exception as exc:  # noqa: BLE001 - anything else from the PDF engine
        raise ExtractionError(f"cannot read PDF: {type(exc).__name__}: {exc}") from exc
    finally:
        doc.close()
    if ocr_pages == 0:
        method = "pymupdf"
    elif ocr_pages == len(pages):
        method = "tesseract"
    else:
        method = "pymupdf+tesseract"
    return ExtractionResult(text="\n\n".join(pages), method=method, ocr_confidence=(sum(confs) / len(confs) if confs else None))


# ---- docx --------------------------------------------------------------------
def extract_docx(path: Path) -> ExtractionResult:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"cannot read DOCX: {type(exc).__name__}: {exc}") from exc
    parts: list[str] = []
    for child in document.element.body.iterchildren():  # keep document order (paragraphs and tables interleaved)
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            t = Paragraph(child, document).text.strip()
            if t:
                parts.append(t)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                cells = []
                for c in row.cells:
                    if not cells or c.text.strip() != cells[-1]:  # merged cells repeat
                        cells.append(c.text.strip())
                parts.append("\t".join(cells))
    return ExtractionResult(text="\n".join(parts), method="docx")


def extract(path: Path, *, langs: str, min_chars: int = 30) -> ExtractionResult:
    kind = detect_type(path)
    if kind == "pdf":
        res = extract_pdf(path, langs, min_chars)
    elif kind == "docx":
        res = extract_docx(path)
    elif kind == "image":
        res = extract_image(path, langs)
    else:
        raise ExtractionError(f"unsupported file type: {path.suffix}")
    if not res.text.strip():
        raise ExtractionError("no text could be extracted from the document")
    return res
