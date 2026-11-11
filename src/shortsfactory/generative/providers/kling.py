"""Official Kling AI provider (v3.0 models: text2video, image2video).

Authentication (2026): Kling AI now recommends a single API Key sent as an
``Authorization: Bearer <key>`` header (created at https://kling.ai/dev/api-key).
The legacy AccessKey/SecretKey JWT (HMAC-SHA256) signing is kept for backward
compatibility and used only when the modern key is absent. The two schemes are
never mixed: the modern key wins when present.

Status: request/parse logic is unit-tested with mocked HTTP; it has NOT been
field-verified without a live key (see GENERATIVE_VIDEO_LIMITATIONS.md).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import requests

from ..backend import BackendVideoMissing, ProviderError, VideoBackend, register_backend
from ..models import BackendCapabilities, GenerationJob, SceneGenerationSpec

_API_BASE = "https://api.klingai.com"
_API_KEY_ENVS = ("KLING_API_KEY", "KLINGAI_API_KEY")  # modern bearer-token key
_LEGACY_KEYS = ("KLING_ACCESS_KEY", "KLING_SECRET_KEY")  # legacy AK/SK pair
_TIMEOUT = 120


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _jwt(ak: str, sk: str, payload: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    head_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{head_b64}.{body_b64}"
    digest = hmac.new(sk.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(digest)}"


@register_backend
class KlingBackend(VideoBackend):
    name = "kling"

    _model_override: str = ""  # set via apply_model_override() (e.g. --video-model)

    def apply_model_override(self, model: str) -> None:
        self._model_override = model or ""

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_t2v=True,
            supports_image_to_video=True,
            supports_first_last_frame=True,  # start+end frame (imageTail, pro mode)
            supports_character_ref=True,  # "Elements" / inputReferences
            native_audio=True,
            camera_control=True,
            max_duration_s=15.0,
            resolutions=["720p", "1080p"],
            aspects=["9:16"],
            cost_per_second_usd=0.14 / 5.0,  # Standard 720p ~$0.14 per 5s
            supports_negative_prompt=True,
        )

    # ---- configuration -------------------------------------------------
    def _api_key(self) -> str:
        for env_name in _API_KEY_ENVS:
            value = self.env.get(env_name) or ""
            if value:
                return value
        return ""

    def is_configured(self) -> bool:
        if self._api_key():
            return True
        return bool(self.env.get("KLING_ACCESS_KEY") and self.env.get("KLING_SECRET_KEY"))

    def _ak(self) -> str:
        return self.env.get("KLING_ACCESS_KEY", "")

    def _sk(self) -> str:
        return self.env.get("KLING_SECRET_KEY", "")

    def _base_url(self) -> str:
        return (self.env.get("KLING_BASE_URL") or _API_BASE).rstrip("/")

    def _headers(self) -> dict:
        key = self._api_key()
        if key:
            # modern single-key bearer authentication
            return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        # legacy AK/SK JWT signing
        payload = {"iss": self._ak(), "exp": int(time.time()) + 1800, "nbf": int(time.time()) - 5}
        token = _jwt(self._ak(), self._sk(), payload)
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ---- generation ------------------------------------------------------
    def submit(self, spec: SceneGenerationSpec) -> GenerationJob:
        if not self.is_configured():
            raise ProviderError(
                "Kling: отсутствует KLING_API_KEY (или пара KLING_ACCESS_KEY/KLING_SECRET_KEY)"
            )
        # CLI override (--video-model) wins over env for text-to-video. The image
        # path stays independent to avoid sending a t2v model to the i2v endpoint.
        text_model = self._model_override or self.env.get("KLING_TEXT_MODEL") or "kling-v3.0-t2v"
        image_model = self.env.get("KLING_IMAGE_MODEL") or "kling-v3.0-i2v"
        body: dict = {
            "model_name": image_model if spec.reference_images else text_model,
            "prompt": spec.prompt,
            "negative_prompt": spec.negative_prompt or None,
            "cfg_scale": 0.5,
            "mode": "std",
            "aspect_ratio": "9:16",
            "duration": str(int(max(spec.duration, 5))),
        }
        if spec.reference_images:
            body["image"] = str(Path(spec.reference_images[0]).resolve())
        if spec.first_frame and spec.last_frame:
            body["first_frame_image"] = str(Path(spec.first_frame).resolve())
            body["last_frame_image"] = str(Path(spec.last_frame).resolve())
        endpoint = "/v1/videos/image2video" if spec.reference_images else "/v1/videos/text2video"
        url = f"{self._base_url()}{endpoint}"
        try:
            resp = requests.post(url, json=body, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"Kling: сеть не доступна: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"Kling: HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Kling: не-JSON ответ") from exc
        tid = data.get("data", {}).get("task_id") if isinstance(data.get("data"), dict) else None
        if not tid:
            raise ProviderError(f"Kling: нет task_id в ответе: {data}")
        return GenerationJob(job_id=str(tid), provider=self.name, scene_idx=spec.scene_idx)

    def poll(self, job: GenerationJob) -> GenerationJob:
        url = f"{self._base_url()}/v1/videos/{job.job_id}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise ProviderError(f"Kling: сеть не доступна: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"Kling: status HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError("Kling: не-JSON статус") from exc
        info = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        status = str(info.get("task_status", "")).upper()
        if status in ("SUCCEED", "SUCCEEDED", "SUCCESS"):
            job.status = "done"
            videos = info.get("task_result", {}).get("videos", [{}])
            job.detail = str(videos[0].get("url", "")) if isinstance(videos[0], dict) else ""
            if not job.detail:
                raise BackendVideoMissing("Kling: выполнено, но нет url видео")
        elif status in ("FAILED", "CANCELLED"):
            job.status = "error"
            job.error = str(info.get("task_status_msg") or "Kling: рендер не удался")
        else:
            job.status = "running"
        return job

    def fetch(self, job: GenerationJob, dest_dir: Path) -> str:
        if job.status != "done" or not job.detail:
            raise BackendVideoMissing("Kling: видео ещё не готово")
        resp = requests.get(job.detail, timeout=_TIMEOUT)
        if resp.status_code >= 400:
            raise ProviderError(f"Kling: не удалось скачать (HTTP {resp.status_code})")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"kling_{job.scene_idx:03d}.mp4"
        out.write_bytes(resp.content)
        if out.stat().st_size == 0:
            raise BackendVideoMissing("Kling: пустой файл видео")
        return str(out)
