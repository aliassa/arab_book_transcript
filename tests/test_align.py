from align import (
    _anchor_index,
    _edit_distance_le1,
    _near_duplicate,
    build_book_words,
    extract_candidates,
    extract_comments_from_transcript,
    infer_page,
)


# -- _edit_distance_le1 / _near_duplicate -----------------------------------


def test_edit_distance_identical_strings():
    assert _edit_distance_le1("مررت", "مررت")


def test_edit_distance_single_substitution():
    assert _edit_distance_le1("مررت", "مررث")  # ت/ث dot-count confusion


def test_edit_distance_two_substitutions_not_close_enough():
    assert not _edit_distance_le1("مررت", "بززث")


def test_edit_distance_single_insertion_or_deletion():
    assert _edit_distance_le1("وبحكمته", "ويبحكمته")  # extra ي inserted
    assert _edit_distance_le1("ويبحكمته", "وبحكمته")  # symmetric


def test_edit_distance_length_gap_of_two_is_too_far():
    assert not _edit_distance_le1("كتاب", "كتابين")


def test_near_duplicate_requires_min_length_three():
    # Same 1-edit shape as the OCR-typo case, but too short to trust --
    # short function words shouldn't fuzzy-match each other.
    assert not _near_duplicate("من", "لن")


def test_near_duplicate_true_for_long_enough_words():
    assert _near_duplicate("مررت", "مررث")


# -- extract_candidates: baseline behavior -----------------------------------


def test_extract_candidates_respects_min_words():
    book_words = ["a", "b", "c", "d"]
    transcript_words = ["a", "b", "x", "y", "c", "d"]
    assert extract_candidates(book_words, transcript_words, min_words=5) == []

    candidates = extract_candidates(book_words, transcript_words, min_words=2)
    assert len(candidates) == 1
    assert candidates[0].text == "x y"
    assert (candidates[0].word_start, candidates[0].word_end) == (2, 4)


def test_extract_candidates_no_book_at_all_is_one_insert():
    book_words = []
    transcript_words = ["one", "two", "three", "four", "five"]
    candidates = extract_candidates(book_words, transcript_words, min_words=5)
    assert len(candidates) == 1
    assert candidates[0].text == "one two three four five"


# -- extract_candidates: OCR-typo boundary fuzzy trim ------------------------


def test_trims_ocr_typo_word_off_the_tail_of_a_comment():
    # "مررث" is an OCR misread of "مررت" -- the book word never matches
    # either spoken occurrence, so the whole gap (comment + the correctly
    # -read repeat) would otherwise collapse into one replace block.
    book_words = ["كلمه1", "كلمه2", "مررث", "كلمه5", "كلمه6"]
    transcript_words = [
        "كلمه1", "كلمه2",
        "c1", "c2", "c3", "c4", "c5",
        "مررت",
        "كلمه5", "كلمه6",
    ]
    candidates = extract_candidates(book_words, transcript_words, min_words=5)
    assert len(candidates) == 1
    assert candidates[0].text == "c1 c2 c3 c4 c5"
    assert "مررت" not in candidates[0].text
    assert candidates[0].word_end == 7  # excludes the "مررت" at index 7


def test_trims_ocr_typo_word_off_the_head_of_a_comment():
    book_words = ["كلمه1", "كلمه2", "مقدمة", "كلمه5", "كلمه6"]
    transcript_words = [
        "كلمه1", "كلمه2",
        "مقدمه",  # ta-marbuta/ha OCR-style mismatch vs book's "مقدمة"
        "c1", "c2", "c3", "c4", "c5",
        "كلمه5", "كلمه6",
    ]
    candidates = extract_candidates(book_words, transcript_words, min_words=5)
    assert len(candidates) == 1
    assert candidates[0].text == "c1 c2 c3 c4 c5"
    assert candidates[0].word_start == 3  # excludes "مقدمه" at index 2


def test_trim_can_drop_a_candidate_below_min_words():
    book_words = ["كلمه1", "كلمه2", "مررث", "كلمه5", "كلمه6"]
    transcript_words = [
        "كلمه1", "كلمه2",
        "c1", "c2", "c3", "c4",
        "مررت",
        "كلمه5", "كلمه6",
    ]
    # 5 transcript words before trim (meets min_words=5), only 4 after.
    assert extract_candidates(book_words, transcript_words, min_words=5) == []


# -- build_book_words / infer_page -------------------------------------------


def test_build_book_words_tracks_page_per_word():
    pages = [
        {"page_number": 1, "text": "بسم الله"},
        {"page_number": 2, "text": "الرحمن الرحيم"},
    ]
    words, word_pages = build_book_words(pages)
    assert words == ["بسم", "الله", "الرحمن", "الرحيم"]
    assert word_pages == [1, 1, 2, 2]


