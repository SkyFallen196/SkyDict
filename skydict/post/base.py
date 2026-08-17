"""Post-processing hook between recognition and delivery.

The pipeline always runs a processor. Today that is :class:`PassthroughProcessor`; the
LLM-backed "modes" (cleanup, email formatting, translation) plug in here later without
the pipeline changing shape.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..stt.base import TranscriptResult


@runtime_checkable
class PostProcessor(Protocol):
    name: str

    def process(self, result: TranscriptResult) -> str: ...


class PassthroughProcessor:
    """Returns the recognised text unchanged."""

    name = "raw"

    def process(self, result: TranscriptResult) -> str:
        return result.text
