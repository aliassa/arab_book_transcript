from pathlib import Path

import fitz
import pytest

from export_annotated_pdf import (
    _anchor_bbox,
    _comment_html,
    _group_by_pdf_page,
    _load_word_box_cache,
    _save_word_box_cache,
    build_annotated_pdf,
    format_ts_range,
)


# -- format_ts_range -----------------------------------------------------


def test_format_ts_range():
    assert format_ts_range({"start": 0, "end": 65}) == "0:00-1:05"


# -- _anchor_bbox ----------------------------------------------------------


def test_anchor_bbox_indexes_from_one():
    boxes = [("a", fitz.Rect(0, 0, 1, 1)), ("b", fitz.Rect(2, 2, 3, 3))]
    assert _anchor_bbox(boxes, 1) == fitz.Rect(0, 0, 1, 1)
    assert _anchor_bbox(boxes, 2) == fitz.Rect(2, 2, 3, 3)


def test_anchor_bbox_clamps_out_of_range_index():
    boxes = [("a", fitz.Rect(0, 0, 1, 1)), ("b", fitz.Rect(2, 2, 3, 3))]
    assert _anchor_bbox(boxes, 99) == fitz.Rect(2, 2, 3, 3)


def test_anchor_bbox_none_when_no_position_or_no_words():
    assert _anchor_bbox([], 1) is None
    assert _anchor_bbox([("a", fitz.Rect(0, 0, 1, 1))], None) is None
    assert _anchor_bbox([("a", fitz.Rect(0, 0, 1, 1))], 0) is None


# -- _group_by_pdf_page ------------------------------------------------------


def test_group_by_pdf_page_reverses_offset_for_lookup_only():
    comments = [
        {"page": 149, "text": "a"},  # printed page; offset 7 -> pdf page 142
        {"page": 150, "text": "b"},
    ]
    grouped = _group_by_pdf_page(comments, page_offset=7)
    assert set(grouped.keys()) == {142, 143}
    number, c = grouped[142][0]
    assert number == 1
    assert c["page"] == 149  # printed number preserved for display


def test_group_by_pdf_page_numbers_sequentially_across_book():
    comments = [{"page": 5, "text": "a"}, {"page": 5, "text": "b"}, {"page": 9, "text": "c"}]
    grouped = _group_by_pdf_page(comments, page_offset=0)
    assert [n for n, _ in grouped[5]] == [1, 2]
    assert [n for n, _ in grouped[9]] == [3]


def test_group_by_pdf_page_skips_comments_with_no_page():
    comments = [{"page": None, "text": "a"}, {"page": 3, "text": "b"}]
    grouped = _group_by_pdf_page(comments, page_offset=0)
    assert list(grouped.keys()) == [3]


# -- _comment_html -----------------------------------------------------------


def test_comment_html_escapes_and_keeps_short_text_untouched():
    out = _comment_html("<script>a</script>", font_size=13)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "أطول من أن يتسع" not in out


def test_comment_html_truncates_long_text_with_note():
    text = "كلمة " * 100
    out = _comment_html(text, font_size=13, truncate=50)
    assert "…" in out
    assert "أطول من أن يتسع" in out


# -- build_annotated_pdf: end-to-end smoke test (no OCR needed) --------------


@pytest.fixture
def two_page_pdf(tmp_path):
    """A tiny synthetic PDF with real selectable text, so "direct" word
    extraction (PyMuPDF) is exercised without needing Tesseract -- keeps
    this test fast and independent of the OCR toolchain."""
    doc = fitz.open()
    p1 = doc.new_page(width=400, height=500)
    p1.insert_text((50, 50), "hello world first page text", fontsize=12)
    p2 = doc.new_page(width=400, height=500)
    p2.insert_text((50, 50), "second page has different words here", fontsize=12)
    path = tmp_path / "book.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def pages_meta():
    return [
        {"page_number": 1, "text": "hello world first page text", "method": "direct", "quality": 1.0},
        {"page_number": 2, "text": "second page has different words here", "method": "direct", "quality": 1.0},
    ]


def test_build_annotated_pdf_bottom_style_keeps_page_count(two_page_pdf, pages_meta):
    comments = [{"page": 2, "position_in_page": 2, "text": "تعليق قصير", "start": 1.0, "end": 3.0}]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="bottom")
    assert pdf_bytes[:5] == b"%PDF-"
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 2
    # The commented page grew taller than the original 500pt; the other didn't.
    assert doc[1].rect.height > 500
    assert doc[0].rect.height == 500
    doc.close()


def test_build_annotated_pdf_inserted_style_adds_a_page(two_page_pdf, pages_meta):
    comments = [{"page": 2, "position_in_page": 2, "text": "تعليق قصير", "start": 1.0, "end": 3.0}]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="inserted")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 3  # one extra notes page after page 2
    assert doc[1].rect.height == 500  # original page untouched in height
    doc.close()


def test_build_annotated_pdf_inserted_short_comments_share_one_notes_page(two_page_pdf, pages_meta):
    # Regression: comment layout used to reserve a fixed-height slot per
    # comment from a generous per-character estimate, so a handful of short
    # comments overflowed onto extra notes pages full of dead whitespace.
    # Flow layout packs them onto one page.
    comments = [
        {"page": 2, "position_in_page": 2, "text": f"تعليق قصير {i}", "start": float(i), "end": i + 1.0}
        for i in range(4)
    ]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="inserted")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 3  # 2 book pages + a single shared notes page
    doc.close()


