"""
Annotated book-PDF export for the reading-club pipeline.

Alternative to export_pdf.py's separate summary report: overlays each
comment directly onto the book's own PDF pages instead of a separate
document, since flipping between a standalone report and the book itself
turned out to be impractical for actually using the output while reading
(a reviewer confirmed this after comparing mockups of both).

Two styles, sharing everything except the per-page layout step:
- "bottom": the page is heightened and comment text goes in the new
  whitespace appended to the bottom of that same page. Comment text is
  truncated to fit, since a very long comment (a whole page's worth of
  intro remarks, say) won't fit in a page-bottom strip.
- "inserted": the original page is left untouched and a new page is
  inserted right after it with the comment text -- no truncation needed,
  at the cost of the book's page count growing.

Both place a small numbered marker at each comment's anchor word -- the
same word align.py already anchors the comment to via `position_in_page`.
This makes that anchor visible; it does not make it any more accurate
than what align.py already computed (see align.py's infer_page docstring
on why a comment's position is a nearest-neighbor estimate, not exact).
Comments are numbered sequentially across the whole book, not restarted
per page, since a page-local "comment #1" would be ambiguous to look up
across a book with comments on many pages.
"""

import html
import json
import sys
from pathlib import Path

import fitz  # pymupdf
import pytesseract
from pytesseract import Output
from PIL import Image
from weasyprint import HTML

from export_pdf import COVER_GREEN, COVER_TITLE_CSS, cover_page_html, format_ts, session_label_ar

FONT_FAMILY = "'Noto Naskh Arabic', serif"
MARKER_COLOR = (0.75, 0.1, 0.1)
BOTTOM_TRUNCATE_CHARS = 220
# Bump to invalidate every existing word-box cache file if the OCR settings
# below (dpi, psm) ever change -- cached boxes from different settings would
# silently misplace markers.
WORD_BOX_OCR_DPI = 300


def _page_word_boxes(doc: "fitz.Document", pdf_page_index: int, method: str) -> list[tuple[str, "fitz.Rect"]]:
    """
    Word-level (text, bbox-in-points) list for one page, in the same
    reading order extract_book.py's stored page text is in -- direct text
    extraction (PyMuPDF) for digitally-extracted pages, OCR word boxes
    (Tesseract) for OCR'd ones -- so a comment's position_in_page indexes
    into this list approximately the way it indexes into that page's
    tokenized text.

    Approximately, not exactly: Tesseract's image_to_data word segmentation
    isn't guaranteed identical to image_to_string's (used for the stored
    page text extract_book.py tokenizes to compute position_in_page), so
    this is a best-effort visual aid, not a guarantee -- same caveat as
    the rest of a comment's inferred position.
    """
    page = doc[pdf_page_index]
    if method == "direct":
        return [(w[4], fitz.Rect(w[0], w[1], w[2], w[3])) for w in page.get_text("words")]

    pix = page.get_pixmap(dpi=WORD_BOX_OCR_DPI)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(img, lang="ara", config="--psm 4", output_type=Output.DICT)
    scale = 72 / WORD_BOX_OCR_DPI
    out = []
    for i in range(len(data["text"])):
        if not data["text"][i].strip():
            continue
        x = data["left"][i] * scale
        y = data["top"][i] * scale
        w = data["width"][i] * scale
        h = data["height"][i] * scale
        out.append((data["text"][i], fitz.Rect(x, y, x + w, y + h)))
    return out


def _word_box_cache_path(pdf_path: Path) -> Path:
    return pdf_path.parent / f".{pdf_path.stem}_word_boxes_cache.json"


def _load_word_box_cache(pdf_path: Path) -> dict[str, list]:
    """
    OCR word boxes persisted next to the book PDF (hidden dotfile, same
    pattern and rationale as app.py's page-text cache): re-deriving them is
    the slowest part of an export render (~1s of Tesseract per commented
    page, on every render click, for a fully-scanned book), yet they only
    change when the PDF or the OCR settings do -- both part of the key.
    Only OCR'd pages are cached; "direct" boxes are milliseconds to redo.
    """
    try:
        data = json.loads(_word_box_cache_path(pdf_path).read_text(encoding="utf-8"))
        if data.get("mtime") == pdf_path.stat().st_mtime and data.get("dpi") == WORD_BOX_OCR_DPI:
            return data["pages"]
    except (OSError, ValueError, KeyError):
        pass
    return {}


