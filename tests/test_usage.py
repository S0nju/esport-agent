from anthropic.types import Message

from esport_agent.usage import Usage, price_for


def make_message(model: str, input_tokens: int, output_tokens: int) -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


def test_price_for_matches_the_longest_prefix() -> None:
    haiku = price_for("claude-haiku-4-5-20251001")
    opus_5 = price_for("claude-opus-5")
    opus_5_5 = price_for("claude-opus-5-5")

    assert haiku is not None and haiku.input_per_mtok == 1.0
    assert opus_5 is not None and opus_5.input_per_mtok == 5.0
    assert opus_5_5 is not None and opus_5_5.input_per_mtok == 4.0
    assert price_for("some-other-model") is None


def test_usage_accumulates_calls() -> None:
    usage = Usage()

    usage.record(make_message("claude-haiku-4-5-20251001", 1_500, 100))
    usage.record(make_message("claude-haiku-4-5-20251001", 1_912, 87))

    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (2, 3_412, 187)
    assert usage.estimated_cost_usd == (3_412 * 1.0 + 187 * 5.0) / 1_000_000
    assert usage.summary() == (
        "claude-haiku-4-5-20251001 · 2 calls · 3,412 in / 187 out tokens · ≈ $0.0043"
    )


def test_summary_for_an_unknown_model() -> None:
    usage = Usage()
    usage.record(make_message("custom-model", 10, 5))

    assert usage.estimated_cost_usd is None
    assert usage.summary() == "custom-model · 1 call · 10 in / 5 out tokens · cost unknown"
