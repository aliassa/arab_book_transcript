"""
Streamlit UI for the reading-club pipeline.

Wraps the same three stages used from the command line (extract_book,
transcribe, align) so both entry points stay in sync -- this file adds
no pipeline logic of its own, only file handling, progress display, and
result rendering.

Run with: streamlit run app.py
"""

import json
import tempfile
from pathlib import Path

import streamlit as st

from align import extract_comments_from_transcript
from extract_book import extract_book
from transcribe import MODEL_SIZE, load_model, transcribe

MODEL_SIZES = ["large-v3", "medium", "small", "base", "tiny"]

st.set_page_config(page_title="Reading Club — Comment Extractor", layout="centered")


@st.cache_resource(show_spinner=False)
def cached_model(model_size: str):
    return load_model(model_size)


def format_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


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
            book_text = "\n".join(p["text"] for p in pages)
            comments = extract_comments_from_transcript(
                book_text, result["segments"], min_words=min_words
            )
            status.update(
                label=f"Found {len(comments)} candidate comment(s)", state="complete"
            )

        st.session_state["pages"] = pages
        st.session_state["transcript"] = result
        st.session_state["comments"] = comments

if "comments" in st.session_state:
    comments = st.session_state["comments"]
    st.subheader(f"{len(comments)} candidate comment(s)")

    for c in comments:
        with st.container(border=True):
            st.caption(f"{format_ts(c['start'])} – {format_ts(c['end'])}  ·  {c['n_words']} words")
            st.markdown(
                f'<div dir="rtl" style="text-align:right; font-size:1.3rem; '
                f'line-height:2;">{c["text"]}</div>',
                unsafe_allow_html=True,
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
