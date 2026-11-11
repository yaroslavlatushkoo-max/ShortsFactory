"""Video providers in the unified registry (Mission 4 §4B).

Wraps the Mission 3 ``VideoBackend`` adapters (veo/kling/fal) so the registry
can present them uniformly. The underlying ``VideoBackend`` interface is
unchanged and remains the object the pipeline actually drives.
"""

from __future__ import annotations

from typing import Any

from .backend import (
    VideoBackend,
    backend_names,
    create_backend,
)
from .registry import AIProvider, AvailabilityResult, Capability, registry

_VIDEO_CAP_MAP = {
    "supports_t2v": Capability.VIDEO_GENERATION,
    "supports_image_to_video": Capability.IMAGE_TO_VIDEO,
}

_PROVIDER_KEYS: dict[str, tuple[str, ...]] = {
    "veo": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "kling": ("KLING_API_KEY", "KLINGAI_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"),
    "fal": ("FAL_KEY", "FAL_API_KEY"),
}


class VideoBackendAIProvider(AIProvider):
    """Registry wrapper around a ``VideoBackend``."""

    def __init__(self, backend: VideoBackend, env: dict[str, str] | None = None) -> None:
        super().__init__(env)
        self.backend = backend
        self.id = backend.name
        self.display_name = f"Video: {backend.name}"
        self.provider_type = "cloud"
        self.paid = True
        self.key_env_names = self._discover_key_env_names()
        caps = backend.capabilities
        self._enabled_caps: set[Capability] = set()
        if getattr(caps, "supports_t2v", False):
            self._enabled_caps.add(Capability.VIDEO_GENERATION)
        if getattr(caps, "supports_image_to_video", False):
            self._enabled_caps.add(Capability.IMAGE_TO_VIDEO)
        self.supported_capabilities = frozenset(self._enabled_caps)
        self._constraints: dict[str, Any] = {
            "max_duration_s": getattr(caps, "max_duration_s", None),
            "resolutions": getattr(caps, "resolutions", []),
            "aspects": getattr(caps, "aspects", []),
            "native_audio": getattr(caps, "native_audio", False),
            "camera_control": getattr(caps, "camera_control", False),
            "character_ref": getattr(caps, "supports_character_ref", False),
            "cost_per_second_usd": getattr(caps, "cost_per_second_usd", None),
        }

    def _discover_key_env_names(self) -> tuple[str, ...]:
        return _PROVIDER_KEYS.get(self.backend.name, ())

    def is_configured(self) -> bool:
        # Live lookup: a key loaded into the process env after the backend was
        # constructed (e.g. .env at CLI startup) must still count as configured.
        if any(self._env_value(k) for k in self.key_env_names):
            return True
        return self.backend.is_configured()

    def models_for(self, capability: Capability) -> list[str]:
        m = getattr(self.backend, "model_id", "") or getattr(self.backend, "default_model", "")
        return [m] if m else [self.id]

    def constraints_for(self, capability: Capability) -> dict[str, Any]:
        return self._constraints

    def fee_free_check(self, capability: Capability) -> bool:
        return False  # verifying validity would require a possibly-paid call

    def check_availability(self) -> AvailabilityResult:
        import time as _time

        configured = self.is_configured()
        return AvailabilityResult(
            provider=self.id,
            ok=configured,
            mode="config",
            cost_incurred=False,
            detail=("Key present" if configured else "No key configured — no paid calls sent"),
            checked_at=_time.strftime("%Y-%m-%dT%H:%M:%S"),
            duration_ms=0,
            last_status="ok" if configured else "unknown",
        )

    def estimate_cost(self, specs: list[Any]) -> Any:
        return self.backend.estimate_cost(specs)


def _register_video_backends() -> None:
    for name in backend_names():
        if name in ("disabled", "base"):
            continue
        try:
            backend = create_backend(name)
        except Exception:
            continue
        registry().register_provider(VideoBackendAIProvider(backend))


_register_video_backends()
