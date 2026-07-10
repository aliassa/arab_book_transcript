"""
Streamlit UI for the reading-club pipeline.

Wraps the same three stages used from the command line (extract_book,
transcribe, align) so both entry points stay in sync -- this file adds
no pipeline logic of its own beyond per-comment audio clipping and PDF
export, which only exist here (reviewing by ear/eye is a UI-only step).

Run with: streamlit run app.py
"""

import json
import math
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from align import extract_comments_from_transcript
from export_annotated_pdf import build_annotated_pdf
from export_pdf import build_pdf, session_label_ar
from extract_book import extract_book
from transcribe import MODEL_SIZE, load_model, transcribe

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
AUDIO_EXTS = {".ogg", ".mp3", ".wav", ".m4a", ".flac"}

# The club's own logo/social icons, not book-specific -- checked into git
# under assets/ (unlike data/, which is gitignored user input: book PDFs
# and session audio) so these design assets survive a fresh clone instead
# of silently disappearing. Read once at startup and passed as bytes into
# export_pdf/export_annotated_pdf, which stay pure functions that don't
# touch the filesystem themselves.
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
CLUB_IMAGE_PATH = ASSETS_DIR / "club_image.jpeg"
CLUB_IMAGE_BYTES = CLUB_IMAGE_PATH.read_bytes() if CLUB_IMAGE_PATH.exists() else None
TELEGRAM_ICON_PATH = ASSETS_DIR / "telegram_icon.png"
FACEBOOK_ICON_PATH = ASSETS_DIR / "facebook_icon.png"
TELEGRAM_ICON_BYTES = TELEGRAM_ICON_PATH.read_bytes() if TELEGRAM_ICON_PATH.exists() else None
FACEBOOK_ICON_BYTES = FACEBOOK_ICON_PATH.read_bytes() if FACEBOOK_ICON_PATH.exists() else None

# A run's transcript+comments (expensive: needs re-transcribing to
# reproduce) and a reviewer's per-comment keep/text edits (cheap to redo,
# but tedious) are split into two files so review edits -- saved on every
# rerun, i.e. every checkbox/text change -- don't rewrite the whole
# (potentially large, hour-long-session) transcript each time.
RUN_STATE_FILENAME = ".run_state.json"
REVIEW_STATE_FILENAME = ".review_state.json"

MODEL_SIZES = ["large-v3", "medium", "small", "base", "tiny"]

# Rough seconds-of-processing-per-second-of-audio on CPU (no GPU). Based on
# this machine's own measured ~2x real-time for large-v3; the rest are
# ballpark guesses at how model size scales, not measurements -- actual
# speed depends heavily on hardware.
SPEED_MULTIPLIER = {
    "large-v3": 2.0,
    "medium": 1.1,
    "small": 0.5,
    "base": 0.25,
    "tiny": 0.15,
}
MODEL_LOAD_OVERHEAD_S = 20  # rough fixed cost the first time a size is loaded

# Short candidates are disproportionately disfluencies/false positives (see
# CLAUDE.md's "known algorithmic limitations") -- still shown for review
# since they might be real, but pre-unchecked so reviewing a run means
# mostly *checking* the long ones rather than *unchecking* the short ones.
DEFAULT_UNCHECKED_BELOW_WORDS = 10

st.set_page_config(page_title="Reading Club — Comment Extractor", layout="centered")

# Font/size for the comment text areas are user-adjustable (widgets further
# down, keyed "comment_font_family"/"comment_font_size") -- read here from
# session_state so the very next rerun after a change already reflects it;
# the first-ever render (before the widgets exist) falls back to the same
# defaults the widgets themselves use.
COMMENT_FONT_OPTIONS = {
    "Default (sans-serif)": "Tahoma, Arial, sans-serif",
    "Serif (Traditional Arabic)": '"Traditional Arabic", "Amiri", serif',
    "Naskh (book-style)": '"Noto Naskh Arabic", "Traditional Arabic", serif',
}
DEFAULT_COMMENT_FONT_FAMILY = "Naskh (book-style)"
DEFAULT_COMMENT_FONT_SIZE = 1.3

