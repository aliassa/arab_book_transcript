"""
Arabic text normalization for the reading-club pipeline.

Book text (direct-extracted or OCR'd) and Whisper transcripts represent
the "same" words very differently at the character level: tashkeel
presence/absence, alef/hamza variants, ta-marbuta vs ha, elongation
(tatweel), punctuation, digit forms. None of that variation is meaningful
for the word-alignment stage -- it just needs to compare the underlying
word sequence. This module reduces text to that comparable form and
tokenizes it.
"""

import re
import unicodedata

# Tashkeel (diacritics) + tatweel (kashida elongation character).
TASHKEEL_RE = re.compile(r"[ً-ٰٟۖ-ۭـ]")

# Arabic-Indic and Extended Arabic-Indic digits -> ASCII digits.
DIGIT_MAP = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

# Character-level unifications: alef variants, hamza carriers, ya/alef-maksura,
# ta-marbuta/ha.
CHAR_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ئ": "ي",
        "ؤ": "و",
        "ة": "ه",
    }
)

# Arabic punctuation (comma, semicolon, question mark, etc.) -- these sit
# inside the Arabic Unicode block, so they must be stripped explicitly
# before the "keep anything in-block" rule below, which would otherwise
# treat them as letters.
ARABIC_PUNCT_RE = re.compile(r"[،؛؟٪﴾﴿ﷺ«»]")

# Anything that isn't an Arabic letter, ASCII digit, or whitespace is
# considered punctuation/noise for alignment purposes and stripped.
KEEP_RE = re.compile(r"[^؀-ۿݐ-ݿﭐ-﻿a-zA-Z0-9\s]")


def normalize_text(text: str) -> str:
    """Canonicalize Arabic text for cross-source comparison (not for display)."""
    text = unicodedata.normalize("NFKC", text)
    text = TASHKEEL_RE.sub("", text)
    text = text.translate(DIGIT_MAP)
    text = text.translate(CHAR_MAP)
    text = ARABIC_PUNCT_RE.sub(" ", text)
    text = KEEP_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text: str) -> list[str]:
    """Normalize then split into words -- the unit alignment operates on."""
    return normalize_text(text).split()


if __name__ == "__main__":
    import sys

    sample = sys.argv[1] if len(sys.argv) > 1 else "أَلسَّلاَمُ عَلَيْكُمْ وَرَحْمَةُ اللهِ"
    print("input :", sample)
    print("norm  :", normalize_text(sample))
    print("tokens:", tokenize(sample))
