"""Token usage and estimated cost of the Claude calls made to answer one question."""

from dataclasses import dataclass

from anthropic.types import Message


@dataclass(frozen=True)
class ModelPrice:
    input_per_mtok: float
    output_per_mtok: float


# USD per million tokens. Only used to display an estimate: the Usage page of the
# Anthropic console is the source of truth. Keys are model id prefixes.
PRICES: dict[str, ModelPrice] = {
    "claude-haiku-4-5": ModelPrice(1.0, 5.0),
    "claude-sonnet-4-6": ModelPrice(3.0, 15.0),
    "claude-sonnet-5": ModelPrice(2.0, 10.0),
    "claude-opus-5": ModelPrice(5.0, 25.0),
    "claude-opus-5-5": ModelPrice(4.0, 20.0),
}


def price_for(model: str) -> ModelPrice | None:
    """Return the price of `model`, matching the longest known id prefix."""
    matches = [prefix for prefix in PRICES if model.startswith(prefix)]
    return PRICES[max(matches, key=len)] if matches else None


@dataclass
class Usage:
    """Accumulated usage of the Claude calls made for one question."""

    model: str = ""
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def record(self, message: Message) -> None:
        """Add the usage of one API response."""
        self.model = message.model
        self.calls += 1
        self.input_tokens += message.usage.input_tokens
        self.output_tokens += message.usage.output_tokens

    @property
    def estimated_cost_usd(self) -> float | None:
        price = price_for(self.model)
        if price is None:
            return None
        return (
            self.input_tokens * price.input_per_mtok + self.output_tokens * price.output_per_mtok
        ) / 1_000_000

    def summary(self) -> str:
        """One line such as `claude-haiku-4-5 · 2 calls · 3,412 in / 187 out tokens · ≈ $0.0044`."""
        cost = self.estimated_cost_usd
        cost_text = f"≈ ${cost:.4f}" if cost is not None else "cost unknown"
        calls = f"{self.calls} call{'s' if self.calls != 1 else ''}"
        tokens = f"{self.input_tokens:,} in / {self.output_tokens:,} out tokens"
        return f"{self.model} · {calls} · {tokens} · {cost_text}"
