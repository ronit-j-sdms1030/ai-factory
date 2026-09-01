"""Bounding the intake transcript handed to the BRD agent.

Carried over from ``capHistory`` in the JavaScript. The requirement already
distills the conversation, so the transcript is supporting context — an
unusually long intake should not scale prompt size and generation time with
it. The port dropped this and passed the whole history, unbounded, while the
agent documentation already claimed a 30-message cap.
"""

from __future__ import annotations

from app.agents.brd import MAX_HISTORY_MESSAGES, cap_history


def _history(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 else "assistant", "content": f"m{i}"} for i in range(n)]


class TestShortConversations:
    def test_a_conversation_under_the_cap_is_untouched(self):
        history = _history(10)
        assert cap_history(history) is history

    def test_a_conversation_exactly_at_the_cap_is_untouched(self):
        history = _history(MAX_HISTORY_MESSAGES)
        assert cap_history(history) is history


class TestTrimming:
    def test_the_middle_is_dropped_not_either_end(self):
        """Both ends carry information the other does not.

        The opening messages set the original framing; the closing ones hold
        the refined final answers. Trimming from one end loses one of them.
        """
        capped = cap_history(_history(100), maximum=10)
        contents = [m["content"] for m in capped]

        assert "m0" in contents and "m1" in contents      # framing kept
        assert "m98" in contents and "m99" in contents    # refined answers kept
        assert "m50" not in contents                      # middle dropped

    def test_the_gap_is_marked_rather_than_spliced_silently(self):
        capped = cap_history(_history(100), maximum=10)
        markers = [m for m in capped if "omitted for length" in m["content"]]

        assert len(markers) == 1
        assert "90 earlier messages" in markers[0]["content"]

    def test_the_marker_sits_between_the_two_halves(self):
        capped = cap_history(_history(100), maximum=10)
        contents = [m["content"] for m in capped]
        marker = next(i for i, c in enumerate(contents) if "omitted" in c)

        assert contents[marker - 1] == "m1"
        assert contents[marker + 1] == "m92"

    def test_output_length_stays_bounded(self):
        """One extra entry for the marker, and no more."""
        assert len(cap_history(_history(500), maximum=10)) == 11
