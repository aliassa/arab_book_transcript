"""
Alignment / comment extraction for the reading-club pipeline.

Treats the book's (normalized) word sequence as the reference and the
transcript's (normalized) word sequence as the hypothesis, then runs a
word-level sequence alignment between them. Transcript words that don't
match nearby book text -- "insertions" relative to the book -- are the
reader's spoken comments rather than book text being read aloud.

Approach: difflib.SequenceMatcher over word tokens. It's an LCS-based
diff, which is the right tool here: the reader reads long verbatim runs
of book text (matching blocks) interrupted by digressions (comment
insertions), which is exactly the shape SequenceMatcher is built to find.
Levenshtein alignment would give the same block structure for this
insertion-dominated case, at higher cost, so SequenceMatcher is preferred
while sessions stay reading-club length.

A candidate comment is a run of consecutive transcript words that
SequenceMatcher tags as "insert" or "replace" (not present in the
matching book text), long enough to clear the minimum-length threshold.
Each candidate keeps its start/end word indices so the caller can map
back to transcript timestamps.
"""

import json
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from normalize import tokenize

MIN_COMMENT_WORDS = 5


@dataclass
class Comment:
    text: str
    word_start: int  # index into transcript word list
    word_end: int  # exclusive
    book_start: int  # index into book word list where the gap begins
    book_end: int  # index into book word list where the gap ends
    n_words: int = field(init=False)

    def __post_init__(self):
        self.n_words = self.word_end - self.word_start


def build_book_words(pages: list[dict]) -> tuple[list[str], list[int]]:
    """
    Tokenizes each page's text and returns the flattened word list
    alongside a parallel list of which page number each word came from,
    so a position in the word list can be mapped back to a page.
    """
    words: list[str] = []
    word_pages: list[int] = []
    for p in pages:
        for w in tokenize(p["text"]):
            words.append(w)
            word_pages.append(p["page_number"])
    return words, word_pages


def infer_page(word_pages: list[int], book_start: int, book_end: int) -> int | None:
    """
    A comment sits in a gap between matched book positions, so there's no
    book word "at" the gap to read a page off of directly. Use the page of
    the book word just before the gap (where the reader was when they
    digressed); fall back to the word just after (comment before any book
    text has matched yet, e.g. an opening remark) or the last known page.
    """
    if book_start > 0:
        return word_pages[book_start - 1]
    if book_end < len(word_pages):
        return word_pages[book_end]
    return word_pages[-1] if word_pages else None


def _edit_distance_le1(a: str, b: str) -> bool:
    """True if a and b differ by at most one character (substitution,
    insertion, or deletion) -- the shape of a typical single-character OCR
    misread (e.g. the Arabic dot-count confusion ت/ث)."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    shorter, longer = (a, b) if la < lb else (b, a)
    i = j = skipped = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
        else:
            skipped += 1
            if skipped > 1:
                return False
            j += 1
    return True


def _near_duplicate(w1: str, w2: str) -> bool:
    """Fuzzy word match tolerant of a single OCR-noise character, gated on
    length so short function words (e.g. 2-letter prepositions) don't
    false-match each other."""
    return len(w1) >= 3 and len(w2) >= 3 and _edit_distance_le1(w1, w2)


def extract_candidates(
    book_words: list[str],
    transcript_words: list[str],
    min_words: int = MIN_COMMENT_WORDS,
) -> list[Comment]:
    """
    Diff transcript_words (hypothesis) against book_words (reference).
    Returns runs of transcript-only words ("insert"/"replace" opcodes)
    at least min_words long.
    """
    matcher = SequenceMatcher(a=book_words, b=transcript_words, autojunk=False)
    candidates = []

    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag not in ("insert", "replace"):
            continue

        # A single OCR-garbled book word (e.g. "مررث" for "مررت") never
        # matches its correctly-spoken transcript counterpart, so the whole
        # unmatched run around it collapses into one replace block -- which
        # can drag a word or two of real book text in at the edges (typically
        # the reader re-reading a line to re-anchor after a digression).
        # Fuzzy-trim boundary words that are near-duplicates of the book
        # word they're paired against back out of the candidate.
        while a1 < a2 and b1 < b2 and _near_duplicate(book_words[a1], transcript_words[b1]):
            a1 += 1
            b1 += 1
        while a2 > a1 and b2 > b1 and _near_duplicate(book_words[a2 - 1], transcript_words[b2 - 1]):
            a2 -= 1
            b2 -= 1

        n_words = b2 - b1
        if n_words < min_words:
            continue
        candidates.append(
            Comment(
                text=" ".join(transcript_words[b1:b2]),
                word_start=b1,
                word_end=b2,
                book_start=a1,
                book_end=a2,
            )
        )

    return candidates


def extract_comments_from_transcript(
    pages: list[dict],
    transcript_segments: list[dict],
    min_words: int = MIN_COMMENT_WORDS,
) -> list[dict]:
    """
    High-level entry point: takes book pages (as produced by extract_book,
    each a {page_number, text, ...} dict) and Whisper segments (each with
    'text' and word-level 'words' timing), normalizes both, aligns, and
    returns comments annotated with start/end timestamps pulled from the
    original (non-normalized) word timings, plus the inferred book page.

    Assumes normalize.tokenize's word count for a segment's text lines
    up 1:1 with its 'words' timing list, which holds as long as both are
    derived from the same whitespace-delimited splitting -- true for
    Whisper's word-level output on Arabic.
    """
    book_words, book_word_pages = build_book_words(pages)

    # Flatten transcript into a single word list, keeping a parallel list
    # of (start, end) timestamps per normalized word.
    transcript_words: list[str] = []
    timestamps: list[tuple[float, float]] = []
    for seg in transcript_segments:
        seg_words = seg.get("words") or []
        for w in seg_words:
            norm = tokenize(w["word"])
            if not norm:
                continue
            # A Whisper "word" token occasionally contains >1 space-split
            # token after normalization (e.g. punctuation-glued forms);
            # assign all of them the same timing rather than dropping them.
            for tok in norm:
                transcript_words.append(tok)
                timestamps.append((w["start"], w["end"]))

    candidates = extract_candidates(book_words, transcript_words, min_words)

    comments = []
    for c in candidates:
        start_ts = timestamps[c.word_start][0]
        end_ts = timestamps[c.word_end - 1][1]
        comments.append(
            {
                "text": c.text,
                "n_words": c.n_words,
                "start": round(start_ts, 3),
                "end": round(end_ts, 3),
                "page": infer_page(book_word_pages, c.book_start, c.book_end),
            }
        )
    return comments


def main():
    if len(sys.argv) < 3:
        print(
            "Usage: python align.py <book_pages.json> <transcript.json> [page_number]"
        )
        sys.exit(1)

    book_path, transcript_path = sys.argv[1], sys.argv[2]
    page_number = int(sys.argv[3]) if len(sys.argv) > 3 else None

    with open(book_path, encoding="utf-8") as f:
        pages = json.load(f)
    with open(transcript_path, encoding="utf-8") as f:
        transcript = json.load(f)

    if page_number is not None:
        pages = [p for p in pages if p["page_number"] == page_number]

    comments = extract_comments_from_transcript(pages, transcript["segments"])

    print(f"Found {len(comments)} candidate comment(s):\n", file=sys.stderr)
    for c in comments:
        print(
            f"  [{c['start']:7.2f}-{c['end']:7.2f}] p.{c['page']} ({c['n_words']}w) {c['text']}",
            file=sys.stderr,
        )

    print(json.dumps(comments, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