def _save_word_box_cache(pdf_path: Path, pages: dict[str, list]) -> None:
    payload = {"mtime": pdf_path.stat().st_mtime, "dpi": WORD_BOX_OCR_DPI, "pages": pages}
    try:
        _word_box_cache_path(pdf_path).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass  # read-only book dir just means the next render re-OCRs


def _anchor_bbox(word_boxes: list[tuple[str, "fitz.Rect"]], position_in_page: int | None) -> "fitz.Rect | None":
    if not word_boxes or not position_in_page:
        return None
    idx = min(position_in_page, len(word_boxes)) - 1
    return word_boxes[idx][1]


def _draw_marker(page: "fitz.Page", bbox: "fitz.Rect | None", number: int) -> None:
    """
    Small numbered circle centered directly above the anchor word, in the
    gap between text lines. Horizontally centered *over the word's own
    width* rather than offset past either edge: offsetting sideways only
    has guaranteed clearance when the word happens to sit at a line's
    start/end (blank margin next to it) -- a word in the middle of a line,
    which is the common case, has another word right up against it with
    only normal inter-word spacing, and a sideways offset lands on top of
    it (confirmed on a real generated book: a marker sitting between two
    words ended up overlapping both). Centering above only ever needs
    vertical clearance from the line above, which ordinary line spacing
    reliably provides.

    Falls back to a fixed top-corner spot when no anchor word could be
    found (e.g. an opening remark with no preceding book position at all)
    rather than silently dropping the marker.
    """
    if bbox is not None:
        cx = min(max((bbox.x0 + bbox.x1) / 2, 14), page.rect.width - 14)
        cy = max(bbox.y0 - 14, 14)
    else:
        cx, cy = page.rect.width - 20, 20
    page.draw_circle((cx, cy), 4, color=MARKER_COLOR, fill=(1, 1, 1), width=0.8)
    page.insert_text((cx - 1.8, cy + 1.8), str(number), fontsize=5.2, color=MARKER_COLOR, fontname="helv")


def _render_html(width: float, height: float, body: str, margin: float = 0) -> "fitz.Document":
    """
    Renders an HTML fragment via WeasyPrint to a (possibly multi-page)
    `width`x`height`pt PDF and returns it opened, instead of drawing text
    with PyMuPDF's own `insert_htmlbox`. Both shape Arabic correctly on
    screen, but `insert_htmlbox`'s output embeds a broken text layer for
    it -- visually right, but `page.search_for()` finds nothing even for
    text plainly visible on the page (confirmed empirically). WeasyPrint
    is already the proven choice for correct Arabic PDF text elsewhere in
    this codebase (see export_pdf.py); reusing it here keeps the resulting
    PDF's comment text actually searchable/selectable, not just legible.
    """
    doc_html = (
        f'<html><head><meta charset="utf-8"><style>'
        f"@page {{ size: {width}pt {height}pt; margin: {margin}pt; }} "
        f"body {{ margin: 0; }}"
        f"</style></head><body>{body}</body></html>"
    )
    return fitz.open(stream=HTML(string=doc_html).write_pdf(), filetype="pdf")


