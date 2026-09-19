"""Deterministic text preparation for embedding input.

Light on purpose. The stored `ContentUnit.text_content` remains the
authoritative text; this only removes artifacts that would otherwise make
the same prose hash differently or waste tokens. It never rewrites,
summarizes, translates, or otherwise editorialises the prose, and no LLM is
involved.

`PREP_VERSION` is recorded on every embedding. Changing anything in this
module means bumping it, which marks existing vectors stale.
"""

import hashlib
import re
import unicodedata

PREP_VERSION = 1

_WS_RUN_RE = re.compile(r"[ \t ]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
# Zero-width and bidi marks: invisible, but they change a hash and a token count.
_INVISIBLE_RE = re.compile(r"[​-‏‪-‮﻿]")


class EmptyTextError(ValueError):
    """The text has no content to embed."""


def prepare_text(raw: str | None) -> str:
    """Normalize text for embedding. Raises when nothing is left to embed."""
    if raw is None:
        raise EmptyTextError("text is None")

    # NFC keeps composed characters stable so the same prose hashes alike
    # whatever produced it.
    text = unicodedata.normalize("NFC", raw)
    text = _INVISIBLE_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RUN_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES_RE.sub("\n\n", text)
    text = text.strip()

    if not text:
        raise EmptyTextError("text is empty after normalization")
    return text


def text_hash(prepared: str) -> str:
    """SHA-256 of prepared text, used to detect that a source has changed."""
    return hashlib.sha256(prepared.encode("utf-8")).hexdigest()
