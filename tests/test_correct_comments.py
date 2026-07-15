from contextlib import contextmanager
from types import SimpleNamespace

from correct_comments import MAX_TOKENS, acceptable_correction, correct_text


# -- acceptable_correction ---------------------------------------------------


def test_word_for_word_substitution_accepted():
    assert acceptable_correction("فاغوا إلى الكهف", "فأووا إلى الكهف")


def test_identical_text_accepted():
    assert acceptable_correction("نص بلا أخطاء", "نص بلا أخطاء")


def test_empty_reply_rejected():
    assert not acceptable_correction("نص أصلي", "")


def test_summary_much_shorter_rejected():
    original = "كلمة " * 100
    assert not acceptable_correction(original, "ملخص قصير")


def test_expansion_much_longer_rejected():
    corrected = "كلمة " * 100
    assert not acceptable_correction("نص قصير هنا", corrected)


# -- correct_text (stubbed client, no network) -------------------------------


class FakeClient:
    """Mimics the one SDK surface correct_text touches:
    client.messages.stream(...) as a context manager whose
    get_final_message() returns a message with content blocks."""

    def __init__(self, reply_text, stop_reason="end_turn"):
        self.calls = []
        outer = self

        message = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="text", text=reply_text)],
        )

        @contextmanager
        def stream(**kwargs):
            outer.calls.append(kwargs)
            yield SimpleNamespace(get_final_message=lambda: message)

        self.messages = SimpleNamespace(stream=stream)


def test_returns_corrected_text():
    client = FakeClient("فأووا إلى الكهف")
    assert correct_text("فاغوا إلى الكهف", client=client) == "فأووا إلى الكهف"


def test_sends_original_text_as_user_message():
    client = FakeClient("رد")
    correct_text("النص الأصلي المرسل للتصحيح", client=client)
    (call,) = client.calls
    assert call["messages"] == [{"role": "user", "content": "النص الأصلي المرسل للتصحيح"}]
    assert call["max_tokens"] == MAX_TOKENS


def test_rewrite_failing_guard_returns_original():
    client = FakeClient("ملخص")  # way shorter than the original
    original = "كلمة " * 50
    assert correct_text(original, client=client) == original


def test_truncated_reply_returns_original():
    client = FakeClient("نص مقطوع في المنتصف", stop_reason="max_tokens")
    assert correct_text("نص مقطوع في المنتصف تقريبا", client=client) == "نص مقطوع في المنتصف تقريبا"


def test_empty_input_short_circuits_without_calling_api():
    client = FakeClient("anything")
    assert correct_text("   ", client=client) == "   "
    assert client.calls == []
