# Reading Club Pipeline — Project Brief

## Goal
Extract the reader's spoken *comments* (not the book text being read aloud)
from recorded reading-club sessions, by comparing an audio transcript
against the book's PDF text.

Inputs: (1) audio recording of a session, (2) the book as a PDF (Arabic,
MSA/Classical). Output (for now): just the comment text, extracted
accurately. Page-linking / storage format is deferred to later.

## Constraints established
- Book PDFs vary: some are real digital text, some scanned images, quality
  of tashkeel (diacritics) varies too. Pipeline must handle both per-page,
  not assume one type per file.
- Occasional ~1hr sessions, not high volume -> prioritize accuracy over
  speed/cost.
- Comments are at least ~5-6 words (threshold adjustable later).
- Reader's speaking rhythm/pace differs between reading the book and
  commenting -> plan to use this as a secondary signal alongside text
  diffing, not yet implemented.

## Pipeline design (3 stages)
1. **Book extraction** (`extract_book.py`, done, tested on a real sample
   page): per-page router — try direct text extraction (pymupdf), score
   quality (ratio of valid Arabic chars), fall back to OCR (pytesseract,
   `ara` language, psm 6) if quality is below threshold (0.6). Tested on
   real book page ("hosn_thann_billah.pdf" p.4) -- OCR quality was good
   (0.99 heuristic score, readable coherent text with only minor noise).

2. **Transcription** (`transcribe.py`, written, NOT yet successfully run
   by user due to local env issues — pip install problems, wrong file
   content saved locally). Uses faster-whisper, model size large-v3,
   language="ar", word_timestamps=True (needed for the rhythm signal
   later), vad_filter on. Needs: `pip install faster-whisper`. First run
   downloads ~3GB model from Hugging Face (needs internet once).

3. **Alignment / comment extraction** (NOT YET BUILT). Planned approach:
   - Normalize both book text and transcript before comparing: strip
     tashkeel, unify alef/hamza/ta-marbuta variants, strip punctuation.
   - Word-level sequence alignment (e.g. difflib.SequenceMatcher or
     Levenshtein-based alignment) treating book text as reference.
   - Transcript segments that don't match nearby book text = candidate
     comments ("insertions" in diff terms).
   - Apply minimum length threshold (~5-6 words) to filter noise.
   - Cross-check candidates using word-timestamp-derived rhythm features
     (speaking rate, pause durations) from Whisper output as corroborating
     signal — comments likely to have distinguishable pacing vs. reading.

## Current blocker
User is switching to Claude Code (local terminal) to resolve local
environment issues (Python/pip pointing to different environments,
file content mismatches after download) and continue development
directly on their machine instead of through chat file uploads.

## Sample files already validated
- `sample_pages.pdf` (page 4 of hosn_thann_billah.pdf) -> OCR'd
  successfully, output text quality good.
- `sample_audio.ogg` (13:39-14:44 of 5931441043701505437.ogg, ~65s) ->
  not yet transcribed due to local setup issues.

## Next steps
1. Get faster-whisper running locally on sample_audio.ogg, inspect
   transcript quality vs. the OCR'd page text.
2. Build the normalization module (Arabic text canonicalization).
3. Build the alignment/diff module and test comment extraction on the
   validated sample pair.
4. Only after core extraction works well: revisit output format
   (page-linked notes, etc. — deferred by design).
