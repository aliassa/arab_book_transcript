"""
AI cleanup of Whisper mis-transcriptions in extracted comments.

Sends one comment's text at a time to Claude and asks for *only*
very-high-confidence fixes: garbled Quran/hadith quotes, misheard proper
names and book titles, and clearly misheard fusha words — never dialect,
never the speaker's own grammar, never content. This automates the manual
review pass that kept finding the same classes of error (see git history:
session 1 of hosn_thann_billah needed ~70 such fixes by hand).

Called from app.py's review UI ("Fix obvious transcription errors" button);
kept out of app.py like the other pipeline stages so the logic is reusable
and testable without Streamlit.

Auth: anthropic.Anthropic() resolves credentials from the environment
(ANTHROPIC_API_KEY, or an `ant auth login` profile) — no key is stored here.
"""

import anthropic

MODEL = "claude-opus-4-8"

# One comment can be 10+ minutes of speech (thousands of words), so the
# output ceiling is set high and requests are streamed — a non-streaming
# request that large risks hitting the SDK's HTTP timeout.
MAX_TOKENS = 64000

SYSTEM_PROMPT = """\
You are correcting Arabic speech-to-text output (Whisper) from a recorded \
reading-club session. The reader reads from a classical/religious book and \
adds his own spoken commentary; the user message is one passage of that \
transcript.

Fix ONLY unambiguous transcription errors where the intended wording is \
certain from context:
- Garbled Quran, hadith, and well-known du'aa quotes (restore the canonical \
wording of the quoted span only).
- Misheard proper names, book titles, and technical/religious terms (e.g. \
"مداري جسالكين" → "مدارج السالكين").
- Clearly misheard standard-Arabic words where only one reading makes sense \
(e.g. "أسخل السجن" → "أدخل السجن").

PRESERVE exactly as written:
- Dialectal/colloquial words and phrases the speaker actually said.
- The speaker's own spoken grammar, repetitions, self-corrections, and \
disfluencies.
- All content: never add, remove, reorder, summarize, or complete anything.
- Any stretch that is garbled beyond confident reconstruction — leave it \
unchanged rather than guessing.

If you are not certain about a word, leave it unchanged. Reply with ONLY \
the corrected text — no commentary, no quotation marks around it, no notes.\
"""


def acceptable_correction(original: str, corrected: str) -> bool:
    """
    Sanity guard on a returned correction: real fixes are word-for-word
    substitutions, so the word count should barely move. A big shift means
    the model summarized, expanded, or answered with commentary instead of
    the corrected text -- keep the original in that case.
    """
    n_original = len(original.split())
    n_corrected = len(corrected.split())
    if n_original == 0 or n_corrected == 0:
        return False
    return 0.8 <= n_corrected / n_original <= 1.2


def correct_text(text: str, client=None, model: str = MODEL) -> str:
    """
    Returns the corrected text for one comment, or the original text
    unchanged if the model declined or the reply fails the sanity guard.
    """
    if not text.strip():
        return text
    if client is None:
        client = anthropic.Anthropic()
    with client.messages.stream(
        model=model,
        max_tokens=MAX_TOKENS,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason != "end_turn":
        # max_tokens (truncated) or refusal -- an incomplete correction is
        # worse than no correction, since the guard below only checks size.
        return text
    corrected = "".join(b.text for b in message.content if b.type == "text").strip()
    if not acceptable_correction(text, corrected):
        return text
    return corrected
