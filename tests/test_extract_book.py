from extract_book import text_quality_score


def test_clean_arabic_scores_high():
    assert text_quality_score("نص عربي واضح وجميل.") == 1.0


def test_garbled_symbols_score_low():
    assert text_quality_score("garbled  symbols !@#$%") < 0.6


def test_empty_and_whitespace_score_zero():
    assert text_quality_score("") == 0.0
    assert text_quality_score("   ") == 0.0


def test_below_three_chars_scores_zero():
    # Too short to judge -- explicit short-circuit, not "0 good / 0 total".
    assert text_quality_score("ab") == 0.0


def test_ascii_letters_dont_count_as_good_but_digits_and_punctuation_do():
    # "Hello123" -> 8 non-space chars, only the 3 digits count as good.
    assert text_quality_score("Hello 123") == 3 / 8


def test_non_arabic_non_ascii_scores_zero():
    assert text_quality_score("好 好 好 好 test") == 0.0
