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
import sys
from pathlib import Path

import fitz  # pymupdf
import pytesseract
from pytesseract import Output
from PIL import Image
from weasyprint import HTML

from export_pdf import format_ts, session_label_ar

FONT_FAMILY = "'Noto Naskh Arabic', serif"
MARKER_COLOR = (0.75, 0.1, 0.1)
BOTTOM_TRUNCATE_CHARS = 220
BOTTOM_ROW_HEIGHT = 100


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

    pix = page.get_pixmap(dpi=300)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    data = pytesseract.image_to_data(img, lang="ara", config="--psm 4", output_type=Output.DICT)
    scale = 72 / 300
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


def _embed_html(page: "fitz.Page", rect: "fitz.Rect", html_body: str) -> None:
    """
    Renders an HTML fragment via WeasyPrint to a same-size single-page PDF
    and embeds it into `page` at `rect`, instead of PyMuPDF's own
    `insert_htmlbox`. Both shape Arabic correctly on screen, but
    `insert_htmlbox`'s output embeds a broken text layer for it -- visually
    right, but `page.search_for()` finds nothing even for text plainly
    visible on the page (confirmed empirically). WeasyPrint is already the
    proven choice for correct Arabic PDF text elsewhere in this codebase
    (see export_pdf.py); reusing it here keeps the resulting PDF's
    comment text actually searchable/selectable, not just legible.
    """
    doc_html = (
        f'<html><head><meta charset="utf-8"><style>'
        f"@page {{ size: {rect.width}pt {rect.height}pt; margin: 0; }} "
        f"body {{ margin: 0; }}"
        f"</style></head><body>{html_body}</body></html>"
    )
    snippet = fitz.open(stream=HTML(string=doc_html).write_pdf(), filetype="pdf")
    page.show_pdf_page(rect, snippet, 0)
    snippet.close()


def _embed_html_flowing(
    out: "fitz.Document", page: "fitz.Page", rect: "fitz.Rect", html_body: str
) -> "fitz.Page":
    """
    Like `_embed_html`, but for content too long to fit in a single
    `rect`-sized box: rather than silently clipping it (what plain
    `show_pdf_page(rect, snippet, 0)` does to any content past the first
    page WeasyPrint renders), lets it flow onto additional same-size pages
    appended to `out`. Needed because `_comment_html`'s height estimate is
    a rough per-character heuristic, not an exact layout -- and some real
    comments (a long opening remark can run 1000+ words) need several
    pages' worth of height no matter how generously that estimate is
    sized. Returns whichever page ends up holding the tail of the content,
    so the caller knows not to keep stacking further comments under it
    without knowing how full it already is.
    """
    doc_html = (
        f'<html><head><meta charset="utf-8"><style>'
        f"@page {{ size: {rect.width}pt {rect.height}pt; margin: 0; }} "
        f"body {{ margin: 0; }}"
        f"</style></head><body>{html_body}</body></html>"
    )
    snippet = fitz.open(stream=HTML(string=doc_html).write_pdf(), filetype="pdf")
    # Captured before any new_page() call below: creating a page in `out`
    # can invalidate previously-obtained Page objects from that same
    # document (confirmed empirically -- accessing `page.rect` again after
    # a second new_page() raises "page is None"), so `page` itself must
    # not be touched again once the loop starts.
    page_w, page_h = page.rect.width, page.rect.height
    page.show_pdf_page(rect, snippet, 0)
    last_page = page
    for i in range(1, snippet.page_count):
        last_page = out.new_page(width=page_w, height=page_h)
        last_page.show_pdf_page(rect, snippet, i)
    snippet.close()
    return last_page