def _entries_html(
    page_comments: list[tuple[int, dict]],
    printed_label: str,
    comment_font_size: int,
    truncate: int | None = None,
    header: str = "",
) -> str:
    """
    One flowing HTML document body for everything a commented page needs:
    optional header, then per comment a meta line (number + page label +
    session label when known -- a whole-book export combines comments from
    several sessions, so which one each came from matters there -- and the
    timestamp range) followed by the comment text. Laying the whole page
    out as one document and letting WeasyPrint flow/paginate it replaced
    an earlier scheme that embedded each piece into its own fixed-height
    box sized by a per-character height *estimate*: the estimate had to be
    generous to never clip, so real pages came out with the over-estimate
    as dead whitespace after every comment, and a comment that outgrew the
    space left on its page was bumped whole to a fresh page -- stranding a
    header alone on a nearly-empty page whenever the first comment was the
    one bumped. Flow layout has neither problem, and renders a whole page
    in one WeasyPrint call instead of three per comment.

    Bidi caveat, why the meta line is two separately-isolated blocks and
    not one paragraph: combining Arabic text/numerals and a hyphenated
    Western time range in a single RTL paragraph reorders the range's two
    halves (confirmed empirically, in both WeasyPrint and PyMuPDF's
    htmlbox). Each flex cell here is its own block with an explicit
    `direction`, i.e. its own bidi paragraph -- re-confirmed extractable
    in the right order after the flow-layout rewrite. The page+session
    label has no such range in it (also confirmed safe, including the 11+
    session fallback "المجلس رقم N" spelling, which does have a plain
    digit) so it stays one block.
    """
    scale = comment_font_size / 13
    meta_size = max(7, round(10 * scale))
    parts = []
    if header:
        parts.append(
            f'<div style="direction:rtl; text-align:right; font-family:{FONT_FAMILY}; '
            f"font-size:{max(11, round(15 * scale))}px; color:#333; "
            f'border-bottom:1px solid #ccc; padding-bottom:8px; margin-bottom:14px;">'
            f"{html.escape(header)}</div>"
        )
    for number, c in page_comments:
        label = f"{number}. {printed_label}"
        session_label = session_label_ar(c.get("session_number"))
        if session_label:
            label += f" · {session_label}"
        parts.append(
            f'<div style="display:flex; justify-content:space-between; direction:rtl; '
            f'margin-bottom:3px; page-break-after:avoid;">'
            f'<div style="direction:rtl; font-family:{FONT_FAMILY}; font-size:{meta_size}px; '
            f'color:#900; font-weight:bold;">{html.escape(label)}</div>'
            f'<div style="direction:ltr; font-family:{FONT_FAMILY}; font-size:{meta_size}px; '
            f'color:#888;">{html.escape(format_ts_range(c))}</div>'
            f"</div>"
        )
        body = _comment_html(c["text"], font_size=comment_font_size, truncate=truncate)
        parts.append(f'<div style="margin-bottom:{round(14 * scale)}px;">{body}</div>')
    return "".join(parts)


def _comment_html(text: str, font_size: int, truncate: int | None = None) -> str:
    display = text
    note = ""
    if truncate and len(text) > truncate:
        display = text[:truncate].rsplit(" ", 1)[0] + "…"
        note = (
            f' <span style="color:#888; font-size:{max(font_size - 3, 8)}px;">'
            "(نص كامل أطول من أن يتسع هنا)</span>"
        )
    return (
        f'<div style="direction:rtl; text-align:right; font-family:{FONT_FAMILY}; '
        f'font-size:{font_size}px; color:#222; line-height:1.6;">{html.escape(display)}{note}</div>'
    )


