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
    n_words: int = field(init=False)

    def __post_init__(self):
        self.n_words = self.word_end - self.word_start


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

    for tag, _a1, _a2, b1, b2 in matcher.get_opcodes():
        if tag not in ("insert", "replace"):
            continue
        n_words = b2 - b1
        if n_words < min_words:
            continue
        candidates.append(
            Comment(
                text=" ".join(transcript_words[b1:b2]),
                word_start=b1,
                word_end=b2,
            )
        )

    return candidates


def extract_comments_from_transcript(
    book_text: str,
    transcript_segments: list[dict],
    min_words: int = MIN_COMMENT_WORDS,
) -> list[dict]:
    """
    High-level entry point: takes raw book text and Whisper segments
    (each with 'text' and word-level 'words' timing), normalizes both,
    aligns, and returns comments annotated with start/end timestamps
    pulled from the original (non-normalized) word timings.

    Assumes normalize.tokenize's word count for a segment's text lines
    up 1:1 with its 'words' timing list, which holds as long as both are
    derived from the same whitespace-delimited splitting -- true for
    Whisper's word-level output on Arabic.
    """
    book_words = tokenize(book_text)

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
    book_text = "\n".join(p["text"] for p in pages)

    comments = extract_comments_from_transcript(book_text, transcript["segments"])

    print(f"Found {len(comments)} candidate comment(s):\n", file=sys.stderr)
    for c in comments:
        print(f"  [{c['start']:7.2f}-{c['end']:7.2f}] ({c['n_words']}w) {c['text']}", file=sys.stderr)

    print(json.dumps(comments, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
