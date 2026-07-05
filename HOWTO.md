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
data/     book PDFs and session audio (gitignored -- your local input files)
output/   transcripts, extracted book text, comments (gitignored -- generated)
docs/     background/planning notes
```

## Prerequisites

- Python 3.12+ venv with `faster-whisper`, `pymupdf`, `pytesseract`,
  `Pillow`, `streamlit`, `weasyprint` installed (`requirements.txt`).
- System `tesseract-ocr` + `tesseract-ocr-ara` (Arabic language pack), used
  for scanned/low-quality PDF pages.
- `ffmpeg` — used to clip audio manually (see below) and, in the UI, to cut
  each candidate comment's own playback clip for review.

### Setting up from a fresh clone

The venv is not committed to git (`.gitignore`) — it's large,
platform-specific, and trivial to recreate from `requirements.txt`. Neither
is `data/` (input PDFs/audio) or `output/` (generated files) — copy your
book PDF and session recording into `data/` yourself after cloning.

```bash
git clone git@github.com:aliassa/arab_book_transcript.git
cd arab_book_transcript

# system deps (Debian/Ubuntu)
sudo apt install -y tesseract-ocr tesseract-ocr-ara ffmpeg

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

1. Upload the book PDF under **Book (PDF)**.
2. Upload the session recording under **Session audio**.
3. (Optional) Open **Advanced options** to change:
   - minimum comment length (default 5 words)
   - Whisper model size (default `large-v3`; smaller sizes are faster but
     less accurate — useful for a quick check before committing to a full
     run)
   - OCR fallback quality threshold (default 0.6)
4. Click **Run pipeline**. Each stage reports progress in turn (book
   extraction -> model load -> transcription -> alignment -> per-comment
   audio clipping) — transcription is the slow part, same as the CLI.
5. **Review each candidate**: every card shows the book page and audio
   timestamp, a player for just that clip (so you can listen and read
   along), an editable text box pre-filled with the extracted text (fix
   anything the transcript got wrong), and a "Keep as a comment" checkbox
   (uncheck it to drop false positives like disfluencies).
6. Click **Generate PDF report** — it uses whatever is currently in the
   text boxes and checkboxes at that moment, so review everything first.
   A **Download comments_report.pdf** button appears once it's built,
   showing how many comments were kept.
7. Download `comments.json`, `transcript.json`, or `book_pages.json` if you
   want the raw (unreviewed) output instead.

The Whisper model stays cached across runs in the same browser session, so
switching files and re-running doesn't reload it — only changing the model
size does. Starting a new **Run pipeline** clears any edits/checkboxes and
generated PDF from the previous run.

Note: file uploads are capped at 500MB (`.streamlit/config.toml`); the full
book PDF and a full session recording should both fit.

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

`comments_report.pdf` (UI only, after review) lists just the kept
comments — your edited text, book page, and mm:ss timestamp range for
each — with correctly shaped/reordered Arabic text (via WeasyPrint).

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
