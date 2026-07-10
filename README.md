# arab_book_transcript

Extracts a reader's spoken comments from a recorded Arabic reading-club
session by diffing the audio transcript (faster-whisper) against the book's
PDF text (pymupdf/Tesseract OCR) — whatever the transcript says that the
book doesn't is the reader's own commentary. Includes a Streamlit UI for
reviewing candidates and exporting them as a standalone PDF report or
overlaid onto the book's own pages.

- **[HOWTO.md](HOWTO.md)** — prerequisites, setup from a fresh clone, and
  usage (CLI pipeline and UI).
- **[docs/project_brief.md](docs/project_brief.md)** — original design
  rationale.
- **[CLAUDE.md](CLAUDE.md)** — architecture notes per module.
