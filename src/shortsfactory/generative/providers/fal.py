"""Generic fal.ai queue provider (aggregator of video models, e.g. Kling).

Follows the standard fal queue pattern: submit -> request_id, poll status,
then read the response_url and download media. Model id is configurable via
env SHORTSFACTORY_FAL_MODEL (default: fal-ai/kling-video/v3/standard/text-to-video).
Requires FAL_KEY.
"""

from __future__ import annotations

from pathlib import Path

import requests

from ..backend import BackendVideoMissing, ProviderError, VideoBackend, register_backend
from ..models import BackendCapabilities, GenerationJob, SceneGenerationSpec

_QUEUE_BASE = "https://queue.fal.run"
_TIMEOUT = 120
_DEFAULT_MODEL = "fal-ai/kling-video/v3/standard/text-to-video"


@register_backend
class FalBackend(VideoBackend):
    name = "fal"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_t2v=True,
            supports_image_to_video=True,
            supports_first_last_frame=True,  # depends on the hosted model (Kling FLF)
            supports_character_ref=True,  # depends on the hosted model
            native_audio=True,  # many hosted models produce audio
            camera_control=False,
            max_duration_s=10.0,
            resolutions=["720p", "1080p"],
            aspects=["9:16"],
            cost_per_second_usd=None,  # per hosted model; do not invent
            supports_negative_prompt=False,
        )

    def is_configured(self) -> bool:
        return bool(self.env.get("FAL_KEY") or self.env.get("FAL_API_KEY"))

    def _key(self) -> str:
        return self.env.get("FAL_KEY") or self.env.get("FAL_API_KEY") or ""

    def _model(self) -> str:
        return self.env.get("SHORTSFACTORY_FAL_MODEL") or _DEFAULT_MODEL

    def _headers(self) -> dict:
        return {"Authorization": f"Key {self._key()}", "Content-Type": "application/json"}

    def submit(self, spec: SceneGenerationSpec) -> GenerationJob:
        if not self.is_configured():
            raise ProviderError("fal: отсутствует FAL_KEY")
        body: dict = {"prompt": spec.prompt}
        if spec.reference_images:
            body["image_url"] = f"file://{Path(spec.reference_images[0]).resolve()}"
        url = f"{_QUEUE_BASE}/{self._model().lstrip('/')}"
        try:
            resp = requests.post(url, json=body, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"fal: сеть не доступна: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"fal: HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("fal: не-JSON ответ") from exc
        rid = data.get("request_id") or data.get("id")
        if not rid:
            raise ProviderError(f"fal: нет request_id в ответе: {data}")
        return GenerationJob(job_id=str(rid), provider=self.name, scene_idx=spec.scene_idx)

    def poll(self, job: GenerationJob) -> GenerationJob:
        url = f"{_QUEUE_BASE}/{self._model().lstrip('/')}/requests/{job.job_id}/status"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"fal: сеть не доступна: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"fal: status HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("fal: не-JSON статус") from exc
        status = str(data.get("status", "")).upper()
        if status == "COMPLETED":
            job.status = "done"
            job.detail = str(data.get("response_url") or data.get("result_url") or "")
        elif status in ("FAILED", "ERROR"):
            job.status = "error"
            job.error = str(data.get("error") or "fal: рендер не удался")
        else:
            job.status = "running"
        return job

    def fetch(self, job: GenerationJob, dest_dir: Path) -> str:
        if job.status != "done" or not job.detail:
            raise BackendVideoMissing("fal: видео ещё не готово")
        resp = requests.get(job.detail, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            raise ProviderError(f"fal: не удалось получить результат (HTTP {resp.status_code})")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        video_url = None
        if isinstance(data.get("video"), dict):
            video_url = data["video"].get("url")
        elif isinstance(data.get("video"), str):
            video_url = data["video"]
        elif isinstance(data.get("url"), str):
            video_url = data["url"]
        if not video_url:
            raise BackendVideoMissing("fal: в ответе нет видео")
        media = requests.get(video_url, timeout=_TIMEOUT)
        if media.status_code >= 400:
            raise ProviderError(f"fal: не удалось скачать видео (HTTP {media.status_code})")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"fal_{job.scene_idx:03d}.mp4"
        out.write_bytes(media.content)
        if out.stat().st_size == 0:
            raise BackendVideoMissing("fal: пустой файл видео")
        return str(out)
