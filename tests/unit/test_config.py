from pathlib import Path

import pytest

from autoria_parser.config import read_input_urls


def test_read_input_urls(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("\n# comment\n https://example.com \n", encoding="utf-8")
    urls = read_input_urls(input_file)
    assert urls == ["https://example.com"]


def test_read_input_urls_empty(tmp_path: Path) -> None:
    input_file = tmp_path / "input.txt"
    input_file.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_input_urls(input_file)
