# How to use the reading-club pipeline

Extracts a reader's spoken *comments* from a recorded reading-club session by
comparing the audio transcript against the book's PDF text. Three stages:
book extraction (OCR/direct text) -> transcription (faster-whisper) ->
alignment (diff transcript against book text, comments = the parts that
don't match).

Two ways to run it: the command line (`extract_book.py`, `transcribe.py`,
`align.py`) or a Streamlit UI (`app.py`) that wraps the same functions.
Pick whichever is more convenient — output is identical either way.

## Prerequisites (already installed on this machine)

- Python venv at `.venv/` with `faster-whisper`, `pymupdf`, `pytesseract`,
  `Pillow`, `streamlit` installed.
- System `tesseract-ocr` + `tesseract-ocr-ara` (Arabic language pack), used
  for scanned/low-quality PDF pages.
- `ffmpeg`, if you need to clip audio (see below).

Activate the venv before running anything:
```bash
cd /home/ali/perso/book_extraction
source .venv/bin/activate
```

## Option A: command line

Three steps, each one script. Every script takes an input file and an
optional output path.

### 1. Extract book text
```bash
python3 extract_book.py <book.pdf> [output.json]
```
Per page: tries direct text extraction, scores its quality (fraction of
plausible Arabic characters), and falls back to OCR if the score is below
0.6. Output JSON is a list of `{page_number, text, method, quality}`.

```bash
python3 extract_book.py hosn_thann_billah.pdf book_pages.json
```

### 2. Transcribe audio
```bash
python3 transcribe.py <audio_file> [output.json]
```
Runs faster-whisper (`large-v3`, Arabic, word-level timestamps, VAD on).
**First run downloads the ~3GB model from Hugging Face** — needs internet
once, then it's cached locally. On this machine (CPU only, no GPU), expect
roughly real-time-or-slower: a ~1hr session can take a couple of hours.
Run long ones in the background:
```bash
nohup python3 transcribe.py full_session.ogg full_transcript.json &
```

### 3. Align and extract comments
```bash
python3 align.py <book_pages.json> <transcript.json> [page_number] > comments.json
```
Diffs the transcript's word sequence against the book's (book = reference).
Runs of transcript words with no match in the book, at least 5 words long,
are candidate comments. Pass a page number to restrict the comparison to
one page; omit it to diff against the whole book. Prints a human-readable
summary to stderr and the JSON array to stdout — hence the `>` redirect.

```bash
python3 align.py book_pages.json full_transcript.json 4 > comments.json
```

### Clipping audio (optional)

If you only want to test on part of a long recording:
```bash
ffmpeg -y -i full_session.ogg -ss 00:13:39 -to 00:16:00 -c copy clip.ogg
```
`-ss` / `-to` are `HH:MM:SS`; `-c copy` avoids re-encoding.

## Option B: the UI

```bash
source .venv/bin/activate
streamlit run app.py
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
   extraction -> model load -> transcription -> alignment) — transcription
   is the slow part, same as the CLI.
5. Results appear as cards (timestamp, word count, the Arabic text, RTL).
6. Download `comments.json`, `transcript.json`, or `book_pages.json` if you
   want the raw output.

The Whisper model stays cached across runs in the same browser session, so
switching files and re-running doesn't reload it — only changing the model
size does.

Note: file uploads are capped at 500MB (`.streamlit/config.toml`); the full
book PDF and a full session recording should both fit.

## Output format

`comments.json` (from either mode) is a list of:
```json
{
  "text": "...",       // normalized Arabic (no tashkeel, unified alef/hamza, no punctuation)
  "n_words": 54,
  "start": 51.86,       // seconds into the audio
  "end": 87.8
}
```

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
  disambiguate, per the original project brief.
