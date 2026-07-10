from export_pdf import build_pdf, cover_page_html, format_ts, session_label_ar


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


def test_build_pdf_with_commentator_and_club_image():
    pdf = build_pdf(
        [{"text": "تعليق تجريبي", "page": 1, "start": 1.5, "end": 4.2}],
        book_title_ar="كتاب",
        commentator_ar="فلان الفلاني",
        club_image_bytes=b"\xff\xd8\xff\xe0fakejpegdata",
    )
    assert pdf[:5] == b"%PDF-"


def test_cover_page_html_includes_commentator_label():
    body = cover_page_html(book_title_ar="كتاب", commentator_ar="فلان الفلاني")
    assert "علّق عليه: فلان الفلاني" in body


def test_cover_page_html_never_shows_a_session_number():
    # The cover fronts the whole book's comments, not one session's -- there
    # is no session_number parameter to pass, and no ordinal label to show.
    body = cover_page_html(book_title_ar="كتاب")
    assert "المجلس" not in body


def test_cover_page_html_omits_image_when_none_given():
    body = cover_page_html(book_title_ar="كتاب")
    assert "<img" not in body


def test_cover_page_html_social_icons_link_out_without_showing_raw_urls():
    body = cover_page_html(
        telegram_icon_bytes=b"\x89PNGfaketelegram",
        facebook_icon_bytes=b"\x89PNGfakefacebook",
    )
    assert 'href="https://t.me/NadiAlQuraaAlArab"' in body
    assert 'href="https://www.facebook.com/groups/1752014606211247/"' in body
    # Icon + Arabic label as the visible link text.
    assert "رابط تيليغرام" in body
    assert "رابط الفايسبوك" in body
    # For printed copies, a footer line shows the tidied short forms; the
    # full scheme'd URL still only lives in the href.
    assert ">t.me/NadiAlQuraaAlArab<" in body
    assert ">facebook.com/groups/1752014606211247<" in body
    assert "https://t.me/NadiAlQuraaAlArab<" not in body
    assert ">https://t.me" not in body


def test_cover_page_html_omits_social_row_when_no_icons_given():
    body = cover_page_html(book_title_ar="كتاب")
    assert "cover-social" not in body
