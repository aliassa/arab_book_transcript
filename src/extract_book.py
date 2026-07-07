"""
Book text extraction for the reading-club pipeline.

Handles the reality that Arabic PDFs vary a lot:
- Some have real selectable text (fast, accurate extraction).
- Some are scanned images (need OCR).
- Some are "fake text" PDFs where extraction returns garbage
  (broken font encoding maps -- common with older Arabic PDFs).

Strategy: try direct text extraction per page, run a quality check
on the result, and fall back to OCR for any page that fails the check.
This is done per-page (not per-file) since a single PDF can mix
scanned and digital pages.

Output: JSON list of {page_number, text, method} so we always know
which pages came from OCR (lower trust) vs direct extraction.
"""

import json
import re
import sys
from pathlib import Path

import fitz  # pymupdf
import pytesseract
from PIL import Image

# Arabic block + common presentation forms
ARABIC_CHAR_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﻿]")


def text_quality_score(text: str) -> float:
    """
    Rough heuristic: what fraction of non-whitespace characters
    are plausible Arabic characters (or digits/punctuation)?
    Garbled font-encoding extractions produce mostly symbols,
    private-use-area characters, or control characters.
    """
    stripped = text.strip()
    if len(stripped) < 3:
        return 0.0

    non_space = [c for c in stripped if not c.isspace()]
    if not non_space:
        return 0.0

    good = sum(
        1
        for c in non_space
        if ARABIC_CHAR_RE.match(c) or c.isdigit() or c in ".,:;؛،؟!()-\"'ـ«»"
    )
    return good / len(non_space)


def extract_page_direct(page: "fitz.Page") -> str:
    return page.get_text("text")


def ocr_page(page: "fitz.Page", dpi: int = 300) -> str:
    """Render page to image and OCR it with Tesseract (Arabic model)."""
    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    # psm 4 (column of variable-sized text), not 6 (uniform block): book pages
    # here mix a decorative header/logo line with body paragraphs, and psm 6's
    # "uniform block" assumption measurably degrades word-level accuracy on
    # that mixed layout -- confirmed by comparing OCR output against several
    # page images by eye (e.g. "قامومي"/"والحهد" under psm 6 vs correctly
    # "قاموسي"/"والجهد" under psm 4 for the same source image).
    config = "--psm 4"
    return pytesseract.image_to_string(img, lang="ara", config=config)


def extract_book(
    pdf_path: str,
    quality_threshold: float = 0.6,
    force_ocr: bool = False,
) -> list[dict]:
    """
    Returns a list of dicts: {page_number (1-indexed), text, method, quality}
    """
    doc = fitz.open(pdf_path)
    pages_out = []

    for i, page in enumerate(doc):
        page_number = i + 1
        method = "ocr" if force_ocr else "direct"
        text = "" if force_ocr else extract_page_direct(page)
        quality = 0.0 if force_ocr else text_quality_score(text)

        if force_ocr or quality < quality_threshold:
            ocr_text = ocr_page(page)
            ocr_quality = text_quality_score(ocr_text)
            # Prefer whichever is better, but mark that OCR was used/attempted.
            if force_ocr or ocr_quality >= quality:
                text, quality, method = ocr_text, ocr_quality, "ocr"
            else:
                method = "direct_low_quality"

        pages_out.append(
            {
                "page_number": page_number,
                "text": text.strip(),
                "method": method,
                "quality": round(quality, 3),
            }
        )
        print(
            f"  page {page_number:>4} | method={method:<18} | quality={quality:.2f}",
            file=sys.stderr,
        )

    return pages_out


def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_book.py <book.pdf> [output.json]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else Path(pdf_path).stem + "_pages.json"

    print(f"Extracting: {pdf_path}", file=sys.stderr)
    pages = extract_book(pdf_path)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)

    n_ocr = sum(1 for p in pages if p["method"] == "ocr")
    print(f"\nDone. {len(pages)} pages -> {out_path}", file=sys.stderr)
    print(f"{n_ocr}/{len(pages)} pages required OCR.", file=sys.stderr)


if __name__ == "__main__":
    main()
