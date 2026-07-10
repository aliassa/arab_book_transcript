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
`export_pdf.py`, `export_annotated_pdf.py`) with synthetic fixtures —
`export_annotated_pdf.py`'s own PDF-building smoke tests use a tiny
in-memory PDF with real selectable text (`"direct"` method) specifically
to avoid needing Tesseract/OCR in the test suite — it does not cover `transcribe.py`
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
   leaks through as if it were a letter. The alef/hamza/ya unification step
   is essential for matching (OCR and Whisper disagree on these letters
   constantly) but produces wrong-looking *output* text (`أ/إ/آ`→`ا`,
   `ى/ئ`→`ي`, etc.), so `tokenize_display()` runs the same tokenization
   minus that one step, staying word-for-word aligned with `tokenize()`'s
   output (the other steps are 1:1 substitutions/removals that don't shift
   word boundaries) — `align.py` uses `tokenize()` for matching and
   `tokenize_display()` to build the text a reviewer actually reads.

4. **`align.py`** — the core comment-extraction logic. Treats book words as
   the reference sequence and transcript words as the hypothesis, runs
   `difflib.SequenceMatcher` (word-level LCS diff), and treats any
   `insert`/`replace` run of transcript words ≥ `min_words` with no match
   in the book as a candidate comment. `SequenceMatcher` searches the
   *entire* book (tens of thousands of words), so a comment that happens to
   use a common word or short phrase can spuriously "match" an unrelated
   occurrence of it elsewhere in the book, splitting one continuous comment
   into fragments each mis-anchored to whatever random page the coincidence
   landed on — `_merge_short_gaps` folds `equal` runs of at most
   `MAX_NOISE_GAP_WORDS` back into the surrounding candidate whenever the
   chain (short matches and skipped/`delete` book text) eventually leads
   back to real transcript content, i.e. it's actually sandwiched rather
   than a genuine anchor; `autojunk=True` (difflib's default) is
   complementary insurance for single words that recur very often in the
   transcript itself. Also builds a page-tagged book word
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
   disconnected glyphs. `cover_page_html`/`COVER_TITLE_CSS` build a
   club-branded cover page shared by both this module's `build_pdf` and
   `export_annotated_pdf.build_annotated_pdf` (via `_insert_cover_page`) so
   the two exports' front matter can't drift apart: club name (set in Noto
   Kufi Arabic, colored/gold-flourished to match `assets/club_image.jpeg`'s
   own calligraphy — sampled from the image's own palette — rather than
   sitting as plain black text above an already-ornate logo), that image,
   book title/author, the session's commentator (`علّق عليه: <name>`,
   distinct from the book's own author), and Telegram/Facebook links as
   icon + Arabic-label `<a>` tags ("رابط تيليغرام"/"رابط الفايسبوك", with
   `assets/telegram_icon.png`/`facebook_icon.png`) so the export doesn't
   print out raw URLs — the URL lives only in the `href`. Deliberately carries no session/
   majlis number (per explicit request — a book's cover shouldn't imply
   the comments behind it belong to one particular session), and `build_pdf`
   no longer repeats the club name/metadata as a second page before the
   entries either — the comment count and entries flow directly below the
   cover instead of behind a forced page break, which used to just be a
   near-empty duplicate page.

6. **`export_annotated_pdf.py`** (UI-only) — a reviewer found a separate
   summary document impractical to actually use while reading, so this
   overlays reviewed comments directly onto the book's own PDF pages
   instead: a small numbered marker at each comment's anchor word (the
   same word `align.py` already anchors it to via `position_in_page` —
   this makes that anchor *visible*, not any more accurate than what
   `align.py` computed), plus the comment text either appended in new
   whitespace at the bottom of that page (`style="bottom"`, text gets
   truncated past `BOTTOM_TRUNCATE_CHARS` since a page-bottom strip is
   tight) or on a new page inserted right after it (`style="inserted"`,
   no truncation, at the cost of the book's page count growing). Comments
   are numbered sequentially across the whole book, not restarted per
   page. `_insert_cover_page` prepends the same club-branded cover as
   `export_pdf.py` (reusing `cover_page_html`/`COVER_TITLE_CSS` directly
   rather than a second copy) as page one of the output, rendered at the
   book's own page size instead of a fixed A4 report page, and only when
   `build_annotated_pdf` is given at least one piece of cover metadata —
   callers/tests that pass none of it get the exact same page count as
   before the cover existed. Placing the marker needs each anchor word's
   *pixel* position,
   which `extract_book.py` never captures (it only keeps flat page text)
   — `_page_word_boxes` re-derives word boxes per commented page on
   export (PyMuPDF `get_text("words")` for `"direct"` pages, Tesseract
   `image_to_data` for OCR'd ones), an approximation of where
   `position_in_page` points since Tesseract's word segmentation here
   isn't guaranteed identical to the `image_to_string` call
   `extract_book.py` used to build the page text that index was computed
   against. OCR'd word boxes are persisted to a hidden
   `.<pdf-stem>_word_boxes_cache.json` next to the book PDF (keyed on the
   PDF's mtime + the OCR dpi, same pattern and rationale as `app.py`'s
   page-text cache): for a fully-scanned book they're ~1s of Tesseract
   per commented page and were re-derived on *every* render click —
   after the first render, re-renders and style switches skip OCR
   entirely. Comment text is laid out as one flowing HTML document per
   commented page (`_entries_html`: header + per-comment meta line +
   body), rendered in a single WeasyPrint call and paginated naturally —
   for `"bottom"` it's rendered on one tall page first, measured, and the
   book page grown by exactly the content's height. This replaced a
   fixed-box-per-comment scheme whose per-character height *estimate*
   over-reserved ~3× the real height (every comment trailed a block of
   dead whitespace, inflating a real book's export by ~120 mostly-empty
   pages) and whose "doesn't fit → bump whole comment to a fresh page"
   rule stranded the page header alone on a near-empty page whenever the
   first comment was long — flow layout has neither failure mode, and
   cut WeasyPrint calls from 3 per comment to 1 per commented page
   (~1,180 → ~75 on a real book). The rendered pages are embedded with
   `show_pdf_page`, rather than drawn via PyMuPDF's own `insert_htmlbox`:
   both shape Arabic correctly on screen, but `insert_htmlbox`'s output
   embeds a broken text layer for it — visually right, but
   `page.search_for()` finds nothing even for text plainly visible on the
   page (confirmed empirically after a whole-book export looked fine on
   screen but wasn't searchable/copyable) — while WeasyPrint's is properly
   extractable, matching `export_pdf.py`'s already-proven choice. One
   bidi quirk confirmed empirically in *both* engines: combining Arabic
   text/numerals and a hyphenated Western timestamp range (e.g.
   "0:00-5:13") in one RTL paragraph reorders the range's two halves, so
   the meta line keeps page-label and timestamp as two separate blocks
   with explicit `direction`s (flex cells, i.e. independent bidi
   paragraphs — re-confirmed after the flow-layout rewrite), never one
   paragraph — a plain Arabic session label (`export_pdf.session_label_ar`,
   e.g. "المجلس التاسع") has no such range
   in it, so it's safe to fold into the page-label block instead of
   needing a third one. `_draw_marker` centers the marker *above* the anchor
   word's own width, not offset past either edge: offsetting sideways
   only has guaranteed clearance for a word at a line's start/end (open
   margin next to it) — a word in the middle of a line, the common case,
   has another word right up against it, and a sideways offset lands on
   top of it (found by spot-checking a real generated whole-book PDF:
   a marker ended up overlapping the two words either side of it).

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
- `assets/club_image.jpeg`/`telegram_icon.png`/`facebook_icon.png` are read
  once at import time into `CLUB_IMAGE_BYTES`/`TELEGRAM_ICON_BYTES`/
  `FACEBOOK_ICON_BYTES` and passed into every `build_pdf`/
  `build_annotated_pdf` call. They live under `assets/` (tracked in git),
  not `data/` (gitignored — the user's own book PDFs/session audio): these
  are fixed club-branding design assets that every book/session's export
  needs, not per-book input, so they must survive a fresh clone rather than
  need re-adding by hand each time.
- `book_info.json` in the book folder holds `{title_ar, author_ar,
  page_offset, commentator_ar}` for the Arabic PDF report (`export_pdf.py`);
  `title_ar` defaults to the book PDF's filename stem if absent, and the
  file gets written back to disk the moment any of its fields is edited
  (every widget change reruns the script, and the script saves whenever
  the live values differ from what's on disk) so the user only types it
  once per book — an earlier version saved only on Run/Resume, which
  silently discarded the values whenever the user filled them in and then
  only rendered/exported without ever clicking Run. `commentator_ar` is the person doing the live
  commentary for the book (shown on the cover page as `علّق عليه: <name>`),
  distinct from `author_ar` (the book's own author) — both are plain text
  inputs the UI persists the same way. `page_offset` corrects for `align.py`/`extract_book.py`
  working in physical-PDF-page-index space, which is usually behind the
  book's own printed page numbers by however many front-matter pages
  (cover, table of contents, preface...) precede the book's page 1 —
  a fixed, book-specific value the user sets once by comparing a known
  printed page number against its PDF page index. Purely a display/export
  transform — the alignment logic itself never knows about it and keeps
  working in raw PDF-page space, which is what it can actually infer from
  the book text. `st.session_state["comments"]` also stays in that raw
  space always (never mutated with the offset baked in) — the shift to
  `comment["page"] += page_offset` happens fresh, reading the "Page
  offset" field's *live* value, at every point comments get displayed or
  exported (the review list, `comments.json`, both per-session annotated-
  PDF buttons, the whole-book one). This is deliberate, not an
  optimization: an earlier version applied the offset once at Run/Resume
  time and stored the result, which meant correcting the field after
  noticing it was wrong silently did nothing until the user re-ran or
  re-resumed — surprising, since it looks like any other live input.
  Keeping the stored copy always raw and re-applying the live offset on
  every rerun makes the field actually live, and is also *why* raw is what
  gets saved to `.run_state.json` in the first place (see below) rather
  than an offset-applied copy.
- A run's transcript+comments are no longer memory-only: `save_run_state`
  writes them to a hidden `.run_state.json` in the *session* folder (not
  the book folder — this is per-session, unlike the book-level pages
  cache) right after alignment completes, before the (comparatively cheap)
  audio-clip-extraction step. This is deliberately the single costliest
  thing to lose — CPU-only transcription of an hour-long session takes
  much longer than everything else in the pipeline combined. Reviewer
  edits (`keep_{i}`/`text_{i}` widget state) are snapshotted separately to
  `.review_state.json` on every script rerun (i.e. every checkbox/text
  change, since Streamlit reruns top-to-bottom on each interaction) via
  `save_review_state` — kept in a separate file from the transcript so
  editing one comment's text doesn't rewrite the whole (potentially large)
  transcript each time. If a `.run_state.json` exists for the selected
  session, a "Resume saved run" button appears next to "Run pipeline";
  resuming reloads the saved transcript/comments (skipping re-transcription
  and re-alignment entirely) plus any saved review edits, and only redoes
  the cheap steps (page-cache lookup, audio clipping). `comments.json`/
  `transcript.json`/`book_pages.json`/PDF are still separate, explicit
  downloads for taking results *out* of the tool — the two state files
  above are an internal safety net, not meant to be consumed directly.
- "Whole-book annotated PDF" (its own expander) sweeps every numbered
  session folder under the selected book, not just the one picked above —
  split into one processing step and two independent render steps, since
  processing (slow, style-agnostic) and rendering (fast, style-specific)
  are different concerns; an earlier version bundled both into two
  "Process all sessions" buttons, one per style, which re-swept every
  session (redundantly, if slowly-cheaply thanks to `ensure_session_run`'s
  own caching) just to produce the other style.
  - **Process all sessions**: `ensure_session_run` reuses a session's
    `.run_state.json` if it has one, otherwise transcribes+aligns it right
    there (this is the one place outside the main "Run pipeline" flow
    that calls `transcribe`, and is why this can take a very long time —
    CPU-only transcription of several un-processed long sessions, run
    serially with a `st.status` per session so progress is visible).
    `load_reviewed_comments_for_session` then reconstructs each session's
    kept+edited comments straight from its saved `.run_state.json`/
    `.review_state.json` on disk — a session with no `.review_state.json`
    yet (never manually reviewed) falls back to the same "keep candidates
    ≥ `DEFAULT_UNCHECKED_BELOW_WORDS`" default the live review UI itself
    starts with. `load_reviewed_comments_for_book` concatenates every
    session's list and sorts by page (then session number, then start
    time), and is called with `page_offset=0` — kept raw in
    `st.session_state["whole_book_comments"]` for the same live-reactivity
    reason `st.session_state["comments"]` is (see above), so correcting
    "Page offset" afterward doesn't require re-processing, just
    re-rendering.
  - **Generate PDF (bottom of page)/(separate page)**: apply the *live*
    page_offset to a copy of the collected raw comments and call
    `export_annotated_pdf.build_annotated_pdf` against the book-level PDF
    — comments from different sessions covering the same page interleave
    in page order rather than being grouped by session. Each can be
    clicked independently, any number of times, without re-processing.
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
