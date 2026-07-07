from export_pdf import build_pdf, format_ts, session_label_ar


def test_format_ts():
    assert format_ts(0) == "0:00"
    assert format_ts(65) == "1:05"
    assert format_ts(3661) == "61:01"


def test_session_label_known_ordinal():
    assert session_label_ar(3) == "المجلس الثالث"


def test_session_label_unknown_ordinal_falls_back_to_number():
    assert session_label_ar(11) == "المجلس رقم 11"


def test_session_label_none_is_empty():
    assert session_label_ar(None) == ""


def test_build_pdf_smoke():
    pdf = build_pdf(
        [{"text": "تعليق تجريبي", "page": 1, "start": 1.5, "end": 4.2}],
        book_title_ar="كتاب",
        author_ar="مؤلف",
        session_number=2,
    )
    assert pdf[:5] == b"%PDF-"


def test_build_pdf_with_no_comments_and_no_metadata():
    pdf = build_pdf([])
    assert pdf[:5] == b"%PDF-"


def test_build_pdf_with_page_position_and_context():
    pdf = build_pdf(
        [
            {
                "text": "تعليق تجريبي",
                "page": 3,
                "start": 1.5,
                "end": 4.2,
                "position_in_page": 42,
                "page_word_count": 130,
                "context_before": "نص من الصفحة قبل التعليق",
            }
        ]
    )
    assert pdf[:5] == b"%PDF-"


def test_build_pdf_escapes_html_special_characters():
    # Comment text/context is user-edited free text (or book_title/author
    # input) -- must not be able to break the report's HTML structure.
    pdf = build_pdf(
        [{"text": "<script>a & b</script>", "page": 1, "start": 0, "end": 1}],
        book_title_ar="<b>كتاب</b>",
    )
    assert pdf[:5] == b"%PDF-"