def _insert_cover_page(
    out: "fitz.Document",
    width: float,
    height: float,
    book_title_ar: str,
    author_ar: str,
    commentator_ar: str,
    club_image_bytes: bytes | None,
    telegram_icon_bytes: bytes | None = None,
    facebook_icon_bytes: bytes | None = None,
) -> None:
    """
    Club-branded front matter (logo, book/commentator metadata, Telegram/
    Facebook links -- see export_pdf.cover_page_html, shared with the
    separate-report export) as the very first page of the book copy, sized
    to match the book's own pages so it doesn't stand out as an odd paper
    size. Rendered through WeasyPrint and embedded via show_pdf_page like
    every other piece of overlay text in this module, for the same
    searchable-text reason documented on _embed_html -- not PyMuPDF's own
    insert_htmlbox.
    """
    body = cover_page_html(
        book_title_ar=book_title_ar,
        author_ar=author_ar,
        commentator_ar=commentator_ar,
        club_image_bytes=club_image_bytes,
        telegram_icon_bytes=telegram_icon_bytes,
        facebook_icon_bytes=facebook_icon_bytes,
    )
    doc_html = f"""
    <html dir="rtl" lang="ar">
    <head>
    <meta charset="utf-8">
    <style>
      @page {{ size: {width}pt {height}pt; margin: 0; }}
      body {{
        margin: 0; box-sizing: border-box; height: {height}pt;
        font-family: {FONT_FAMILY}; direction: rtl; text-align: center; color: #1c231f;
        display: flex; align-items: center; justify-content: center;
      }}
      .cover-page {{ padding: 32pt; box-sizing: border-box; }}
      .cover-title {{ font-size: 28pt; margin: 0 0 10pt; }}
      {COVER_TITLE_CSS}
      .cover-image {{ max-width: 78%; max-height: 300pt; margin: 0 auto 22pt; display: block; }}
      .cover-meta {{ margin-bottom: 20pt; }}
      .cover-meta-line {{ font-size: 16pt; color: #333; margin-bottom: 7pt; }}
      .cover-social {{ display: flex; justify-content: center; gap: 22pt; margin-top: 10pt; }}
      .cover-social-link {{
        display: flex; align-items: center; gap: 6pt;
        text-decoration: none; color: {COVER_GREEN};
      }}
      .cover-social-icon {{ width: 26pt; height: 26pt; display: block; }}
      .cover-social-label {{ font-size: 12pt; font-weight: bold; white-space: nowrap; }}
    </style>
    </head>
    <body>{body}</body>
    </html>
    """
    snippet = fitz.open(stream=HTML(string=doc_html).write_pdf(), filetype="pdf")
    page = out.new_page(width=width, height=height)
    page.show_pdf_page(fitz.Rect(0, 0, width, height), snippet, 0)
    snippet.close()


def _group_by_pdf_page(comments: list[dict], page_offset: int) -> dict[int, list[tuple[int, dict]]]:
    """
    Groups (numbered sequentially across the whole book) comments by the
    PDF page they belong to. `comments[i]["page"]` is a printed page
    number (page_offset already applied, same as everywhere else the UI
    shows/exports it) -- reversed here only to locate the right PDF page;
    the printed number itself is used unchanged for display.
    """
    by_page: dict[int, list[tuple[int, dict]]] = {}
    for i, c in enumerate(comments, start=1):
        if c.get("page") is None:
            continue
        pdf_page_number = c["page"] - page_offset
        by_page.setdefault(pdf_page_number, []).append((i, c))
    return by_page


