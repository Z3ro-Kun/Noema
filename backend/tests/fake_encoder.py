"""A deterministic stand-in for the embedding model.

Real vectors need a ~90MB download and torch; the test suite must not depend
on either. This produces stable, L2-normalized vectors from a hash of the
text, so identical text always embeds identically and different text lands
somewhere else. It is not semantically meaningful -- semantics are checked
by hand in the real-run validation, not by unit tests.
"""

import hashlib
import math

from app.core.config import get_settings


class FakeEncoder:
    def __init__(
        self,
        model_name: str = "fake/test-encoder",
        model_revision: str | None = "fake-1",
        # Defaults to the configured production width so vectors always fit
        # the typed pgvector column; a dimension change should not require
        # editing every test.
        dimension: int | None = None,
        normalized: bool = True,
        max_seq_length: int = 256,
    ) -> None:
        self.model_name = model_name
        self.model_revision = model_revision
        self.dimension = dimension or get_settings().embedding_dimensions
        self.normalized = normalized
        self._max_seq_length = max_seq_length
        self.encode_calls = 0
        self.encoded_batches: list[list[str]] = []

    @property
    def max_sequence_length(self) -> int:
        return self._max_seq_length

    def count_tokens(self, text: str) -> int:
        # Rough but deterministic: whitespace tokens plus two specials.
        return len(text.split()) + 2

    def _vector_for(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand the digest deterministically to the full dimension.
        raw = []
        counter = 0
        while len(raw) < self.dimension:
            block = hashlib.sha256(digest + counter.to_bytes(4, "big")).digest()
            raw.extend(block)
            counter += 1
        values = [(byte - 127.5) / 127.5 for byte in raw[: self.dimension]]

        if not self.normalized:
            return values
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.encode_calls += 1
        self.encoded_batches.append(list(texts))
        return [self._vector_for(text) for text in texts]


class WrongDimensionEncoder(FakeEncoder):
    """Returns vectors of the wrong width, to prove the guard works."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * (self.dimension - 1) for _ in texts]


class ExplodingEncoder(FakeEncoder):
    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model unavailable")