def _meta_html_pair(
    page: "fitz.Page",
    rect: tuple[float, float, float, float],
    number: int,
    page_label: str,
    ts_label: str,
    font_size: int,
    session_label: str = "",
) -> None:
    """
    Comment number + page label (+ session label, when known -- a
    whole-book export combines comments from several sessions, so which
    one each came from matters there), and the timestamp range, as two
    independent embedded snippets (separate bidi contexts) rather than one
    shared line: combining Arabic text/numerals and a hyphenated Western
    time range in a single RTL paragraph reorders the range's two halves
    (confirmed empirically, in both WeasyPrint and PyMuPDF's htmlbox);
    separate boxes sidestep it. The page+session label has no such range
    in it (confirmed safe empirically too, including the 11+ session
    fallback "المجلس رقم N" spelling, which does have a plain digit) so it
    stays one box, just wider than the timestamp box since it carries more.
    """
    x0, y0, x1, y1 = rect
    mid = x0 + (x1 - x0) * 0.35
    label = f"{number}. {page_label}"
    if session_label:
        label += f" · {session_label}"
    _embed_html(
        page,
        fitz.Rect(mid, y0, x1, y1),
        f'<div style="direction:rtl; text-align:right; font-family:{FONT_FAMILY}; '
        f'font-size:{font_size}px; color:#900; font-weight:bold;">{html.escape(label)}</div>',
    )
    _embed_html(
        page,
        fitz.Rect(x0, y0, mid, y1),
        f'<div style="direction:rtl; text-align:left; font-family:{FONT_FAMILY}; '
        f'font-size:{font_size}px; color:#888;">{html.escape(ts_label)}</div>',
    )


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
) -> bytes:
    """
    style: "bottom" or "inserted" (see module docstring). Returns PDF
    bytes, matching export_pdf.build_pdf's in-memory-only pattern (nothing
    written to disk here).

    comment_font_size scales the comment body text (default 13px matches
    the size this used to be hardcoded at). The layout boxes around it
    (the fixed-height "bottom" row band, the "inserted" style's per-entry
    height estimate, and the small meta-text labels) scale with it too --
    otherwise larger text would just overflow/clip its box, since
    `_embed_html` renders into a fixed-size single page and silently drops
    anything past it. The scaling is quadratic (`scale ** 2`) for the
    text-bulk components, not linear: a bigger font both fits fewer
    characters per line *and* makes each line taller, so the area a given
    amount of text needs grows with the square of the font-size ratio.
    """
    if style not in ("bottom", "inserted"):
        raise ValueError(f"unknown style: {style!r}")

    scale = comment_font_size / 13

    method_by_page = {p["page_number"]: p["method"] for p in pages}
    by_page = _group_by_pdf_page(comments, page_offset)

    src = fitz.open(pdf_path)
    out = fitz.open()

    for i in range(src.page_count):
        pdf_page_number = i + 1
        page_comments = by_page.get(pdf_page_number, [])
        W, H = src[i].rect.width, src[i].rect.height

        if not page_comments:
            p = out.new_page(width=W, height=H)
            p.show_pdf_page(fitz.Rect(0, 0, W, H), src, i)
            continue

        word_boxes = _page_word_boxes(src, i, method_by_page.get(pdf_page_number, "ocr"))
        printed_label = f"الصفحة {pdf_page_number + page_offset}"

        if style == "bottom":
            row_height = round(BOTTOM_ROW_HEIGHT * scale ** 2)
            band_h = 20 + row_height * len(page_comments)
            p = out.new_page(width=W, height=H + band_h)
            p.show_pdf_page(fitz.Rect(0, 0, W, H), src, i)
            for number, c in page_comments:
                _draw_marker(p, _anchor_bbox(word_boxes, c.get("position_in_page")), number)
            p.draw_line((30, H + 14), (W - 30, H + 14), color=(0.7, 0.7, 0.7), width=0.75)

            y = H + 20
            for number, c in page_comments:
                _meta_html_pair(
                    p, (30, y, W - 30, y + 13), number, printed_label, format_ts_range(c),
                    font_size=max(7, round(9 * scale)), session_label=session_label_ar(c.get("session_number")),
                )
                html_body = _comment_html(c["text"], font_size=comment_font_size, truncate=BOTTOM_TRUNCATE_CHARS)
                _embed_html(p, fitz.Rect(30, y + 14, W - 30, y + row_height - 4), html_body)
                y += row_height
            continue

        # style == "inserted"
        p = out.new_page(width=W, height=H)
        p.show_pdf_page(fitz.Rect(0, 0, W, H), src, i)
        for number, c in page_comments:
            _draw_marker(p, _anchor_bbox(word_boxes, c.get("position_in_page")), number)

        notes = out.new_page(width=W, height=H)
        _embed_html(
            notes,
            fitz.Rect(40, 40, W - 40, 70),
            f'<div style="direction:rtl; text-align:right; font-family:{FONT_FAMILY}; '
            f'font-size:15px; color:#333; border-bottom:1px solid #ccc; padding-bottom:8px;">'
            f"تعليقات {printed_label}</div>",
        )
        y = 90
        for number, c in page_comments:
            if y > H - 80:
                notes = out.new_page(width=W, height=H)
                y = 40

            chars_per_line = max(15, round(45 / scale))
            line_height = round(24 * scale)
            base_h = round(70 * scale)
            needed_h = base_h + (len(c["text"]) // chars_per_line) * line_height
            available_h = H - y - 40

            if needed_h > available_h:
                # Doesn't fit even generously in what's left on this page --
                # start this comment fresh instead of clipping it (a long
                # opening remark can run to 1000+ words / several pages'
                # worth, see _embed_html_flowing).
                notes = out.new_page(width=W, height=H)
                y = 40
                available_h = H - y - 40

            _meta_html_pair(
                notes, (40, y, W - 40, y + 16), number, printed_label, format_ts_range(c),
                font_size=max(7, round(10 * scale)), session_label=session_label_ar(c.get("session_number")),
            )
            entry_h = min(needed_h, available_h)
            html_body = _comment_html(c["text"], font_size=comment_font_size)
            last_page = _embed_html_flowing(
                out, notes, fitz.Rect(40, y + 18, W - 40, y + entry_h), html_body
            )
            if last_page is not notes:
                # Content overflowed onto further pages -- don't guess how
                # much of the last one is free, just start the next
                # comment on its own fresh page.
                notes = out.new_page(width=W, height=H)
                y = 40
            else:
                y += entry_h + 14

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