def test_infer_page_uses_word_before_gap():
    word_pages = [1, 1, 2, 2, 3]
    assert infer_page(word_pages, book_start=3, book_end=3) == 2


def test_infer_page_falls_back_to_word_after_gap_at_start():
    word_pages = [1, 1, 2, 2, 3]
    assert infer_page(word_pages, book_start=0, book_end=0) == 1


def test_infer_page_falls_back_to_last_page_when_gap_spans_whole_book():
    word_pages = [1, 1, 2, 2, 3]
    assert infer_page(word_pages, book_start=0, book_end=len(word_pages)) == 3


def test_infer_page_none_when_no_book_words():
    assert infer_page([], book_start=0, book_end=0) is None


def test_anchor_index_matches_infer_page_source_position():
    word_pages = [1, 1, 2, 2, 3]
    assert _anchor_index(word_pages, book_start=3, book_end=3) == 2
    assert word_pages[_anchor_index(word_pages, book_start=3, book_end=3)] == infer_page(
        word_pages, book_start=3, book_end=3
    )


# -- extract_comments_from_transcript: end to end ----------------------------


def _word(token, start, end):
    return {"word": token, "start": start, "end": end}


def test_extract_comments_from_transcript_end_to_end():
    pages = [{"page_number": 1, "text": "بسم الله الرحمن الرحيم"}]
    segments = [
        {
            "words": [
                _word("بسم", 0.0, 0.5),
                _word("الله", 0.5, 1.0),
                _word("هذا", 1.0, 1.5),
                _word("تعليق", 1.5, 2.0),
                _word("شخصي", 2.0, 2.5),
                _word("طويل", 2.5, 3.0),
                _word("جدا", 3.0, 3.5),
                _word("الرحمن", 3.5, 4.0),
                _word("الرحيم", 4.0, 4.5),
            ]
        }
    ]

    comments = extract_comments_from_transcript(pages, segments, min_words=5)

    assert len(comments) == 1
    c = comments[0]
    assert c["text"] == "هذا تعليق شخصي طويل جدا"
    assert c["n_words"] == 5
    assert c["start"] == 1.0
    assert c["end"] == 3.5
    assert c["page"] == 1
    # "بسم الله" (2 words) precede the gap on a 4-word page.
    assert c["position_in_page"] == 2
    assert c["page_word_count"] == 4
    assert c["context_before"] == "بسم الله"


def test_extract_comments_from_transcript_no_context_before_opening_remark():
    # Comment happens before any book text has been read at all -- there is
    # nothing to show as "text before it".
    pages = [{"page_number": 1, "text": "كلمه1 كلمه2 كلمه3"}]
    segments = [
        {
            "words": [
                _word("c1", 0.0, 0.5),
                _word("c2", 0.5, 1.0),
                _word("c3", 1.0, 1.5),
                _word("c4", 1.5, 2.0),
                _word("c5", 2.0, 2.5),
                _word("كلمه1", 2.5, 3.0),
                _word("كلمه2", 3.0, 3.5),
                _word("كلمه3", 3.5, 4.0),
            ]
        }
    ]

    comments = extract_comments_from_transcript(pages, segments, min_words=5)

    assert len(comments) == 1
    c = comments[0]
    assert c["page"] == 1
    assert c["position_in_page"] == 1
    assert c["page_word_count"] == 3
    assert c["context_before"] == ""


def test_extract_comments_from_transcript_position_in_page_is_per_page_not_global():
    # The comment falls on page 2, which starts fresh at word 1 -- not
    # counting page 1's words too.
    pages = [
        {"page_number": 1, "text": "كلمه1 كلمه2 كلمه3"},
        {"page_number": 2, "text": "كلمه4 كلمه5"},
    ]
    segments = [
        {
            "words": [
                _word("كلمه1", 0.0, 0.5),
                _word("كلمه2", 0.5, 1.0),
                _word("كلمه3", 1.0, 1.5),
                _word("كلمه4", 1.5, 2.0),
                _word("c1", 2.0, 2.5),
                _word("c2", 2.5, 3.0),
                _word("c3", 3.0, 3.5),
                _word("c4", 3.5, 4.0),
                _word("c5", 4.0, 4.5),
                _word("كلمه5", 4.5, 5.0),
            ]
        }
    ]

    comments = extract_comments_from_transcript(pages, segments, min_words=5)

    assert len(comments) == 1
    c = comments[0]
    assert c["page"] == 2
    assert c["position_in_page"] == 1  # "كلمه4" is the 1st word of page 2
    assert c["page_word_count"] == 2
    assert c["context_before"] == "كلمه1 كلمه2 كلمه3 كلمه4"
