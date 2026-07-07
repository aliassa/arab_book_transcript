# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Extracts a reader's spoken *comments* from a recorded reading-club session
by diffing the audio transcript against the book's PDF text. Comments are
whatever the transcript says that the book doesn't — i.e. the reader's own
digressions, not the book text being read aloud. See `docs/project_brief.md`
for the original design rationale and `HOWTO.md` for user-facing usage.

## Commands

```bash
source .venv/bin/activate                      # always activate first
pip install -r requirements.txt                # after a fresh clone (see HOWTO.md)

python3 src/extract_book.py <book.pdf> [out.json]
python3 src/transcribe.py <audio> [out.json]    # downloads ~3GB model on first run
python3 src/align.py <book_pages.json> <transcript.json> [page_number] > comments.json

streamlit run src/app.py                        # UI: upload, run, review, export PDF

pytest                                           # unit tests (tests/), no fixtures needed
```

There is no linter or build step. `pytest` covers the pure-logic pieces
(`normalize.py`, `align.py`, `extract_book.py`'s `text_quality_score`,
`export_pdf.py`) with synthetic fixtures — it does not cover `transcribe.py`
(needs the real Whisper model) or `app.py` (Streamlit UI), which stay
verified by running the pipeline against real sample data (see `data/` and
`output/` for a validated sample pair: `sample_pages.pdf` /
`sample_audio_2.ogg` / `output/sample_audio_2_comments.json`).

`ffmpeg`/`ffprobe` and system `tesseract-ocr` + `tesseract-ocr-ara` are
required (not pip-installable) — see `HOWTO.md` prerequisites.

## Architecture

Three pipeline stages, each its own module, called from both a CLI
entrypoint (`if __name__ == "__main__"`) and `src/app.py` (Streamlit UI) —
**the UI never duplicates pipeline logic**, it only imports and calls the
same functions the CLI scripts use, plus UI-only concerns (audio clipping
for playback, PDF export, progress display). Any change to extraction/
transcription/alignment logic belongs in the stage module, not `app.py`.

1. **`extract_book.py`** — per-*page* (not per-file) router: try direct
   text extraction (pymupdf), score it (`text_quality_score`: fraction of
   plausible Arabic chars), fall back to OCR (pytesseract, `ara`, psm 6) if
   below `quality_threshold` (default 0.6). A single PDF can mix scanned
   and digital pages, hence per-page. Output: list of
   `{page_number, text, method, quality}`.

2. **`transcribe.py`** — faster-whisper, `language="ar"`,
   `word_timestamps=True` (the alignment stage needs per-word timing),
   `vad_filter=True`. `load_model()` is split out from `transcribe()`
   specifically so the UI can cache the model across runs
   (`st.cache_resource`) instead of reloading it every time.
   `clip_timestamps` (default `"0"`, i.e. the whole file) is
   faster-whisper's own `"start,end"` seconds-range syntax passed straight
   through — used by the UI's testing mode to transcribe just the first N
   minutes. Returned timestamps stay absolute against the *original* file
   regardless, so nothing downstream needs to know a clip was requested;
   this is a lighter alternative to HOWTO.md's manual ffmpeg-clip workflow
   since it needs no separate clipped audio file. Note: faster-whisper
   ignores `vad_filter` whenever a real clip range is given.

3. **`normalize.py`** — canonicalizes Arabic text before comparison: strips
   tashkeel/tatweel, unifies alef/hamza/ya/ta-marbuta variants, strips
   Arabic *and* ASCII punctuation, maps Arabic-Indic digits to ASCII.
   Note: Arabic punctuation (، ؛ ؟ etc.) sits inside the main Arabic
   Unicode block, so it must be stripped by an explicit punctuation regex
   *before* the generic "keep anything in the Arabic block" filter, or it
   leaks through as if it were a letter.

4. **`align.py`** — the core comment-extraction logic. Treats book words as
   the reference sequence and transcript words as the hypothesis, runs
   `difflib.SequenceMatcher` (word-level LCS diff), and treats any
   `insert`/`replace` run of transcript words ≥ `min_words` with no match
   in the book as a candidate comment. Also builds a page-tagged book word
   list (`build_book_words`) so each candidate's page number can be
   inferred (`infer_page`, via `_anchor_index`) from the nearest matched
   book position around it — comments have no book position of their own
   since they don't match anything, so the page is a nearest-neighbor
   guess, not exact. Each comment also carries `position_in_page`/
   `page_word_count` (word offset within that page, from the same anchor)
   and `context_before` (the `CONTEXT_WORDS` book words immediately
   preceding the gap, empty if the comment precedes any matched book text
   at all, e.g. an opening remark) so a reviewer isn't left guessing
   *where* on the page a comment landed.
   `extract_comments_from_transcript(pages, transcript_segments, ...)` is
   the single entry point both CLI and UI call.

