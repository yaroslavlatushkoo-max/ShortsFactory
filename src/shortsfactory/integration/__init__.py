"""Integrated text -> storyboard -> motion plan -> rendered Shorts pipeline.

The package deliberately keeps the three projects separate:

* Story Studio is executed in its own Python environment through a JSON worker.
* Animation Intelligence Lab is executed in its own Python environment through a
  JSON worker.
* ShortsFactory owns the orchestration, the adapter layer and the real renderer.

Only plain JSON crosses the process boundaries; no project imports another
project's package into ShortsFactory.
"""

from __future__ import annotations

from .contracts import IntegrationError
from .pipeline import IntegratedResult, run_integrated_create

__all__ = ["IntegrationError", "IntegratedResult", "run_integrated_create"]
