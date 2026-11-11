"""Google Gemini Veo 3.1 provider (image-to-video / text-to-video).

Uses the Gemini generateContent REST API. All request-shape specifics are
kept in one method (`_build_payload`) so field verification against current
Google docs is a single edit. No live call is made without a key; the dry-run
path prints the exact request instead.
"""

from __future__ import annotations

import base64
from pathlib import Path

import requests

from ..backend import BackendVideoMissing, ProviderError, VideoBackend, register_backend
from ..models import BackendCapabilities, GenerationJob, SceneGenerationSpec

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MODEL = "veo-3.1-lite-generate-001"
_TIMEOUT = 120


@register_backend
class VeoBackend(VideoBackend):
    name = "veo"

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_t2v=True,
            supports_image_to_video=True,
            supports_first_last_frame=False,
            supports_character_ref=True,  # via a reference image (i2v)
            native_audio=False,
            camera_control=False,
            max_duration_s=8.0,
            resolutions=["720p", "1080p"],
            aspects=["9:16"],
            cost_per_second_usd=0.05,  # Lite 720p (0.08 at 1080p)
            supports_negative_prompt=False,
        )

    def is_configured(self) -> bool:
        return bool(self.env.get("GEMINI_API_KEY") or self.env.get("GOOGLE_API_KEY"))

    def _key(self) -> str:
        return self.env.get("GEMINI_API_KEY") or self.env.get("GOOGLE_API_KEY") or ""

    def _build_payload(self, spec: SceneGenerationSpec) -> dict:
        ratio = "9:16" if abs(spec.width / spec.height - 9 / 16) < 0.05 else "16:9"
        gen: dict = {"aspectRatio": ratio, "durationSeconds": int(max(spec.duration, 4))}
        parts: list[dict] = [{"text": spec.prompt}]
        if spec.reference_images:
            ref = Path(spec.reference_images[0])
            parts.append(
                {
                    "inlineData": {
                        "mimeType": "image/png",
                        "data": base64.b64encode(ref.read_bytes()).decode("ascii"),
                    }
                }
            )
        return {"contents": [{"parts": parts}], "generationConfig": gen}

    def submit(self, spec: SceneGenerationSpec) -> GenerationJob:
        if not self.is_configured():
            raise ProviderError("Veo: отсутствует GEMINI_API_KEY / GOOGLE_API_KEY")
        url = f"{_API_BASE}/models/{_MODEL}:generateContent"
        payload = self._build_payload(spec)
        headers = {"x-goog-api-key": self._key(), "Content-Type": "application/json"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"Veo: сеть не доступна: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"Veo: HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Veo: не-JSON ответ сервера") from exc
        job = GenerationJob(job_id=_extract_operation(data), provider=self.name, scene_idx=spec.scene_idx)
        # inline completion
        video = _extract_video(data)
        if video is not None:
            job.status = "done"
            job.detail = video  # holds decoded bytes or uri
        else:
            job.status = "running"
        return job

    def poll(self, job: GenerationJob) -> GenerationJob:
        if job.status == "done":
            return job
        return job  # Veo generateContent is synchronous; status stays running until fetched

    def fetch(self, job: GenerationJob, dest_dir: Path) -> str:
        if job.status != "done" or not job.detail:
            raise BackendVideoMissing("Veo: видео ещё не готово")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"veo_{job.scene_idx:03d}.mp4"
        detail = job.detail
        if isinstance(detail, str) and detail.startswith(("http://", "https://")):
            resp = requests.get(detail, timeout=_TIMEOUT)
            if resp.status_code >= 400:
                raise ProviderError(f"Veo: не удалось скачать видео (HTTP {resp.status_code})")
            out.write_bytes(resp.content)
        else:
            # base64 inline
            try:
                data = base64.b64decode(detail)
            except Exception as exc:  # noqa: BLE001
                raise BackendVideoMissing("Veo: некорректные inline-видео") from exc
            out.write_bytes(data)
        if not out.exists() or out.stat().st_size == 0:
            raise BackendVideoMissing("Veo: пустой файл видео")
        return str(out)


def _extract_operation(data: dict) -> str:
    return str(data.get("name") or "veo-job")


def _extract_video(data: dict) -> str | None:
    candidates = data.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        return None
    parts = candidates[0].get("content", {}).get("parts", []) if isinstance(candidates[0], dict) else []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if "inlineData" in part and isinstance(part["inlineData"], dict):
            payload = part["inlineData"].get("data")
            if payload:
                return payload
        if isinstance(part.get("video"), dict) and part["video"].get("uri"):
            return part["video"]["uri"]
    # some responses surface a uri at top-level
    uri = data.get("videoUrl") or data.get("uri")
    if isinstance(uri, str) and uri.startswith("http"):
        return uri
    return None
