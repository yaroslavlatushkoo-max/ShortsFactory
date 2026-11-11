"""Provider adapters for generative video backends."""

from __future__ import annotations

from . import fal, kling, veo  # noqa: F401  (registers providers)

__all__ = ["veo", "kling", "fal"]
