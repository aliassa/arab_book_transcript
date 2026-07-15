# How to use the reading-club pipeline

Extracts a reader's spoken *comments* from a recorded reading-club session by
comparing the audio transcript against the book's PDF text. Three stages:
book extraction (OCR/direct text) -> transcription (faster-whisper) ->
alignment (diff transcript against book text, comments = the parts that
don't match).

Two ways to run it: the command line (`src/extract_book.py`,
`src/transcribe.py`, `src/align.py`) or a Streamlit UI (`src/app.py`) that
wraps the same functions. Pick whichever is more convenient — output is
identical either way.

## Layout

```
src/      pipeline code + the Streamlit app
assets/   club logo + Telegram/Facebook icons (tracked in git -- fixed branding, not per-book input)
data/     book PDFs and session audio (gitignored -- your local input files)
output/   transcripts, extracted book text, comments (gitignored -- generated)
docs/     background/planning notes
```

The UI expects `data/` organized per book, one subfolder per reading session:

```
data/<book_folder>/book.pdf          # full book PDF
data/<book_folder>/book_info.json    # {"title_ar", "author_ar", "commentator_ar", "page_offset"} -- optional, UI fills it in
data/<book_folder>/1/session1.ogg    # session 1's audio
data/<book_folder>/2/session2.ogg    # session 2's audio
...
```
Session folders just need to be named `1`, `2`, `3`, ... A session folder can
optionally hold its own PDF (e.g. a pre-clipped page range) which the UI will
prefer over the book-level PDF for that session — but this isn't necessary
for correctness, see the note on `book_pages_cache` below.

## Prerequisites

- Python 3.12+ venv with `faster-whisper`, `pymupdf`, `pytesseract`,
  `Pillow`, `streamlit`, `weasyprint` installed (`requirements.txt`).
- System `tesseract-ocr` + `tesseract-ocr-ara` (Arabic language pack), used
  for scanned/low-quality PDF pages.
- `ffmpeg` — used to clip audio manually (see below) and, in the UI, to cut
  each candidate comment's own playback clip for review.
- `fonts-noto-core` — the PDF exports set Arabic text in Noto Naskh Arabic
  and the cover title in Noto Kufi Arabic. Without these fonts WeasyPrint
  silently substitutes a generic serif: everything still renders, just in
  the wrong typeface, with no error pointing at the missing fonts.
- WeasyPrint's native libraries (`libpango-1.0-0`, `libpangoft2-1.0-0`) —
  `pip install weasyprint` succeeds without them and only fails later, at
  import time. Usually already present on a desktop install, missing on
  servers/containers.
- *(optional)* An Anthropic API key, only for the UI's "Fix obvious
  transcription errors with Claude" button: `export ANTHROPIC_API_KEY=...`
  in the shell before `streamlit run`. Everything else works without it —
  the button just shows an auth error if clicked with no key.

### Setting up from a fresh clone

The venv is not committed to git (`.gitignore`) — it's large,
platform-specific, and trivial to recreate from `requirements.txt`. Neither
is `data/` (input PDFs/audio) or `output/` (generated files) — copy your
book PDF and session recording into `data/` yourself after cloning.

```bash
git clone git@github.com:aliassa/arab_book_transcript.git
cd arab_book_transcript

# system deps (Debian/Ubuntu)
sudo apt install -y tesseract-ocr tesseract-ocr-ara ffmpeg \
  fonts-noto-core libpango-1.0-0 libpangoft2-1.0-0

# python deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

mkdir -p data output
```

Every time you come back to work on this (new shell), just:
```bash
cd arab_book_transcript   # or wherever you cloned it
source .venv/bin/activate
```

## Option A: command line

Three steps, each one script. Every script takes an input file and an
optional output path.

### 1. Extract book text
```bash
python3 src/extract_book.py <book.pdf> [output.json]
```
Per page: tries direct text extraction, scores its quality (fraction of
plausible Arabic characters), and falls back to OCR if the score is below
0.6. Output JSON is a list of `{page_number, text, method, quality}`.

```bash
python3 src/extract_book.py data/hosn_thann_billah.pdf output/book_pages.json
```

### 2. Transcribe audio
```bash
python3 src/transcribe.py <audio_file> [output.json]
```
Runs faster-whisper (`large-v3`, Arabic, word-level timestamps, VAD on).
**First run downloads the ~3GB model from Hugging Face** — needs internet
once, then it's cached locally. On this machine (CPU only, no GPU), expect
roughly real-time-or-slower: a ~1hr session can take a couple of hours.
Run long ones in the background:
```bash
nohup python3 src/transcribe.py data/full_session.ogg output/full_transcript.json &
```

### 3. Align and extract comments
```bash
python3 src/align.py <book_pages.json> <transcript.json> [page_number] > comments.json
```
Diffs the transcript's word sequence against the book's (book = reference).
Runs of transcript words with no match in the book, at least 5 words long,
are candidate comments. Each comment's book page is inferred from the
nearest matched book text around it, so this works whether you pass a
single page number (restricts comparison to it) or diff against the whole
book (omit the argument) — either way every comment gets a page number.
Prints a human-readable summary to stderr and the JSON array to stdout —
hence the `>` redirect.

```bash
python3 src/align.py output/book_pages.json output/full_transcript.json 4 > output/comments.json
```

### Clipping input files (optional)

Useful for testing on a slice of a long recording or book instead of the
whole thing.

**Audio, by time range:**
```bash
ffmpeg -y -i data/full_session.ogg -ss 00:13:39 -to 00:16:00 -c copy data/clip.ogg
```
`-ss` / `-to` are `HH:MM:SS`; `-c copy` avoids re-encoding.

**PDF, by page range** (via `pymupdf`, already a dependency — page numbers
are 1-indexed, inclusive, matching `extract_book.py`'s `page_number`):
```bash
python3 -c "
import fitz
doc = fitz.open('data/hosn_thann_billah.pdf')
out = fitz.open()
out.insert_pdf(doc, from_page=3, to_page=5)  # pages 4-6 (0-indexed range)
out.save('data/hosn_thann_billah_p4-6.pdf')
"
```
Adjust `from_page`/`to_page` (0-indexed: subtract 1 from the page numbers
you want) and the file names.

## Option B: the UI

```bash
source .venv/bin/activate
streamlit run src/app.py
```
Opens at `http://localhost:8501`. Then:

1. Pick the **Book** from the dropdown (populated from `data/`).
2. Check/edit **Book title (Arabic)**, **Author (Arabic)**, and
   **Commentator (Arabic)** — pre-filled from `book_info.json` if it
   exists, else Book title falls back to the book PDF's filename.
   Commentator is the person doing the live commentary for this book (shown
   on the exported cover page as "علّق عليه: <name>"), not the book's own
   author. All saved back to `book_info.json` the first time you run the
   pipeline for this book, so you only type them once.
3. Pick the **Session** — sessions are the numbered subfolders
   (`data/<book>/1`, `2`, ...), labelled with their Arabic ordinal
   (المجلس الأول, الثاني, ...). The PDF and audio for that session are found
   automatically: a session-specific PDF if one exists in that folder, else
   the book-level PDF; the audio file in that session's folder.
4. (Optional) Open **Advanced options** to change:
   - minimum comment length (default 7 words)
   - Whisper model size (default `large-v3`; smaller sizes are faster but
     less accurate — useful for a quick check before committing to a full
     run)
   - OCR fallback quality threshold (default 0.6)
   - **Analyze only part of the audio (testing)** — check this and pick a
     number of minutes to transcribe just the start of the recording
     instead of the whole thing, to see results in a couple of minutes
     while you're tuning the other settings. The "Estimated time to
     result" box updates to reflect the shorter duration. Unlike the
     manual ffmpeg-clip workflow below, this doesn't create a separate
     file — just uncheck it to go back to the full recording.
5. Click **Run pipeline**. Each stage reports progress in turn (book
   extraction -> model load -> transcription -> alignment -> per-comment
   audio clipping) — transcription is the slow part, same as the CLI. Book
   extraction is skipped (shows "(cached, instant)") if this exact PDF was
   already extracted for an earlier session — see caching note below.
6. **Review each candidate**: every card shows the book page (plus roughly
   which word on the page it falls at, and the book text read right before
   it, so you don't have to reread the whole page to find it), the audio
   timestamp, a player for just that clip (so you can listen and read
   along), an editable text box pre-filled with the extracted text (fix
   anything the transcript got wrong), and a "Keep as a comment" checkbox
   (unchecked by default for short candidates, since those are
   disproportionately disfluencies — uncheck any others that turn out to
   be false positives too).
7. Click **Generate PDF report** — it uses whatever is currently in the
   text boxes and checkboxes at that moment, so review everything first.
   The finished PDF is written to `output/` (overwriting the previous copy
   of the same name) and opened in your PDF viewer automatically; a
   **Download comments_report.pdf** button also appears as a fallback,
   showing how many comments were kept. The same save-and-open behavior
   applies to every Generate-PDF button below. The report is Arabic-only, and
   opens with a club-branded cover page (club logo, book title/author,
   commentator, and Telegram/Facebook links as labeled icons plus a small
   footer line with the short URLs for printed copies — no ugly full URLs,
   no session number, since the cover fronts the book, not one session)
   followed directly by the comment count and the kept comments — no
   separate/blank page in between.

   A separate report isn't the only option: **Generate annotated PDF
   (bottom of page)** and **Generate annotated PDF (separate page)**
   overlay the same kept/edited comments directly onto the book's own PDF
   instead — the same cover page as page one, then a small numbered marker
   at the spot each comment was made, with the comment text either in new
   whitespace at the bottom of that page (long comments get truncated to
   fit) or on a whole new page inserted right after it (no truncation, but
   the book's page count grows). Both need the book's actual PDF, so
   they're disabled if it can't be found.
8. Download `comments.json`, `transcript.json`, or `book_pages.json` if you
   want the raw (unreviewed) output instead.

Both annotated-PDF buttons only cover the one session picked above. To get
every session's comments onto one annotated copy of the whole book, open
the **Whole-book annotated PDF (all sessions)** section (below the main
pipeline, works regardless of which session is currently selected). It's
two steps: click **Process all sessions** first — it transcribes and
aligns any session that's never been run yet (reusing saved results for
ones that already have been, including your manual review edits), showing
progress one session at a time. This can take a very long time if several
sessions haven't been processed before — transcription is the slow part
and there's no shortcut around doing it for each session's audio — so
start it and leave it running rather than waiting on it. Once it's done
(or if every session was already processed, which is instant), click
**Generate PDF (bottom of page)** or **(separate page)** to render the
combined result — either or both, and as many times as you like (e.g.
after nudging "Page offset") without repeating the slow processing step.

The Whisper model stays cached across runs in the same browser session, so
switching files and re-running doesn't reload it — only changing the model
size does. Starting a new **Run pipeline** clears any edits/checkboxes and
generated PDF from the previous run.

**Book text caching**: you don't need to clip the book PDF down to each
session's page range — the diff only pulls out transcript words that don't
match anywhere in the book, wherever in the book they match, so handing it
the full book every time is fine. To avoid re-running (possibly slow) OCR
on the full book for every session, extraction results are cached next to
the PDF as a hidden `.<pdf-name>_pages_cache.json` file, keyed on the PDF's
modification time and the quality threshold — so the first session for a
book pays the extraction cost once, every session after that is instant.
Delete that file (or touch/replace the PDF) to force re-extraction.

**A run's transcript and comments are saved automatically** to a hidden
`.run_state.json` in the session folder as soon as alignment finishes —
before the (comparatively cheap) audio-clipping step, so the expensive
part (transcription) survives a server restart even if you never click a
download button. Your review edits (checkbox/text changes) are also
autosaved continuously to `.review_state.json` in the same folder as you
go, and the review screen shows a running "N/M marked keep" count so you
can see that progress is being captured, not just at the end. If a saved
run exists for the session you pick, a **Resume saved run** button appears
next to **Run pipeline** — it reloads the saved transcript/comments (and
any review edits) without re-transcribing or re-aligning.

Generated PDFs and audio clips themselves still aren't saved anywhere —
only `comments.json`/`transcript.json`/`book_pages.json`/the PDF, via their
download buttons, get you a copy outside the tool. The two hidden state
files above are an internal safety net for resuming work in the UI, not
meant to be opened directly.

## Output format

`comments.json` (from either mode) is a list of:
```json
{
  "text": "...",       // normalized Arabic (no tashkeel, unified alef/hamza, no punctuation)
  "n_words": 54,
  "start": 51.86,       // seconds into the audio
  "end": 87.8,
  "page": 4             // inferred book page number
}
```

`comments_report.pdf` (UI only, after review) opens with the club-branded
cover page (logo, book title/author/commentator, Telegram/Facebook icons),
then lists just the kept comments — your edited text, book page, and
mm:ss timestamp range for each — with correctly shaped/reordered Arabic
text (via WeasyPrint).

## Known limitations (not yet fixed)

- **Boundary fuzziness**: if the reader repeats a book line before/after a
  comment (common — it re-anchors them after the digression), the diff can
  fold a few words of that repeated line into the comment span instead of
  cutting exactly at the digression's start.
- **Short disfluencies**: a stumble/self-correction while reading can also
  surface as a candidate if it happens to not match the book text and
  clears the 5-word threshold — it looks like a comment structurally even
  though it isn't one.
- Both are exactly what the planned (not yet built) speech-rhythm signal —
  pace and pause durations from the word timestamps — is meant to help
  disambiguate, per the original project brief. Until then, the UI's
  review step (listen to the clip, edit the text, uncheck false
  positives) is the actual fix — that's what it's for.
