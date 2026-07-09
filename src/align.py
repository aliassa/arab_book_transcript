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

from normalize import tokenize, tokenize_display

MIN_COMMENT_WORDS = 5

# How many book words immediately preceding a comment's gap to surface as
# orientation context (so a reviewer doesn't have to reread the whole page
# to find where in it the reader digressed).
CONTEXT_WORDS = 12

# SequenceMatcher searches the *whole* book (tens of thousands of words),
# so a genuine comment that happens to use a common word/short phrase --
# "الله", "من", even a 4-word phrase like "لا اله الا الله" -- can spuriously
# "match" an unrelated occurrence of it elsewhere in the book, splitting one
# continuous comment into fragments each mis-anchored to whatever random
# page that coincidence landed on. An "equal" run this short, sandwiched
# between two insert/replace runs, is noise rather than a real re-anchor
# point (see _merge_short_gaps) and gets folded back into the comment.
MAX_NOISE_GAP_WORDS = 4


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


def build_book_words(pages: list[dict]) -> tuple[list[str], list[str], list[int]]:
    """
    Tokenizes each page's text and returns the flattened word list
    (letter-unified, for matching) alongside a parallel display-form word
    list (original alef/hamza/ya/ta-marbuta spellings, for output text) and
    a parallel list of which page number each word came from, so a
    position in the word list can be mapped back to a page. `tokenize` and
    `tokenize_display` split identically (see normalize.py), so the two
    word lists stay index-aligned word-for-word.
    """
    words: list[str] = []
    display_words: list[str] = []
    word_pages: list[int] = []
    for p in pages:
        for w, d in zip(tokenize(p["text"]), tokenize_display(p["text"])):
            words.append(w)
            display_words.append(d)
            word_pages.append(p["page_number"])
    return words, display_words, word_pages


def _anchor_index(word_pages: list[int], book_start: int, book_end: int) -> int | None:
    """
    A comment sits in a gap between matched book positions, so there's no
    book word "at" the gap to anchor it to directly. Anchor to the book word
    just before the gap (where the reader was when they digressed); fall
    back to the word just after (comment before any book text has matched
    yet, e.g. an opening remark), or the last known word, or None if the
    book has no words at all.
    """
    if book_start > 0:
        return book_start - 1
    if book_end < len(word_pages):
        return book_end
    return len(word_pages) - 1 if word_pages else None


def infer_page(word_pages: list[int], book_start: int, book_end: int) -> int | None:
    idx = _anchor_index(word_pages, book_start, book_end)
    return word_pages[idx] if idx is not None else None


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


def _merge_short_gaps(
    opcodes: list[tuple[str, int, int, int, int]],
    max_gap_words: int = MAX_NOISE_GAP_WORDS,
) -> list[tuple[str, int, int, int, int]]:
    """
    Fold spurious short matches out of a run of opcodes: a chain of "equal"
    runs of at most `max_gap_words` transcript words each (and any "delete"
    runs alongside them, which never break a span since they consume no
    transcript words) gets absorbed into the surrounding insert/replace
    span, provided the chain eventually leads back to another insert/
    replace run rather than a genuine (longer) equal match or the end of
    the opcode list -- i.e. it's actually sandwiched, not just a trailing
    coincidence. See MAX_NOISE_GAP_WORDS for why this happens. Opcodes are
    contiguous in both a- and b-space (each one picks up where the
    previous left off), so bridging a gap is just extending the run's
    a2/b2 to the next committed opcode's a2/b2 -- the skipped opcodes'
    own ranges are implicitly included.
    """
    merged = []
    i, n = 0, len(opcodes)
    while i < n:
        tag, a1, a2, b1, b2 = opcodes[i]
        if tag not in ("insert", "replace"):
            merged.append(opcodes[i])
            i += 1
            continue

        cur_a1, cur_a2, cur_b1, cur_b2 = a1, a2, b1, b2
        j = i + 1
        # Tentatively-skipped delete/short-equal opcodes since the last
        # committed insert/replace -- only actually noise if another
        # insert/replace follows to "sandwich" them; a chain of several
        # (e.g. a few short coincidental word matches each separated by a
        # run of skipped book text) is still noise as long as it eventually
        # leads back to real transcript content, not just the first hop.
        skipped: list[tuple[str, int, int, int, int]] = []
        while j < n:
            jtag, ja1, ja2, jb1, jb2 = opcodes[j]
            if jtag == "delete" or (jtag == "equal" and (jb2 - jb1) <= max_gap_words):
                skipped.append(opcodes[j])
                j += 1
                continue
            if jtag in ("insert", "replace"):
                cur_a2, cur_b2 = ja2, jb2
                skipped = []
                j += 1
                continue
            break  # equal run too long to be coincidental: a genuine anchor

        out_tag = "insert" if cur_a1 == cur_a2 else "replace"
        merged.append((out_tag, cur_a1, cur_a2, cur_b1, cur_b2))
        # Trailing skipped opcodes never got absorbed (nothing sandwiched
        # them) -- keep them as-is rather than silently dropping them.
        merged.extend(skipped)
        i = j

    return merged


