"""The embedding model wrapper.

Kept behind a tiny protocol so everything above it -- the job, the search
service, their tests -- can run against a fake encoder and never download a
model. Only this module imports sentence-transformers, and it does so lazily
so importing the app does not pull in torch.

Production model: sentence-transformers/all-mpnet-base-v2.
  - 768 dimensions, matching the `embeddings.vector` column.
  - CPU-practical (~420MB): ~180s to embed the current corpus, ~40-70ms
    end-to-end query latency. No GPU required anywhere.
  - Trained for sentence-level semantic similarity, which is the task here.
Promoted from all-MiniLM-L6-v2 (384-d) on the evidence in Phase 1F, where it
improved 5 of 6 fixed evaluation queries and returned qualitatively better
passages rather than merely higher scores. The name and width are
configuration, not constants scattered through the code; changing the width
requires a migration because the pgvector column is typed.

Vectors are L2-normalized, so cosine distance and inner product agree and
similarity can be reported as 1 - cosine_distance.
"""

from typing import Protocol, runtime_checkable

from app.core.config import get_settings


@runtime_checkable
class Encoder(Protocol):
    """What the rest of the system needs from an embedding model."""

    model_name: str
    model_revision: str | None
    dimension: int
    normalized: bool

    def encode(self, texts: list[str]) -> list[list[float]]: ...

    def count_tokens(self, text: str) -> int: ...

    @property
    def max_sequence_length(self) -> int: ...


class SentenceTransformerEncoder:
    """Loads the model once and reuses it for every batch."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str = "cpu",
        dimension: int | None = None,
    ) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model_name
        # Defaults to the production column width. A candidate model under
        # evaluation declares its own, and the check in `_load` still refuses
        # a model whose real output width disagrees with what was declared.
        self.dimension = dimension or settings.embedding_dimensions
        self.normalized = True
        self._device = device
        self._model = None
        # Resolved eagerly, not on load: staleness is checked *before* any
        # encoding happens, and a revision that only appeared after the model
        # loaded would make every stored vector look stale on every run.
        self.model_revision: str | None = self._resolve_revision()

    def _load(self):
        if self._model is None:
            # Imported here so `import app.main` does not drag in torch.
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self._device)
            reported = self._model.get_sentence_embedding_dimension()
            if reported != self.dimension:
                raise ValueError(
                    f"model {self.model_name} produces {reported} dimensions, "
                    f"but the embeddings column expects {self.dimension}"
                )
        return self._model

    def _resolve_revision(self) -> str | None:
        """Best-effort build identifier for the loaded model."""
        try:
            from sentence_transformers import __version__ as st_version

            return f"sentence-transformers/{st_version}"
        except Exception:
            return None

    @property
    def max_sequence_length(self) -> int:
        return int(self._load().max_seq_length)

    def count_tokens(self, text: str) -> int:
        tokenizer = self._load().tokenizer
        return len(tokenizer.encode(text, add_special_tokens=True))

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=self.normalized,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]


def get_encoder(model_name: str | None = None, dimension: int | None = None) -> Encoder:
    return SentenceTransformerEncoder(model_name=model_name, dimension=dimension)