5. **`export_pdf.py`** (UI-only) — renders reviewed comments to PDF via
   WeasyPrint, not reportlab: WeasyPrint shapes/reorders Arabic text
   correctly out of the box, where reportlab would need manual
   `arabic_reshaper` + `python-bidi` handling to avoid rendering broken
   disconnected glyphs.

### Known algorithmic limitations (not bugs — see `HOWTO.md`)

- **Boundary fuzziness**: if the reader repeats a book line before/after a
  digression (common, to re-anchor themselves), the diff can only match
  the book's single occurrence to *one* of the transcript's two, folding
  a few extra words into the comment span. `extract_candidates` fuzzy-trims
  boundary words that are a single-character edit away from the adjacent
  book word (`_near_duplicate`), which fixes the common case where this is
  caused by an OCR misread (e.g. "مررث" for "مررت") rather than genuine
  ambiguity — but doesn't help when the repeated line was transcribed or
  OCR'd cleanly and the ambiguity is real.
- **Disfluencies as false positives**: a stumble/self-correction while
  reading has the same "unmatched run of words" shape as a real comment.
- Both are why the UI has a manual review step (listen, edit text,
  uncheck false positives) rather than trusting the diff output directly
  — that review step *is* the current fix. A planned-but-not-built
  speech-rhythm signal (pace/pauses from word timestamps) is meant to
  eventually help disambiguate these automatically.

### UI-specific notes (`app.py`)

- Streamlit reruns the whole script on every widget interaction. Per-
  comment review state (`text_{i}`, `keep_{i}` widget keys) and the
  generated PDF persist in `st.session_state` across reruns *by design*
  — but must be explicitly cleared at the start of a new pipeline run, or
  a previous run's edits/checkboxes leak into the new one's results.
- Book/session picked via dropdowns over `data/<book>/<session>/`, not file
  upload (`list_book_dirs`, `list_session_dirs`, `find_pdf`, `find_audio`).
  `find_pdf(session_dir, book_dir)` prefers a session-specific PDF (e.g. a
  pre-clipped page range) if one exists in the session folder, else falls
  back to the book-level PDF — but a session-specific PDF is optional, not
  required: `align.py`'s diff only extracts unmatched *transcript* words, so
  handing it the full book is correct regardless of which pages that
  session actually covers.
- Extracted page text is cached next to the PDF as a hidden
  `.<pdf-stem>_pages_cache.json` (`get_book_pages`), keyed on the PDF's
  mtime + `quality_threshold`, since the same book PDF is reused across
  every session and OCR is the expensive part — without this, extraction
  would silently re-run (and re-OCR) on every single session.
- `book_info.json` in the book folder holds `{title_ar, author_ar}` for the
  Arabic PDF report (`export_pdf.py`); defaults to the book PDF's filename
  stem if absent, and gets written back to disk on every pipeline run so
  the user only types it once per book.
- Nothing produced by a run (other than the two caches above) is ever
  written to disk — results only exist in `st.session_state` for the life
  of the server process. If the user closes/restarts the server before
  using one of the download buttons (`comments.json`/`transcript.json`/
  `book_pages.json`/PDF), the run's output is unrecoverable and must be
  regenerated (extraction will be instant via cache; transcription won't).
- Processing-time estimates (`SPEED_MULTIPLIER` dict) are rough,
  hardware-dependent guesses, calibrated only for `large-v3` on this
  machine's CPU (no GPU) — don't treat them as measured for other model
  sizes. They also predate the book-extraction cache above, so the
  multiplier likely still bakes in OCR time that a cached-book run no
  longer pays.
- "Analyze only part of the audio (testing)" (Advanced options) transcribes
  just the first N minutes via `transcribe()`'s `clip_timestamps`, for
  quick iteration on a long session without waiting on a full run. The
  "Estimated time to result" metric reflects the clipped duration
  (`effective_duration`), not the full file, whenever this is active.
