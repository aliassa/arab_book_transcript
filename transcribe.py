"""
Audio transcription for the reading-club pipeline.

Transcribes a reading-club session recording using faster-whisper.
word_timestamps is enabled because the alignment stage needs per-word
timing to derive speaking-rhythm features (rate, pauses) as a secondary
signal for distinguishing reader comments from book text being read aloud.

Output: JSON with the full text plus segment/word-level timestamps.
"""

import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel

MODEL_SIZE = "large-v3"


def transcribe(audio_path: str, model_size: str = MODEL_SIZE) -> dict:
    model = WhisperModel(model_size, device="auto", compute_type="auto")

    segments, info = model.transcribe(
        audio_path,
        language="ar",
        word_timestamps=True,
        vad_filter=True,
    )

    segments_out = []
    full_text_parts = []

    for seg in segments:
        words_out = [
            {
                "word": w.word,
                "start": round(w.start, 3),
                "end": round(w.end, 3),
                "probability": round(w.probability, 3),
            }
            for w in (seg.words or [])
        ]
        segments_out.append(
            {
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": seg.text.strip(),
                "words": words_out,
            }
        )
        full_text_parts.append(seg.text.strip())
        print(
            f"  [{seg.start:7.2f}-{seg.end:7.2f}] {seg.text.strip()}",
            file=sys.stderr,
        )

    return {
        "audio_path": audio_path,
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 3),
        "segments": segments_out,
        "text": " ".join(full_text_parts).strip(),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <audio_file> [output.json]")
        sys.exit(1)

    audio_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else Path(audio_path).stem + "_transcript.json"

    print(f"Transcribing: {audio_path}", file=sys.stderr)
    result = transcribe(audio_path)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nDone. -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
