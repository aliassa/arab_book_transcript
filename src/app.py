"""
Streamlit UI for the reading-club pipeline.

Wraps the same three stages used from the command line (extract_book,
transcribe, align) so both entry points stay in sync -- this file adds
no pipeline logic of its own beyond per-comment audio clipping and PDF
export, which only exist here (reviewing by ear/eye is a UI-only step).

Run with: streamlit run app.py
"""

import json
import subprocess
import tempfile
from pathlib import Path

import streamlit as st

from align import extract_comments_from_transcript
from export_pdf import build_pdf
from extract_book import extract_book
from transcribe import MODEL_SIZE, load_model, transcribe

MODEL_SIZES = ["large-v3", "medium", "small", "base", "tiny"]

st.set_page_config(page_title="Reading Club — Comment Extractor", layout="centered")
st.markdown(
    "<style>.stTextArea textarea { direction: rtl; text-align: right; font-size: 1.1rem; }</style>",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def cached_model(model_size: str):
    return load_model(model_size)


def format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


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
    "Upload a book PDF and a session recording. The pipeline OCRs/extracts "
    "the book text, transcribes the audio, and diffs the two to surface the "
    "reader's own comments."
)

pdf_file = st.file_uploader("Book (PDF)", type=["pdf"])
audio_file = st.file_uploader("Session audio", type=["ogg", "mp3", "wav", "m4a", "flac"])

with st.expander("Advanced options"):
    min_words = st.slider("Minimum comment length (words)", 3, 15, 5)
    model_size = st.selectbox(
        "Whisper model size", MODEL_SIZES, index=MODEL_SIZES.index(MODEL_SIZE)
    )
    quality_threshold = st.slider("OCR fallback quality threshold", 0.0, 1.0, 0.6, 0.05)

run = st.button("Run pipeline", type="primary", disabled=not (pdf_file and audio_file))

if run:
    # Drop any leftover per-comment widget state / generated PDF from a
    # previous run so stale edits don't leak into a new one's results.
    for key in list(st.session_state.keys()):
        if key.startswith("text_") or key.startswith("keep_"):
            del st.session_state[key]
    st.session_state.pop("pdf_bytes", None)

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / pdf_file.name
        pdf_path.write_bytes(pdf_file.getvalue())
        audio_path = Path(tmp) / audio_file.name
        audio_path.write_bytes(audio_file.getvalue())

        with st.status("Extracting book text...") as status:
            pages = extract_book(str(pdf_path), quality_threshold=quality_threshold)
            n_ocr = sum(1 for p in pages if p["method"] == "ocr")
            status.update(
                label=f"Book extracted — {len(pages)} page(s), {n_ocr} needed OCR",
                state="complete",
            )

        with st.status("Loading Whisper model...") as status:
            model = cached_model(model_size)
            status.update(label=f"Model ready ({model_size})", state="complete")

        with st.status("Transcribing audio (this can take a while)...") as status:
            result = transcribe(str(audio_path), model_size=model_size, model=model)
            status.update(
                label=f"Transcribed {result['duration']:.0f}s of audio",
                state="complete",
            )

        with st.status("Aligning transcript against book text...") as status:
            comments = extract_comments_from_transcript(
                pages, result["segments"], min_words=min_words
            )
            status.update(
                label=f"Found {len(comments)} candidate comment(s)", state="complete"
            )

        with st.status("Extracting comment audio clips...") as status:
            clips = [extract_clip(str(audio_path), c["start"], c["end"]) for c in comments]
            status.update(label=f"Extracted {len(clips)} audio clip(s)", state="complete")

        st.session_state["pages"] = pages
        st.session_state["transcript"] = result
        st.session_state["comments"] = comments
        st.session_state["clips"] = clips
        st.session_state["book_title"] = Path(pdf_file.name).stem

if "comments" in st.session_state:
    comments = st.session_state["comments"]
    clips = st.session_state["clips"]
    st.subheader(f"{len(comments)} candidate comment(s) — review below")
    st.caption(
        "Listen to each clip, fix the text if the transcript got something "
        "wrong, and uncheck anything that isn't actually a comment."
    )

    for i, c in enumerate(comments):
        with st.container(border=True):
            st.caption(
                f"page {c['page']} · {format_ts(c['start'])} – {format_ts(c['end'])} "
                f"· {c['n_words']} words"
            )
            st.audio(clips[i], format="audio/mp3")
            st.text_area(
                "Comment text",
                value=c["text"],
                key=f"text_{i}",
                height=100,
                label_visibility="collapsed",
            )
            st.checkbox("Keep as a comment", value=True, key=f"keep_{i}")

    st.divider()

    if st.button("Generate PDF report", type="primary"):
        reviewed = [
            {
                "text": st.session_state[f"text_{i}"],
                "page": c["page"],
                "start": c["start"],
                "end": c["end"],
            }
            for i, c in enumerate(comments)
            if st.session_state.get(f"keep_{i}", True)
        ]
        st.session_state["pdf_bytes"] = build_pdf(
            reviewed, book_title=st.session_state.get("book_title", "")
        )
        st.session_state["pdf_count"] = len(reviewed)

    if "pdf_bytes" in st.session_state:
        st.download_button(
            f"Download comments_report.pdf ({st.session_state['pdf_count']} kept)",
            st.session_state["pdf_bytes"],
            file_name="comments_report.pdf",
            mime="application/pdf",
        )

    st.divider()
    dl1, dl2, dl3 = st.columns(3)
    dl1.download_button(
        "Download comments.json",
        json.dumps(comments, ensure_ascii=False, indent=2),
        file_name="comments.json",
        mime="application/json",
    )
    dl2.download_button(
        "Download transcript.json",
        json.dumps(st.session_state["transcript"], ensure_ascii=False, indent=2),
        file_name="transcript.json",
        mime="application/json",
    )
    dl3.download_button(
        "Download book_pages.json",
        json.dumps(st.session_state["pages"], ensure_ascii=False, indent=2),
        file_name="book_pages.json",
        mime="application/json",
    )