def build_annotated_pdf(
    pdf_path: str,
    pages: list[dict],
    comments: list[dict],
    page_offset: int,
    style: str,
    comment_font_size: int = 13,
    book_title_ar: str = "",
    author_ar: str = "",
    commentator_ar: str = "",
    club_image_bytes: bytes | None = None,
    telegram_icon_bytes: bytes | None = None,
    facebook_icon_bytes: bytes | None = None,
) -> bytes:
    """
    style: "bottom" or "inserted" (see module docstring). Returns PDF
    bytes, matching export_pdf.build_pdf's in-memory-only pattern (nothing
    written to disk here).

    comment_font_size scales the comment body text (default 13px matches
    the size this used to be hardcoded at); the meta labels and header
    scale with it. Comment layout is one flowing HTML document per
    commented page (see _entries_html on why, and on what the earlier
    fixed-box scheme got wrong).

    book_title_ar/author_ar/commentator_ar/club_image_bytes add a
    club-branded cover page (see _insert_cover_page) as page one of the
    output -- only when at least one of them is given, so callers/tests
    that don't pass any of this metadata get the exact same page count as
    before this was added.
    """
    if style not in ("bottom", "inserted"):
        raise ValueError(f"unknown style: {style!r}")

    scale = comment_font_size / 13

    method_by_page = {p["page_number"]: p["method"] for p in pages}
    by_page = _group_by_pdf_page(comments, page_offset)

    src = fitz.open(pdf_path)
    out = fitz.open()

    box_cache = _load_word_box_cache(Path(pdf_path))
    box_cache_dirty = False

    if book_title_ar or author_ar or commentator_ar or club_image_bytes:
        first_rect = src[0].rect
        _insert_cover_page(
            out, first_rect.width, first_rect.height,
            book_title_ar, author_ar, commentator_ar, club_image_bytes,
            telegram_icon_bytes, facebook_icon_bytes,
        )

    for i in range(src.page_count):
        pdf_page_number = i + 1
        page_comments = by_page.get(pdf_page_number, [])
        W, H = src[i].rect.width, src[i].rect.height

        if not page_comments:
            p = out.new_page(width=W, height=H)
            p.show_pdf_page(fitz.Rect(0, 0, W, H), src, i)
            continue

        method = method_by_page.get(pdf_page_number, "ocr")
        cache_key = str(i)
        if method != "direct" and cache_key in box_cache:
            word_boxes = [(t, fitz.Rect(x0, y0, x1, y1)) for t, x0, y0, x1, y1 in box_cache[cache_key]]
        else:
            word_boxes = _page_word_boxes(src, i, method)
            if method != "direct":
                box_cache[cache_key] = [[t, r.x0, r.y0, r.x1, r.y1] for t, r in word_boxes]
                box_cache_dirty = True
        printed_label = f"الصفحة {pdf_page_number + page_offset}"

        if style == "bottom":
            # Render the band's content once at a generously tall single
            # page, measure how tall it actually came out, and grow the
            # book page by exactly that much -- no estimate, no dead space.
            band_w = W - 60
            body = _entries_html(
                page_comments, printed_label, comment_font_size, truncate=BOTTOM_TRUNCATE_CHARS
            )
            max_h = 80 + round(180 * scale ** 2) * len(page_comments)
            snippet = _render_html(band_w, max_h, body)
            while snippet.page_count > 1:  # bound was somehow still too tight
                snippet.close()
                max_h *= 2
                snippet = _render_html(band_w, max_h, body)
            blocks = snippet[0].get_text("blocks")
            content_h = max((b[3] for b in blocks), default=20) + 4

            p = out.new_page(width=W, height=H + 20 + content_h + 12)
            p.show_pdf_page(fitz.Rect(0, 0, W, H), src, i)
            for number, c in page_comments:
                _draw_marker(p, _anchor_bbox(word_boxes, c.get("position_in_page")), number)
            p.draw_line((30, H + 14), (W - 30, H + 14), color=(0.7, 0.7, 0.7), width=0.75)
            p.show_pdf_page(
                fitz.Rect(30, H + 20, W - 30, H + 20 + content_h),
                snippet, 0, clip=fitz.Rect(0, 0, band_w, content_h),
            )
            snippet.close()
            continue

        # style == "inserted"
        p = out.new_page(width=W, height=H)
        p.show_pdf_page(fitz.Rect(0, 0, W, H), src, i)
        for number, c in page_comments:
            _draw_marker(p, _anchor_bbox(word_boxes, c.get("position_in_page")), number)

        body = _entries_html(
            page_comments, printed_label, comment_font_size, header=f"تعليقات {printed_label}"
        )
        snippet = _render_html(W, H, body, margin=40)
        for j in range(snippet.page_count):
            notes = out.new_page(width=W, height=H)
            notes.show_pdf_page(fitz.Rect(0, 0, W, H), snippet, j)
        snippet.close()

    if box_cache_dirty:
        _save_word_box_cache(Path(pdf_path), box_cache)

    pdf_bytes = out.tobytes()
    out.close()
    src.close()
    return pdf_bytes


def format_ts_range(comment: dict) -> str:
    return f"{format_ts(comment['start'])}-{format_ts(comment['end'])}"


def main():
    if len(sys.argv) < 5:
        print(
            "Usage: python export_annotated_pdf.py <book.pdf> <book_pages.json> "
            "<comments.json> <bottom|inserted> [page_offset] [out.pdf]"
        )
        sys.exit(1)

    import json

    pdf_path, pages_path, comments_path, style = sys.argv[1:5]
    page_offset = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    out_path = sys.argv[6] if len(sys.argv) > 6 else f"{Path(pdf_path).stem}_annotated_{style}.pdf"

    pages = json.loads(Path(pages_path).read_text(encoding="utf-8"))
    comments = json.loads(Path(comments_path).read_text(encoding="utf-8"))

    pdf_bytes = build_annotated_pdf(pdf_path, pages, comments, page_offset, style)
    Path(out_path).write_bytes(pdf_bytes)
    print(f"Done. -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
