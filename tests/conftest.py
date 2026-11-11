from pathlib import Path

import pytest

from shortsfactory.config import Config


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def config() -> Config:
    return Config(ROOT)
