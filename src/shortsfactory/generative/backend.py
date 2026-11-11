"""Video generation backend interface (Mission 3 §3D).

Provider-agnostic: submit/poll/fetch/cancel + capabilities + cost estimate.
No pipeline code depends on a specific API. Providers are selected by name and
configured via environment (see .env.example). The DisabledBackend is the
default and makes it impossible to silently pass procedural output off as
generative.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import BackendCapabilities, CostEstimate, GenerationJob, SceneGenerationSpec


class BackendError(RuntimeError):
    pass


class BackendNotConfigured(BackendError):
    pass


class BackendTimeout(BackendError):
    pass


class BackendVideoMissing(BackendError):
    pass


class ProviderError(BackendError):
    pass


def _load_dotenv(root: Path | None = None) -> None:
    """Minimal .env loader (no external dependency). Loads once."""
    if getattr(_load_dotenv, "_loaded", False):
        return
    candidates = []
    if root is not None:
        candidates.append(Path(root) / ".env")
    candidates.append(Path.cwd() / ".env")
    for path in candidates:
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
            break
    _load_dotenv._loaded = True  # type: ignore[attr-defined]


class VideoBackend(ABC):
    """Interface every provider implements."""

    name: str = "base"

    def __init__(self, env: dict[str, str] | None = None) -> None:
        self.env = dict(os.environ if env is None else env)

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """True when the required key/endpoint is present (without calling it)."""
        ...

    def estimate_cost(self, specs: list[SceneGenerationSpec]) -> CostEstimate | None:
        per = self.capabilities.cost_per_second_usd
        if per is None:
            return None
        total = sum(int(s.duration) for s in specs)
        return CostEstimate(
            estimated_usd=round(total * per, 4),
            provider=self.name,
            units="seconds",
            breakdown={"seconds": total, "rate_per_second": per},
        )

    def apply_model_override(self, model: str) -> None:
        """Per-run model override hook. Default no-op; providers opt in."""
        return None

    @abstractmethod
    def submit(self, spec: SceneGenerationSpec) -> GenerationJob:
        """Submit a job. Raises BackendNotConfigured if not configured."""
        ...

    @abstractmethod
    def poll(self, job: GenerationJob) -> GenerationJob:
        """Update job.status/detail. Raises BackendNotConfigured if not configured."""
        ...

    @abstractmethod
    def fetch(self, job: GenerationJob, dest_dir: Path) -> str:
        """Return a local file path for the finished clip."""
        ...

    def cancel(self, job: GenerationJob) -> None:
        """Best-effort cancel; defaults to no-op."""
        return None

    def run_to_completion(
        self,
        spec: SceneGenerationSpec,
        dest_dir: Path,
        poll_interval: float = 5.0,
        poll_timeout: float = 600.0,
    ) -> GenerationJob:
        if not self.is_configured():
            raise BackendNotConfigured(
                f"Провайдер «{self.name}» не настроен. Укажите ключ в .env "
                f"(см. .env.example) или выберите другой провайдер."
            )
        job = self.submit(spec)
        import time as _time

        deadline = _time.monotonic() + poll_timeout
        while True:
            job = self.poll(job)
            if job.status == "done":
                job.media = self.fetch(job, dest_dir)
                return job
            if job.status == "error":
                raise ProviderError(job.error or "Провайдер вернул ошибку")
            if _time.monotonic() > deadline:
                self.cancel(job)
                raise BackendTimeout(f"Таймаут ожидания рендера (>{poll_timeout:.0f} с)")
            _time.sleep(poll_interval)


# ---- Registry ----

_REGISTRY: dict[str, type[VideoBackend]] = {}


def register_backend(cls: type[VideoBackend]) -> type[VideoBackend]:
    _REGISTRY[cls.name] = cls
    return cls


def backend_names() -> list[str]:
    return sorted(_REGISTRY)


def create_backend(
    name: str,
    env: dict[str, str] | None = None,
    root: Path | None = None,
) -> VideoBackend:
    _load_dotenv(root)
    if not name or name == "disabled":
        return DisabledBackend(env)
    cls = _REGISTRY.get(name)
    if cls is None:
        raise BackendError(f"Неизвестный провайдер «{name}». Доступны: {', '.join(backend_names())}")
    return cls(env)


# ---- Default: disabled ----

@register_backend
class DisabledBackend(VideoBackend):
    name = "disabled"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(max_duration_s=0.0)

    def is_configured(self) -> bool:
        return False

    def submit(self, spec: SceneGenerationSpec) -> GenerationJob:
        raise BackendNotConfigured(
            "Генеративный видеобэкенд не настроен (provider=disabled). "
            "Задайте SHORTSFACTORY_VIDEO_PROVIDER и ключ в .env, либо используйте --mode procedural."
        )

    def poll(self, job: GenerationJob) -> GenerationJob:
        return self.submit(SceneGenerationSpec())  # type: ignore[return-value]  # pragma: no cover

    def fetch(self, job: GenerationJob, dest_dir: Path) -> str:
        raise BackendNotConfigured("Бэкенд отключён")
