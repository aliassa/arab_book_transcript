from normalize import normalize_text, tokenize


def test_strips_tashkeel_and_tatweel():
    assert normalize_text("أَلسَّلاَمُ عَلَيْكُمْ") == "السلام عليكم"


def test_unifies_alef_variants():
    assert normalize_text("إن الله أكبر آمين ٱرحم") == "ان الله اكبر امين ارحم"


def test_unifies_ta_marbuta_to_ha():
    assert normalize_text("مكتبة كبيرة") == "مكتبه كبيره"


def test_unifies_ya_and_alef_maksura():
    assert normalize_text("هدى وفتى") == "هدي وفتي"


def test_unifies_hamza_carriers():
    # ئ -> ي, ؤ -> و; bare hamza ء is left as-is (in-block, not remapped)
    assert normalize_text("سئل عن مؤمن وشيء") == "سيل عن مومن وشيء"


def test_maps_arabic_indic_digits_to_ascii():
    assert normalize_text("رقم ١٢٣ صفحة") == "رقم 123 صفحه"


def test_strips_arabic_and_ascii_punctuation():
    assert normalize_text("مرحباً، كيف حالك؟ أهلاً!") == "مرحبا كيف حالك اهلا"


def test_collapses_whitespace():
    assert normalize_text("الكتاب   يحوي    مسافات") == "الكتاب يحوي مسافات"


def test_passes_through_latin_and_ascii_digits():
    assert normalize_text("hello world 123") == "hello world 123"


def test_empty_string():
    assert normalize_text("") == ""
    assert tokenize("") == []


def test_tokenize_splits_normalized_text_on_whitespace():
    assert tokenize("أَلسَّلاَمُ عَلَيْكُمْ وَرَحْمَةُ اللهِ") == [
        "السلام",
        "عليكم",
        "ورحمه",
        "الله",
    ]
