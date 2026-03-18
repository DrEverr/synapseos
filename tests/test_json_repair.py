"""Tests for JSON repair from LLM output."""

import pytest

from synapse.llm.json_repair import repair_and_parse_json


def test_valid_json():
    assert repair_and_parse_json('{"key": "value"}') == {"key": "value"}


def test_valid_array():
    assert repair_and_parse_json("[1, 2, 3]") == [1, 2, 3]


def test_markdown_fences():
    result = repair_and_parse_json('```json\n{"key": "value"}\n```')
    assert result == {"key": "value"}


def test_python_literals():
    result = repair_and_parse_json('{"active": True, "count": None}')
    assert result == {"active": True, "count": None}


def test_trailing_commas():
    result = repair_and_parse_json('{"a": 1, "b": 2,}')
    assert result == {"a": 1, "b": 2}


def test_line_comments():
    result = repair_and_parse_json('{"a": 1 // comment\n}')
    assert result == {"a": 1}


def test_embedded_json():
    result = repair_and_parse_json('Here is the result: {"key": "value"} end.')
    assert result == {"key": "value"}


def test_empty_raises():
    with pytest.raises(ValueError):
        repair_and_parse_json("")


def test_invalid_raises():
    with pytest.raises(ValueError):
        repair_and_parse_json("not json at all")


def test_nested_objects():
    result = repair_and_parse_json('{"a": {"b": [1, 2]}}')
    assert result == {"a": {"b": [1, 2]}}


def test_truncated_repair():
    result = repair_and_parse_json('{"entities": [{"text": "hello"')
    assert isinstance(result, dict)
    assert "entities" in result
