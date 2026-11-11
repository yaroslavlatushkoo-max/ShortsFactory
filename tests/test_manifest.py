"""Unit tests for the project manifest (stage E2)."""
from __future__ import annotations

import json

import pytest

from shortsfactory import manifest


def test_create_manifest_defaults():
    m = manifest.create_manifest()
    assert m["format"] == "shortsfactory-project"
    assert m["version"] == manifest.MANIFEST_VERSION
    assert m["status"] == "draft"
    assert m["settings"]["lang"] == "ru"
    assert m["settings"]["width"] == 480


def test_save_load_roundtrip(tmp_path):
    m = manifest.create_manifest(
        story_path="output/gui_stories/x/story.json",
        settings={"lang": "en", "scenes": 5},
        artifacts_path="out/final.mp4",
    )
    p = manifest.save_manifest(m, tmp_path / "project.manifest.json")
    loaded = manifest.load_manifest(p)
    assert loaded["story"].endswith("story.json")
    assert loaded["settings"]["lang"] == "en"
    assert loaded["settings"]["scenes"] == 5
    assert loaded["artifacts"]["video"].endswith("final.mp4")


def test_load_rejects_foreign_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "other"}), encoding="utf-8")
    with pytest.raises(ValueError):
        manifest.load_manifest(bad)


def test_summary_mentions_key_fields():
    m = manifest.create_manifest(settings={"lang": "ru", "scenes": 8})
    text = manifest.summary_manifest(m)
    assert "ru" in text
    assert "8" in text
