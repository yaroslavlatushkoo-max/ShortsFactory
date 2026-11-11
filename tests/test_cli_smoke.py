"""Smoke-тесты CLI (cli.py): только парсер и справка, никаких рендеров и LLM."""
from __future__ import annotations

import pytest

from shortsfactory.cli import build_parser, main

SUBCOMMANDS = [
    "create", "batch", "ask", "status", "inspect", "retry", "report", "preview",
    "doctor", "resume", "remake", "animate", "integrated-create", "ai-create",
    "free-create", "blocky-film", "blocky-create", "blocky-qc", "blocky-regress",
    "providers", "providers-check",
]


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_subcommand_help_exits_zero(command: str, capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([command, "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage:")
    assert "shortsfactory" in out


def test_version_exits_zero(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert "ShortsFactory" in capsys.readouterr().out


def test_create_minimal_args_parse():
    args = build_parser().parse_args(["create", "--topic", "Космос", "--no-run"])
    assert args.command == "create"
    assert args.topic == "Космос"
    assert args.duration == 40.0
    assert args.languages == ["ru", "en"]
    assert args.visual_mode == "images"
    assert args.no_run is True
    assert args.json is False


def test_blocky_film_minimal_args_parse():
    args = build_parser().parse_args(["blocky-film", "--scene", "scenes/blocky/x.json", "--json"])
    assert args.command == "blocky-film"
    assert args.scene == "scenes/blocky/x.json"
    assert args.json_out is True


def test_unknown_command_exits_nonzero(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["нет-такой-команды"])
    assert exc.value.code != 0
    assert "invalid choice" in capsys.readouterr().err


def test_main_without_command_prints_help(capsys: pytest.CaptureFixture[str]):
    # без команды main печатает справку и возвращает 0 до загрузки конфигурации
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "create" in out
    assert "blocky-film" in out