_font_family_label = st.session_state.get("comment_font_family", DEFAULT_COMMENT_FONT_FAMILY)
_font_size = st.session_state.get("comment_font_size", DEFAULT_COMMENT_FONT_SIZE)
st.markdown(
    f"<style>.stTextArea textarea {{ direction: rtl; text-align: right; "
    f"font-size: {_font_size}rem; "
    f"font-family: {COMMENT_FONT_OPTIONS[_font_family_label]}; }}</style>",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def cached_model(model_size: str):
    return load_model(model_size)


def format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def format_duration(seconds: float) -> str:
    seconds = max(int(round(seconds)), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def probe_duration(path: Path) -> float | None:
    """Audio duration via ffprobe, without running any transcription."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def list_book_dirs() -> list[Path]:
    if not DATA_DIR.is_dir():
        return []
    return sorted((d for d in DATA_DIR.iterdir() if d.is_dir()), key=lambda d: d.name)


def list_session_dirs(book_dir: Path) -> list[Path]:
    return sorted(
        (d for d in book_dir.iterdir() if d.is_dir() and d.name.isdigit()),
        key=lambda d: int(d.name),
    )


def find_pdf(*dirs: Path) -> Path | None:
    """First PDF found directly in these dirs, in order (session-specific clip first)."""
    for d in dirs:
        matches = sorted(d.glob("*.pdf"))
        if matches:
            return matches[0]
    return None


def find_audio(directory: Path) -> Path | None:
    matches = sorted(p for p in directory.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    return matches[0] if matches else None


def load_book_info(book_dir: Path) -> dict:
    info_path = book_dir / "book_info.json"
    if not info_path.exists():
        return {}
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_book_info(
    book_dir: Path, title_ar: str, author_ar: str, page_offset: int = 0, commentator_ar: str = ""
) -> None:
    info_path = book_dir / "book_info.json"
    info_path.write_text(
        json.dumps(
            {
                "title_ar": title_ar,
                "author_ar": author_ar,
                "page_offset": page_offset,
                "commentator_ar": commentator_ar,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def save_and_open_pdf(pdf_bytes: bytes, filename: str) -> Path:
    """
    Writes a generated PDF straight into the repo's output/ folder and
    opens it in the system PDF viewer, so the normal local flow needs no
    manual "Download" click (the download buttons stay as a fallback,
    e.g. when the browser isn't on the machine running the app). Same
    filename overwrites the previous copy in place -- regenerating should
    update the one file, not pile up "name (6).pdf" duplicates the way
    repeated browser downloads did. Must only be called inside a
    generate-button branch, never on ordinary reruns: Streamlit reruns
    the whole script on every widget interaction, and re-opening the
    viewer on each of those would spawn a window per checkbox click.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    path.write_bytes(pdf_bytes)
    try:
        subprocess.Popen(
            ["xdg-open", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass  # headless / no viewer configured -- the file is still saved
    return path


def get_book_pages(pdf_path: Path, quality_threshold: float) -> tuple[list[dict], bool]:
    """
    Extracted page text is expensive (OCR) but doesn't change unless the PDF
    or the quality threshold does, and the same book PDF gets reused across
    many sessions -- so cache it next to the PDF instead of re-extracting
    every run. Returns (pages, used_cache).
    """
    cache_path = pdf_path.with_name(f".{pdf_path.stem}_pages_cache.json")
    mtime = pdf_path.stat().st_mtime
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cached = None
        if (
            cached
            and cached.get("mtime") == mtime
            and cached.get("quality_threshold") == quality_threshold
        ):
            return cached["pages"], True

    pages = extract_book(str(pdf_path), quality_threshold=quality_threshold)
    cache_path.write_text(
        json.dumps(
            {"mtime": mtime, "quality_threshold": quality_threshold, "pages": pages},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return pages, False


def save_run_state(
    session_dir: Path,
    transcript: dict,
    comments: list[dict],
    min_words: int,
    quality_threshold: float,
    model_size: str,
) -> None:
    """
    Persists a completed run's transcript+comments to the session folder
    immediately, not just on explicit download -- transcription is the
    expensive, CPU-only step (see CLAUDE.md), so losing it to a server
    restart before the reviewer gets around to clicking a download button
    is the costliest possible way to lose work.
    """
    path = session_dir / RUN_STATE_FILENAME
    path.write_text(
        json.dumps(
            {
                "saved_at": time.time(),
                "min_words": min_words,
                "quality_threshold": quality_threshold,
                "model_size": model_size,
                "transcript": transcript,
                "comments": comments,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def load_run_state(session_dir: Path) -> dict | None:
    path = session_dir / RUN_STATE_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_review_state(session_dir: Path, n_comments: int) -> None:
    """
    Snapshots the reviewer's current keep/text choices from widget state.
    Called on every script rerun while comments are on screen (i.e. every
    checkbox toggle or text edit, since Streamlit reruns top-to-bottom on
    each interaction) so review progress is never lost either, not just
    the raw transcription -- deliberately separate from save_run_state so
    editing one comment's text doesn't rewrite the whole transcript.
    """
    review = {
        str(i): {
            "keep": st.session_state.get(f"keep_{i}", True),
            "text": st.session_state.get(f"text_{i}", ""),
        }
        for i in range(n_comments)
    }
    path = session_dir / REVIEW_STATE_FILENAME
    path.write_text(
        json.dumps({"saved_at": time.time(), "review": review}, ensure_ascii=False),
        encoding="utf-8",
    )


def load_review_state(session_dir: Path) -> dict | None:
    path = session_dir / REVIEW_STATE_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def ensure_session_run(
    session_dir: Path,
    book_dir: Path,
    quality_threshold: float,
    model_size: str,
    min_words: int,
) -> dict | None:
    """
    Returns a session's saved run state, transcribing+aligning it first if
    it has never been run -- used by the whole-book export to sweep up
    every session's audio, not just ones already opened/reviewed in the
    UI. Returns None if the session has no audio or no PDF to align
    against (skipped, not an error -- a book folder may have session
    subfolders that aren't ready yet).
    """
    existing = load_run_state(session_dir)
    if existing is not None:
        return existing

    audio_path = find_audio(session_dir)
    pdf_path = find_pdf(session_dir, book_dir)
    if audio_path is None or pdf_path is None:
        return None

    pages, _ = get_book_pages(pdf_path, quality_threshold)
    model = cached_model(model_size)
    result = transcribe(str(audio_path), model_size=model_size, model=model)
    comments = extract_comments_from_transcript(pages, result["segments"], min_words=min_words)
    save_run_state(session_dir, result, comments, min_words, quality_threshold, model_size)
    return load_run_state(session_dir)


def load_reviewed_comments_for_session(session_dir: Path, page_offset: int) -> list[dict]:
    """
    Reconstructs the same kept+edited comment list the live review UI
    shows for one session, straight from its saved .run_state.json/
    .review_state.json -- without needing that session currently loaded in
    st.session_state, so a whole-book export can pull from every session
    that's ever been run, not just the one open right now. A session with
    no review_state yet (never opened for manual review) falls back to the
    same "keep long candidates by default" heuristic the review UI itself
    starts with.
    """
    run_state = load_run_state(session_dir)
    if run_state is None:
        return []
    review_state = load_review_state(session_dir)
    review = (review_state or {}).get("review", {})

    reviewed = []
    for i, c in enumerate(run_state["comments"]):
        override = review.get(str(i))
        keep = override["keep"] if override else c["n_words"] >= DEFAULT_UNCHECKED_BELOW_WORDS
        if not keep:
            continue
        text = override["text"] if override else c["text"]
        page = c["page"] + page_offset if c.get("page") is not None else None
        reviewed.append(
            {
                "text": text,
                "page": page,
                "start": c["start"],
                "end": c["end"],
                "position_in_page": c.get("position_in_page"),
                "page_word_count": c.get("page_word_count"),
                "context_before": c.get("context_before"),
                "session_number": int(session_dir.name),
            }
        )
    return reviewed


def load_reviewed_comments_for_book(book_dir: Path, page_offset: int) -> list[dict]:
    all_comments = []
    for session_dir in list_session_dirs(book_dir):
        all_comments.extend(load_reviewed_comments_for_session(session_dir, page_offset))
    all_comments.sort(
        key=lambda c: (c["page"] if c["page"] is not None else float("inf"), c["session_number"], c["start"])
    )
    return all_comments


def extract_clip(audio_path: str, start: float, end: float) -> bytes:
    """Cuts one comment's audio span out of the full recording as an mp3."""
    duration = max(end - start, 0.1)
    with tempfile.NamedTemporaryFile(suffix=".mp3") as tmp_clip:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", audio_path,
                "-t", str(duration),
                "-ar", "44100", "-ac", "1",
                "-c:a", "libmp3lame", "-q:a", "4",
                tmp_clip.name,
            ],
            check=True,
            capture_output=True,
        )
        return Path(tmp_clip.name).read_bytes()


st.title("Reading Club — Comment Extractor")
st.caption(
    "Pick a book and reading session from data/. The pipeline OCRs/extracts "
    "the book text, transcribes the audio, and diffs the two to surface the "
    "reader's own comments."
)

book_dirs = list_book_dirs()
if not book_dirs:
    st.error(
        f"No book folders found in `{DATA_DIR}`. Create `data/<book_name>/` "
        "with the book PDF and numbered session folders (`1`, `2`, ...)."
    )
    st.stop()

book_dir = st.selectbox("Book", book_dirs, format_func=lambda d: d.name)
book_info = load_book_info(book_dir)

root_pdf_matches = sorted(book_dir.glob("*.pdf"))
title_from_filename = root_pdf_matches[0].stem if root_pdf_matches else ""

col1, col2, col3 = st.columns([2, 2, 1])
title_ar = col1.text_input(
    "Book title (Arabic)",
    value=book_info.get("title_ar") or title_from_filename,
)
author_ar = col2.text_input("Author (Arabic)", value=book_info.get("author_ar", ""))
page_offset = col3.number_input(
    "Page offset",
    value=int(book_info.get("page_offset", 0)),
    step=1,
    help="Pages are counted by physical position in the PDF, which is "
    "usually behind the book's own printed page numbers by however many "
    "front-matter pages (cover, table of contents, preface...) come "
    "before the book's page 1. Set this once per book: printed page = "
    "PDF page + offset. Find it by opening the PDF at a page whose "
    "printed number you know and subtracting.",
)
commentator_ar = st.text_input(
    "Commentator (Arabic)",
    value=book_info.get("commentator_ar", ""),
    help='Shown on the exported PDFs\' cover page as "علّق عليه: <name>" '
    "-- the person doing the live commentary for this book, not the book's author.",
)
st.caption("Saved to `book_info.json` in this book's folder as you edit.")

# Persist edits immediately (Streamlit reruns the script on every widget
# change, so this runs right after any field is edited) -- an earlier
# version only saved on Run/Resume, which silently threw the values away
# whenever the user filled them in and then only rendered a PDF, never
# clicking Run before closing the app.
if (
    title_ar != book_info.get("title_ar", "")
    or author_ar != book_info.get("author_ar", "")
    or int(page_offset) != int(book_info.get("page_offset", 0))
    or commentator_ar != book_info.get("commentator_ar", "")
):
    save_book_info(book_dir, title_ar, author_ar, int(page_offset), commentator_ar)

session_dirs = list_session_dirs(book_dir)
if not session_dirs:
    st.error(f"No numbered session folders found in `{book_dir}`.")
    st.stop()

session_dir = st.selectbox(
    "Session", session_dirs,
    format_func=lambda d: f"{d.name} — {session_label_ar(int(d.name))}",
)
session_number = int(session_dir.name)

# A session folder may hold its own clipped/page-range PDF; fall back to the
# book-level PDF in the book's root folder otherwise.
pdf_path = find_pdf(session_dir, book_dir)
audio_path = find_audio(session_dir)

if pdf_path is None:
    st.error(f"No PDF found in `{session_dir}` or `{book_dir}`.")
if audio_path is None:
    st.error(f"No audio file found in `{session_dir}`.")

audio_duration = probe_duration(audio_path) if audio_path is not None else None

with st.expander("Advanced options"):
    min_words = st.slider("Minimum comment length (words)", 3, 15, 7)
    model_size = st.selectbox(
        "Whisper model size", MODEL_SIZES, index=MODEL_SIZES.index(MODEL_SIZE)
    )
    quality_threshold = st.slider("OCR fallback quality threshold", 0.0, 1.0, 0.6, 0.05)

    clip_minutes = None
    if audio_duration:
        full_minutes = max(1, math.ceil(audio_duration / 60))
        limit_audio = st.checkbox(
            "Analyze only part of the audio (testing)",
            help="Transcribe just the first N minutes instead of the whole "
            "recording, to see results quickly while testing settings.",
        )
        if limit_audio:
            clip_minutes = st.slider("Minutes to analyze (from the start)", 1, full_minutes, full_minutes)

# Purely a rendering setting for the annotated-PDF exports further down
# (both per-session and whole-book) -- unlike the options above, changing
# this needs only a re-click of "Generate...", not a pipeline rerun, so it
# lives outside "Advanced options" to avoid implying otherwise.
comment_pdf_font_size = st.slider(
    "Comment text size in exported PDF",
    9, 22, 13,
    help="Font size for the comment text overlaid onto the book PDF itself "
    "(the annotated PDF exports below) -- not the review text boxes above. "
    "Changing this doesn't need a pipeline rerun, just click "
    "\"Generate...\" again.",
)

# clip_timestamps is faster-whisper's own "start,end" range syntax (seconds);
# passing it through to transcribe() means timestamps come back absolute
# against the full file, so nothing downstream (audio clipping, page
# inference) needs to know a clip was used -- no separate clipped audio file
# needed either, unlike the CLI's manual ffmpeg-clip workflow in HOWTO.md.
clip_timestamps = "0"
effective_duration = audio_duration
if clip_minutes is not None and audio_duration and clip_minutes * 60 < audio_duration:
    clip_timestamps = f"0,{clip_minutes * 60}"
    effective_duration = float(clip_minutes * 60)

estimated_seconds = None
if effective_duration:
    overhead = 0 if model_size in st.session_state.get("loaded_models", set()) else MODEL_LOAD_OVERHEAD_S
    estimated_seconds = effective_duration * SPEED_MULTIPLIER.get(model_size, 1.0) + overhead
    with st.container(border=True):
        st.metric(
            "Estimated time to result",
            format_duration(estimated_seconds),
            help=(
                f"Rough estimate for {format_duration(effective_duration)} of audio "
                f"on '{model_size}' running on CPU -- actual time depends on "
                "your hardware, this is a ballpark, not a promise."
            ),
        )

saved_run = load_run_state(session_dir)

btn_col1, btn_col2 = st.columns(2)
run = btn_col1.button("Run pipeline", type="primary", disabled=not (pdf_path and audio_path))
resume = False
if saved_run:
    saved_when = datetime.fromtimestamp(saved_run["saved_at"]).strftime("%Y-%m-%d %H:%M")
    resume = btn_col2.button(
        f"Resume saved run ({len(saved_run['comments'])} comments, saved {saved_when})",
        disabled=not (pdf_path and audio_path),
        help="Reload this session's last transcription + comments (and any "
        "review progress) without re-transcribing.",
    )

if run:
    # Drop any leftover per-comment widget state / generated PDF from a
    # previous run so stale edits don't leak into a new one's results.
    for key in list(st.session_state.keys()):
        if key.startswith("text_") or key.startswith("keep_"):
            del st.session_state[key]
    st.session_state.pop("pdf_bytes", None)
    st.session_state.pop("annotated_bottom_bytes", None)
    st.session_state.pop("annotated_inserted_bytes", None)

    run_start = time.time()

    with st.status("Extracting book text...") as status:
        pages, used_cache = get_book_pages(pdf_path, quality_threshold)
        n_ocr = sum(1 for p in pages if p["method"] == "ocr")
        cache_note = " (cached, instant)" if used_cache else ""
        status.update(
            label=f"Book extracted{cache_note} — {len(pages)} page(s), {n_ocr} needed OCR",
            state="complete",
        )

    with st.status("Loading Whisper model...") as status:
        model = cached_model(model_size)
        st.session_state.setdefault("loaded_models", set()).add(model_size)
        status.update(label=f"Model ready ({model_size})", state="complete")

    with st.status("Transcribing audio (this can take a while)...") as status:
        result = transcribe(
            str(audio_path), model_size=model_size, model=model, clip_timestamps=clip_timestamps
        )
        if clip_timestamps != "0":
            label = f"Transcribed first {clip_minutes} min (testing) of {result['duration']:.0f}s total audio"
        else:
            label = f"Transcribed {result['duration']:.0f}s of audio"
        status.update(label=label, state="complete")

    with st.status("Aligning transcript against book text...") as status:
        comments = extract_comments_from_transcript(
            pages, result["segments"], min_words=min_words
        )
        status.update(
            label=f"Found {len(comments)} candidate comment(s)", state="complete"
        )

    # Saved (and kept in session_state below) in raw PDF-page-index space --
    # extract_comments_from_transcript works in that space, since that's
    # what it can infer from the book text. The page_offset shift to the
    # book's own printed page numbers happens only where comments are
    # displayed/exported below, reading the "Page offset" field live on
    # every rerun -- not here, and not stored -- so dragging that field
    # updates every page number shown immediately, without needing to
    # Run/Resume again just to pick up a correction.
    save_run_state(session_dir, result, comments, min_words, quality_threshold, model_size)

    with st.status("Extracting comment audio clips...") as status:
        clips = [extract_clip(str(audio_path), c["start"], c["end"]) for c in comments]
        status.update(label=f"Extracted {len(clips)} audio clip(s)", state="complete")

    st.session_state["pages"] = pages
    st.session_state["transcript"] = result
    st.session_state["comments"] = comments
    st.session_state["clips"] = clips
    st.session_state["book_folder_name"] = book_dir.name
    st.session_state["book_title_ar"] = title_ar
    st.session_state["author_ar"] = author_ar
    st.session_state["commentator_ar"] = commentator_ar
    st.session_state["session_number"] = session_number
    st.session_state["pdf_path"] = str(pdf_path)
    st.session_state["actual_duration"] = time.time() - run_start
    st.session_state["estimated_duration"] = estimated_seconds

if resume:
    # Same session-state shape as a fresh run, minus re-transcribing/
    # re-aligning -- reuse what was saved and only redo the cheap step
    # (audio clipping) and whatever the book/session pages cache needs.
    for key in list(st.session_state.keys()):
        if key.startswith("text_") or key.startswith("keep_"):
            del st.session_state[key]
    st.session_state.pop("pdf_bytes", None)
    st.session_state.pop("annotated_bottom_bytes", None)
    st.session_state.pop("annotated_inserted_bytes", None)

    pages, _ = get_book_pages(pdf_path, saved_run.get("quality_threshold", quality_threshold))
    result = saved_run["transcript"]
    comments = saved_run["comments"]  # raw PDF-page-index space; see run's comment above

    review_state = load_review_state(session_dir)
    if review_state:
        for i_str, r in review_state["review"].items():
            st.session_state[f"keep_{i_str}"] = r["keep"]
            st.session_state[f"text_{i_str}"] = r["text"]

    clips = [extract_clip(str(audio_path), c["start"], c["end"]) for c in comments]

    st.session_state["pages"] = pages
    st.session_state["transcript"] = result
    st.session_state["comments"] = comments
    st.session_state["clips"] = clips
    st.session_state["book_folder_name"] = book_dir.name
    st.session_state["book_title_ar"] = title_ar
    st.session_state["author_ar"] = author_ar
    st.session_state["commentator_ar"] = commentator_ar
    st.session_state["session_number"] = session_number
    st.session_state["pdf_path"] = str(pdf_path)
    st.session_state["actual_duration"] = None
    st.session_state["estimated_duration"] = None

if "comments" in st.session_state:
    # st.session_state["comments"] stays in raw PDF-page-index space (see
    # the comment where it's saved, above); shift to the book's own
    # printed page numbers here, reading "Page offset" live on every
    # rerun, so dragging that field updates every page number shown
    # immediately -- no need to Run/Resume again just to see a correction.
    comments = [
        {**c, "page": c["page"] + page_offset} if c.get("page") is not None else c
        for c in st.session_state["comments"]
    ]
    clips = st.session_state["clips"]

    actual = st.session_state["actual_duration"]
    if actual is not None:
        with st.container(border=True):
            estimated = st.session_state.get("estimated_duration")
            if estimated:
                col1, col2 = st.columns(2)
                col1.metric("Actual time taken", format_duration(actual))
                col2.metric("Estimated beforehand", format_duration(estimated))
            else:
                st.metric("Actual time taken", format_duration(actual))

    st.subheader(f"{len(comments)} candidate comment(s) — review below")
    st.caption(
        "Listen to each clip, fix the text if the transcript got something "
        "wrong, and uncheck anything that isn't actually a comment."
    )

    font_col, size_col = st.columns(2)
    font_col.selectbox(
        "Comment text font", list(COMMENT_FONT_OPTIONS), key="comment_font_family"
    )
    size_col.slider(
        "Comment text size", 0.9, 2.5, DEFAULT_COMMENT_FONT_SIZE, 0.1, key="comment_font_size"
    )

    for i, c in enumerate(comments):
        with st.container(border=True):
            page_note = f"page {c['page']}"
            if c.get("position_in_page") and c.get("page_word_count"):
                page_note += f" (word {c['position_in_page']}/{c['page_word_count']})"
            st.caption(
                f"{page_note} · {format_ts(c['start'])} – {format_ts(c['end'])} "
                f"· {c['n_words']} words"
            )
            if c.get("context_before"):
                st.caption(f"Text right before: …{c['context_before']}")
            st.audio(clips[i], format="audio/mp3")
            st.text_area(
                "Comment text",
                value=c["text"],
                key=f"text_{i}",
                height=100,
                label_visibility="collapsed",
            )
            st.checkbox(
                "Keep as a comment",
                value=c["n_words"] >= DEFAULT_UNCHECKED_BELOW_WORDS,
                key=f"keep_{i}",
            )

    # Snapshot review progress on every rerun (i.e. every checkbox/text
    # edit above) so it survives a server restart same as the run itself --
    # keyed off session_state's own record of which session this is, not
    # the live dropdowns, so it can't get written to the wrong session's
    # folder if the user changes the dropdown without re-running/resuming.
    saved_session_dir = (
        DATA_DIR / st.session_state["book_folder_name"] / str(st.session_state["session_number"])
    )
    save_review_state(saved_session_dir, len(comments))
    n_keep = sum(1 for i in range(len(comments)) if st.session_state.get(f"keep_{i}", True))
    st.caption(f"Progress saved automatically · {n_keep}/{len(comments)} marked keep")

    st.divider()

    # Shared by all three export styles below -- built fresh every rerun
    # (cheap: just reads already-rendered widget state) rather than only
    # inside a button's own if-block, so all three buttons see the same
    # up-to-date kept/edited comments regardless of which one is clicked.
    reviewed = [
        {
            "text": st.session_state[f"text_{i}"],
            "page": c["page"],
            "start": c["start"],
            "end": c["end"],
            "position_in_page": c.get("position_in_page"),
            "page_word_count": c.get("page_word_count"),
            "context_before": c.get("context_before"),
            "session_number": st.session_state.get("session_number"),
        }
        for i, c in enumerate(comments)
        if st.session_state.get(f"keep_{i}", True)
    ]
    file_stem = f"{st.session_state.get('book_folder_name', 'book')}_majlis{st.session_state.get('session_number', '')}"

    if st.button("Generate PDF report", type="primary"):
        st.session_state["pdf_bytes"] = build_pdf(
            reviewed,
            book_title_ar=st.session_state.get("book_title_ar", ""),
            author_ar=st.session_state.get("author_ar", ""),
            commentator_ar=st.session_state.get("commentator_ar", ""),
            club_image_bytes=CLUB_IMAGE_BYTES,
            telegram_icon_bytes=TELEGRAM_ICON_BYTES,
            facebook_icon_bytes=FACEBOOK_ICON_BYTES,
        )
        st.session_state["pdf_count"] = len(reviewed)
        save_and_open_pdf(st.session_state["pdf_bytes"], f"{file_stem}_report.pdf")

    if "pdf_bytes" in st.session_state:
        st.download_button(
            f"Download comments_report.pdf ({st.session_state['pdf_count']} kept)",
            st.session_state["pdf_bytes"],
            file_name=f"{file_stem}_report.pdf",
            mime="application/pdf",
        )
        st.caption(f"Saved to `output/{file_stem}_report.pdf` and opened in your PDF viewer.")

    st.caption(
        "Or annotate the book PDF itself instead of a separate report -- a "
        "small numbered marker at each comment's spot, with the comment "
        "text either appended below that page or on a new page right after it."
    )
    if not reviewed:
        st.caption(
            "No comments are currently checked \"Keep as a comment\" above -- "
            "the annotated PDF below would have no markers at all."
        )
    annot_col1, annot_col2 = st.columns(2)
    # Fall back to the live dropdown-resolved path (computed further up on
    # every rerun regardless of session_state) rather than only the stored
    # copy -- a browser session whose "comments" were populated by a run
    # from before pdf_path started being stored here would otherwise see
    # these buttons silently disabled. page_offset has no such fallback to
    # consider: `reviewed`'s page numbers above were just computed with the
    # live value, so build_annotated_pdf must be told that same value to
    # correctly reverse it back to a PDF page.
    annotated_book_pdf_path = st.session_state.get("pdf_path") or (str(pdf_path) if pdf_path else None)
    annotated_page_offset = page_offset

    if annot_col1.button("Generate annotated PDF (bottom of page)", disabled=not annotated_book_pdf_path):
        with st.spinner("Rendering annotated PDF..."):
            st.session_state["annotated_bottom_bytes"] = build_annotated_pdf(
                annotated_book_pdf_path,
                st.session_state["pages"],
                reviewed,
                annotated_page_offset,
                style="bottom",
                comment_font_size=comment_pdf_font_size,
                book_title_ar=st.session_state.get("book_title_ar", ""),
                author_ar=st.session_state.get("author_ar", ""),
                commentator_ar=st.session_state.get("commentator_ar", ""),
                club_image_bytes=CLUB_IMAGE_BYTES,
                telegram_icon_bytes=TELEGRAM_ICON_BYTES,
                facebook_icon_bytes=FACEBOOK_ICON_BYTES,
            )
        save_and_open_pdf(st.session_state["annotated_bottom_bytes"], f"{file_stem}_annotated_bottom.pdf")
    if "annotated_bottom_bytes" in st.session_state:
        annot_col1.download_button(
            "Download annotated_bottom.pdf",
            st.session_state["annotated_bottom_bytes"],
            file_name=f"{file_stem}_annotated_bottom.pdf",
            mime="application/pdf",
        )
        annot_col1.caption(f"Saved to `output/{file_stem}_annotated_bottom.pdf` and opened.")

    if annot_col2.button("Generate annotated PDF (separate page)", disabled=not annotated_book_pdf_path):
        with st.spinner("Rendering annotated PDF..."):
            st.session_state["annotated_inserted_bytes"] = build_annotated_pdf(
                annotated_book_pdf_path,
                st.session_state["pages"],
                reviewed,
                annotated_page_offset,
                style="inserted",
                comment_font_size=comment_pdf_font_size,
                book_title_ar=st.session_state.get("book_title_ar", ""),
                author_ar=st.session_state.get("author_ar", ""),
                commentator_ar=st.session_state.get("commentator_ar", ""),
                club_image_bytes=CLUB_IMAGE_BYTES,
                telegram_icon_bytes=TELEGRAM_ICON_BYTES,
                facebook_icon_bytes=FACEBOOK_ICON_BYTES,
            )
        save_and_open_pdf(st.session_state["annotated_inserted_bytes"], f"{file_stem}_annotated_inserted.pdf")
    if "annotated_inserted_bytes" in st.session_state:
        annot_col2.download_button(
            "Download annotated_inserted.pdf",
            st.session_state["annotated_inserted_bytes"],
            file_name=f"{file_stem}_annotated_inserted.pdf",
            mime="application/pdf",
        )
        annot_col2.caption(f"Saved to `output/{file_stem}_annotated_inserted.pdf` and opened.")

    st.divider()
    dl1, dl2, dl3 = st.columns(3)
    dl1.download_button(
        "Download comments.json",
        json.dumps(comments, ensure_ascii=False, indent=2),
        file_name=f"{file_stem}_comments.json",
        mime="application/json",
    )
    dl2.download_button(
        "Download transcript.json",
        json.dumps(st.session_state["transcript"], ensure_ascii=False, indent=2),
        file_name=f"{file_stem}_transcript.json",
        mime="application/json",
    )
    dl3.download_button(
        "Download book_pages.json",
        json.dumps(st.session_state["pages"], ensure_ascii=False, indent=2),
        file_name=f"{file_stem}_book_pages.json",
        mime="application/json",
    )

st.divider()
with st.expander("Whole-book annotated PDF (all sessions)"):
    st.caption(
        "Combines every session under this book folder into one annotated "
        "copy of the whole book PDF, not just the session picked above. "
        "Sessions with a saved run already (from a previous \"Run "
        "pipeline\"/\"Resume\" here, or a previous whole-book pass) are "
        "reused as-is, including any manual review edits saved for them; "
        "any session that's never been run gets transcribed and aligned "
        "here from scratch. That can take a very long time for several "
        "un-processed long sessions -- transcription is CPU-only and is by "
        "far the slowest step (see the per-session time estimate above) -- "
        "so this is meant to be started and left running, not waited on."
    )
    book_pdf_path = find_pdf(book_dir)
    book_session_dirs = list_session_dirs(book_dir)
    if book_pdf_path is None:
        st.caption("No book-level PDF found in this book's folder.")
    elif not book_session_dirs:
        st.caption("No numbered session folders found in this book's folder.")
    else:
        # Processing (transcribe/align every session) and rendering (turn
        # the resulting comments into a PDF in one style or the other) are
        # separate concerns -- processing is expensive and style-agnostic,
        # so it happens once here, and both "Generate PDF" buttons below
        # just re-render already-collected comments, not re-sweep sessions.
        if st.button("Process all sessions"):
            for a_session_dir in book_session_dirs:
                with st.status(f"Session {a_session_dir.name}...") as status:
                    state = ensure_session_run(
                        a_session_dir, book_dir, quality_threshold, model_size, min_words
                    )
                    if state is None:
                        status.update(
                            label=f"Session {a_session_dir.name}: skipped (no audio/PDF found)",
                            state="error",
                        )
                    else:
                        status.update(
                            label=f"Session {a_session_dir.name}: "
                            f"{len(state['comments'])} candidate comment(s)",
                            state="complete",
                        )
            book_pages, _ = get_book_pages(book_pdf_path, quality_threshold)
            # Kept raw (no page_offset applied) for the same reason
            # st.session_state["comments"] is above: so the "Page offset"
            # field stays live for this too, instead of the whole sweep
            # needing to be redone just to pick up a correction.
            st.session_state["whole_book_pages"] = book_pages
            st.session_state["whole_book_comments"] = load_reviewed_comments_for_book(book_dir, page_offset=0)
            st.session_state["whole_book_pdf_path"] = str(book_pdf_path)
            st.session_state.pop("whole_book_bottom_bytes", None)
            st.session_state.pop("whole_book_inserted_bytes", None)

        if "whole_book_comments" not in st.session_state:
            st.caption("Click \"Process all sessions\" to collect comments before generating a PDF.")
        else:
            raw_comments = st.session_state["whole_book_comments"]
            offset_comments = [
                {**c, "page": c["page"] + page_offset} if c.get("page") is not None else c
                for c in raw_comments
            ]
            st.caption(f"{len(raw_comments)} comment(s) ready across {len(book_session_dirs)} session(s).")

            whole_col1, whole_col2 = st.columns(2)
            if whole_col1.button("Generate PDF (bottom of page)"):
                with st.spinner(f"Rendering whole-book annotated PDF ({len(offset_comments)} comment(s))..."):
                    st.session_state["whole_book_bottom_bytes"] = build_annotated_pdf(
                        st.session_state["whole_book_pdf_path"],
                        st.session_state["whole_book_pages"],
                        offset_comments,
                        page_offset,
                        style="bottom",
                        comment_font_size=comment_pdf_font_size,
                        book_title_ar=title_ar,
                        author_ar=author_ar,
                        commentator_ar=commentator_ar,
                        club_image_bytes=CLUB_IMAGE_BYTES,
                        telegram_icon_bytes=TELEGRAM_ICON_BYTES,
                        facebook_icon_bytes=FACEBOOK_ICON_BYTES,
                    )
                save_and_open_pdf(
                    st.session_state["whole_book_bottom_bytes"],
                    f"{book_dir.name}_annotated_bottom.pdf",
                )
            if "whole_book_bottom_bytes" in st.session_state:
                whole_col1.download_button(
                    f"Download {book_dir.name}_annotated_bottom.pdf ({len(raw_comments)} comments)",
                    st.session_state["whole_book_bottom_bytes"],
                    file_name=f"{book_dir.name}_annotated_bottom.pdf",
                    mime="application/pdf",
                )
                whole_col1.caption(f"Saved to `output/{book_dir.name}_annotated_bottom.pdf` and opened.")

            if whole_col2.button("Generate PDF (separate page)"):
                with st.spinner(f"Rendering whole-book annotated PDF ({len(offset_comments)} comment(s))..."):
                    st.session_state["whole_book_inserted_bytes"] = build_annotated_pdf(
                        st.session_state["whole_book_pdf_path"],
                        st.session_state["whole_book_pages"],
                        offset_comments,
                        page_offset,
                        style="inserted",
                        comment_font_size=comment_pdf_font_size,
                        book_title_ar=title_ar,
                        author_ar=author_ar,
                        commentator_ar=commentator_ar,
                        club_image_bytes=CLUB_IMAGE_BYTES,
                        telegram_icon_bytes=TELEGRAM_ICON_BYTES,
                        facebook_icon_bytes=FACEBOOK_ICON_BYTES,
                    )
                save_and_open_pdf(
                    st.session_state["whole_book_inserted_bytes"],
                    f"{book_dir.name}_annotated_inserted.pdf",
                )
            if "whole_book_inserted_bytes" in st.session_state:
                whole_col2.download_button(
                    f"Download {book_dir.name}_annotated_inserted.pdf ({len(raw_comments)} comments)",
                    st.session_state["whole_book_inserted_bytes"],
                    file_name=f"{book_dir.name}_annotated_inserted.pdf",
                    mime="application/pdf",
                )
                whole_col2.caption(f"Saved to `output/{book_dir.name}_annotated_inserted.pdf` and opened.")
