from pathlib import Path

import pytest

from shortsfactory.config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def config() -> Config:
    return Config(ROOT)


@pytest.fixture
def needs_voices(config):
    """Скип-гард для интеграционных тестов, которым нужны Piper-модели."""
    missing = [
        n for n in ("ru_RU-dmitri-medium.onnx", "en_US-libritts_r-medium.onnx")
        if not (config.models_dir / n).exists()
    ]
    if missing:
        pytest.skip(f"Piper voices не скачаны: {', '.join(missing)}")