def test_build_annotated_pdf_inserted_header_stays_with_long_first_comment(two_page_pdf, pages_meta):
    # Regression: a first comment too long for one notes page used to be
    # bumped whole to a fresh page, stranding the "تعليقات الصفحة N" header
    # alone on a nearly-empty page. With flow layout the comment starts
    # right under the header and fragments across pages naturally.
    comments = [{"page": 2, "position_in_page": 2, "text": "كلمة " * 800, "start": 1.0, "end": 3.0}]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="inserted")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count > 3  # long enough to need several notes pages
    first_notes = doc[2]
    assert len(first_notes.search_for("تعليقات")) > 0  # header present...
    assert len(first_notes.search_for("كلمة")) > 0  # ...with comment text on the same page
    doc.close()


def test_build_annotated_pdf_bottom_band_sized_to_content(two_page_pdf, pages_meta):
    # Regression: the bottom band used to grow by a fixed 100pt-per-comment
    # row regardless of how little text each comment had.
    comments = [
        {"page": 2, "position_in_page": 2, "text": f"قصير {i}", "start": float(i), "end": i + 1.0}
        for i in range(2)
    ]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="bottom")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert 500 < doc[1].rect.height < 700  # grew, but well under the old 500+20+2x100
    doc.close()


# -- word-box cache ------------------------------------------------------------


def test_word_box_cache_roundtrip(two_page_pdf):
    pdf_path = Path(two_page_pdf)
    boxes = {"3": [["كلمة", 1.0, 2.0, 3.0, 4.0]]}
    _save_word_box_cache(pdf_path, boxes)
    assert _load_word_box_cache(pdf_path) == boxes


def test_word_box_cache_ignored_when_pdf_changes(two_page_pdf):
    # Keyed on the PDF's mtime: an edited/replaced book PDF must not reuse
    # stale word boxes, or markers would be placed off the old layout.
    import os

    pdf_path = Path(two_page_pdf)
    _save_word_box_cache(pdf_path, {"1": [["a", 0.0, 0.0, 1.0, 1.0]]})
    st = pdf_path.stat()
    os.utime(pdf_path, (st.st_atime, st.st_mtime + 10))
    assert _load_word_box_cache(pdf_path) == {}


def test_build_annotated_pdf_no_comments_passes_pages_through_unchanged(two_page_pdf, pages_meta):
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, [], page_offset=0, style="bottom")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 2
    doc.close()


def test_build_annotated_pdf_rejects_unknown_style(two_page_pdf, pages_meta):
    with pytest.raises(ValueError):
        build_annotated_pdf(two_page_pdf, pages_meta, [], page_offset=0, style="nonsense")


def test_build_annotated_pdf_shows_session_label_when_known(two_page_pdf, pages_meta):
    # A whole-book export combines comments from several sessions, so which
    # one each came from matters there -- shown next to the page label.
    comments = [
        {
            "page": 2, "position_in_page": 2, "text": "تعليق",
            "start": 1.0, "end": 3.0, "session_number": 3,
        }
    ]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="inserted")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc[2].search_for("المجلس الثالث")) > 0
    doc.close()


def test_build_annotated_pdf_omits_session_label_when_unknown(two_page_pdf, pages_meta):
    comments = [{"page": 2, "position_in_page": 2, "text": "تعليق", "start": 1.0, "end": 3.0}]
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, comments, page_offset=0, style="inserted")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc[2].search_for("المجلس")) == 0
    doc.close()


# -- cover page --------------------------------------------------------------


def test_build_annotated_pdf_no_cover_page_when_no_book_info_given(two_page_pdf, pages_meta):
    # Backward-compatible default: callers/tests that pass none of the new
    # cover-page metadata get the exact same page count as before it existed.
    pdf_bytes = build_annotated_pdf(two_page_pdf, pages_meta, [], page_offset=0, style="bottom")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 2
    doc.close()


def test_build_annotated_pdf_adds_cover_page_when_book_title_given(two_page_pdf, pages_meta):
    pdf_bytes = build_annotated_pdf(
        two_page_pdf, pages_meta, [], page_offset=0, style="bottom",
        book_title_ar="كتاب تجريبي", author_ar="مؤلف", commentator_ar="فلان الفلاني",
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc.page_count == 3  # cover page + the 2 original pages, untouched
    assert len(doc[0].search_for("كتاب تجريبي")) > 0
    assert len(doc[0].search_for("مؤلف")) > 0
    assert len(doc[0].search_for("علّق عليه")) > 0
    assert doc[1].rect.height == 500  # original first page unchanged
    doc.close()


def test_build_annotated_pdf_cover_page_matches_book_page_size(two_page_pdf, pages_meta):
    pdf_bytes = build_annotated_pdf(
        two_page_pdf, pages_meta, [], page_offset=0, style="inserted", book_title_ar="كتاب",
    )
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    assert doc[0].rect.width == 400
    assert doc[0].rect.height == 500
    doc.close()