def extract_candidates(
    book_words: list[str],
    transcript_words: list[str],
    min_words: int = MIN_COMMENT_WORDS,
    display_words: list[str] | None = None,
) -> list[Comment]:
    """
    Diff transcript_words (hypothesis) against book_words (reference).
    Returns runs of transcript-only words ("insert"/"replace" opcodes)
    at least min_words long.

    Matching runs on `transcript_words`/`book_words`, which have their
    alef/hamza/ya/ta-marbuta variants unified (normalize.tokenize) since
    OCR and Whisper disagree on those constantly -- but that same
    unification makes for wrong-looking output text. `display_words`, if
    given, is a word-for-word-aligned list with original letter forms
    (normalize.tokenize_display) used to build each Comment's `text`
    instead; it defaults to `transcript_words` for callers that don't
    need the distinction (e.g. tests).
    """
    display = display_words if display_words is not None else transcript_words
    # autojunk=True (the difflib default): down-weights single transcript
    # words that recur so often they're not meaningful match anchors --
    # complementary to _merge_short_gaps below, which catches the same
    # problem for book-side coincidences and multi-word phrases autojunk
    # doesn't cover.
    matcher = SequenceMatcher(a=book_words, b=transcript_words, autojunk=True)
    opcodes = _merge_short_gaps(matcher.get_opcodes())
    candidates = []

    for tag, a1, a2, b1, b2 in opcodes:
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
                text=" ".join(display[b1:b2]),
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
    book_words, book_display_words, book_word_pages = build_book_words(pages)

    # First/last-exclusive book-word index of each page, so a comment's
    # position within its page can be reported (not just the page number).
    # Pages are contiguous runs in book_word_pages since build_book_words
    # appends page-by-page, so one pass is enough.
    page_spans: dict[int, list[int]] = {}
    for idx, pg in enumerate(book_word_pages):
        span = page_spans.setdefault(pg, [idx, idx + 1])
        span[1] = idx + 1

    # Flatten transcript into a single word list, keeping a parallel list
    # of (start, end) timestamps per normalized word, plus a parallel
    # display-form word list (original letter forms) for output text.
    transcript_words: list[str] = []
    transcript_display_words: list[str] = []
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
            for tok, disp in zip(norm, tokenize_display(w["word"])):
                transcript_words.append(tok)
                transcript_display_words.append(disp)
                timestamps.append((w["start"], w["end"]))

    candidates = extract_candidates(
        book_words, transcript_words, min_words, display_words=transcript_display_words
    )

    comments = []
    for c in candidates:
        start_ts = timestamps[c.word_start][0]
        end_ts = timestamps[c.word_end - 1][1]

        anchor = _anchor_index(book_word_pages, c.book_start, c.book_end)
        page = book_word_pages[anchor] if anchor is not None else None

        position_in_page = None
        page_word_count = None
        if anchor is not None:
            span_start, span_end = page_spans[page]
            position_in_page = anchor - span_start + 1  # 1-indexed
            page_word_count = span_end - span_start

        # Text read right before the gap, for orientation -- distinct from
        # the anchor above, which can point *after* the gap (opening-remark
        # case) where there is nothing before to show.
        context_before = ""
        if c.book_start > 0:
            context_start = max(0, c.book_start - CONTEXT_WORDS)
            context_before = " ".join(book_display_words[context_start:c.book_start])

        comments.append(
            {
                "text": c.text,
                "n_words": c.n_words,
                "start": round(start_ts, 3),
                "end": round(end_ts, 3),
                "page": page,
                "position_in_page": position_in_page,
                "page_word_count": page_word_count,
                "context_before": context_before,
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
