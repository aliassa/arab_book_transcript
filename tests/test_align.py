from align import (
    _anchor_index,
    _edit_distance_le1,
    _merge_short_gaps,
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


# -- _merge_short_gaps ---------------------------------------------------


def test_merge_short_gaps_bridges_single_short_equal():
    # A lone coincidental word match ("equal", 1 word) sandwiched between
    # two comment fragments gets folded into one continuous span.
    opcodes = [
        ("replace", 0, 2, 0, 3),
        ("equal", 2, 3, 3, 4),
        ("replace", 3, 5, 4, 7),
    ]
    assert _merge_short_gaps(opcodes) == [("replace", 0, 5, 0, 7)]


def test_merge_short_gaps_bridges_chain_with_deletes():
    # Real-world shape (from an actual session): several short coincidental
    # matches, each separated by a run of unrelated book text that's simply
    # skipped ("delete"), before finally reaching real transcript content
    # again. The whole chain is still noise, not just the first hop.
    opcodes = [
        ("replace", 0, 2, 0, 3),
        ("equal", 2, 3, 3, 4),  # short match #1
        ("delete", 3, 20, 4, 4),  # unrelated book text skipped
        ("equal", 20, 21, 4, 5),  # short match #2
        ("delete", 21, 40, 5, 5),
        ("equal", 40, 42, 5, 7),  # short match #3 (2 words)
        ("replace", 42, 45, 7, 10),
    ]
    assert _merge_short_gaps(opcodes) == [("replace", 0, 45, 0, 10)]


def test_merge_short_gaps_stops_at_a_genuine_long_equal():
    # An "equal" run longer than the noise threshold is a real anchor --
    # it must survive as its own opcode and split the two comments either
    # side of it, not get merged away.
    opcodes = [
        ("replace", 0, 2, 0, 3),
        ("equal", 2, 3, 3, 4),  # short: merges into comment 1
        ("replace", 3, 5, 4, 7),
        ("equal", 5, 17, 7, 19),  # 12 words -- a real match
        ("replace", 17, 19, 19, 22),
    ]
    assert _merge_short_gaps(opcodes) == [
        ("replace", 0, 5, 0, 7),
        ("equal", 5, 17, 7, 19),
        ("replace", 17, 19, 19, 22),
    ]


def test_merge_short_gaps_does_not_absorb_trailing_short_equal_with_no_followup():
    # A short "equal" with nothing after it isn't sandwiched by anything --
    # it must be left alone rather than silently swallowed.
    opcodes = [
        ("replace", 0, 2, 0, 3),
        ("equal", 2, 3, 3, 4),
    ]
    assert _merge_short_gaps(opcodes) == [
        ("replace", 0, 2, 0, 3),
        ("equal", 2, 3, 3, 4),
    ]


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
    words, display_words, word_pages = build_book_words(pages)
    assert words == ["بسم", "الله", "الرحمن", "الرحيم"]
    assert display_words == ["بسم", "الله", "الرحمن", "الرحيم"]
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


def test_extract_comments_preserves_original_letter_forms():
    # Book and transcript both use letter forms that tokenize() (matching)
    # would collapse -- إ->ا and ى->ي -- but the comment text and context
    # shown to a reviewer must keep the original (correct) spelling, not
    # the collapsed one used internally for matching.
    pages = [{"page_number": 1, "text": "إن هذا الكتاب مفيد جدى"}]
    segments = [
        {
            "words": [
                _word("إن", 0.0, 0.5),
                _word("هذا", 0.5, 1.0),
                _word("الكتاب", 1.0, 1.5),
                _word("أعتقد", 1.5, 2.0),
                _word("أن", 2.0, 2.5),
                _word("إخراجه", 2.5, 3.0),
                _word("كان", 3.0, 3.5),
                _word("رائعاً", 3.5, 4.0),
                _word("مفيد", 4.0, 4.5),
                _word("جدى", 4.5, 5.0),
            ]
        }
    ]

    comments = extract_comments_from_transcript(pages, segments, min_words=5)

    assert len(comments) == 1
    c = comments[0]
    # Original hamza/alef-maksura forms (أ, إ, ى) survive in the output --
    # tokenize() would have rewritten all of these to ا/ي for matching.
    assert c["text"] == "أعتقد أن إخراجه كان رائعا"
    assert c["context_before"] == "إن هذا الكتاب"


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
