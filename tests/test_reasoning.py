"""Tests for ReAct reasoning loop helpers."""

import pytest

from synapse.chat.reasoning import _parse_action, _sanitize_cypher, _truncate_to_first_action


class TestSanitizeCypher:
    def test_plain_cypher(self):
        cypher = "MATCH (n) RETURN n"
        assert _sanitize_cypher(cypher) == "MATCH (n) RETURN n"

    def test_strips_quotes(self):
        assert _sanitize_cypher('"MATCH (n) RETURN n"') == "MATCH (n) RETURN n"
        assert _sanitize_cypher("'MATCH (n) RETURN n'") == "MATCH (n) RETURN n"

    def test_strips_trailing_parens(self):
        assert _sanitize_cypher("MATCH (n) RETURN n)") == "MATCH (n) RETURN n"

    def test_blocks_create(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("CREATE (n:Test {name: 'x'})")

    def test_blocks_delete(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("MATCH (n) DELETE n")

    def test_blocks_merge(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("MERGE (n:Test {name: 'x'})")

    def test_blocks_set(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("MATCH (n) SET n.name = 'x'")

    def test_blocks_drop(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("DROP INDEX ON :Test(name)")

    def test_blocks_remove(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("MATCH (n) REMOVE n.name")

    def test_case_insensitive(self):
        with pytest.raises(ValueError):
            _sanitize_cypher("match (n) delete n")


class TestTruncateToFirstAction:
    def test_single_action(self):
        text = "Thought: thinking\nAction: GRAPH_QUERY(MATCH (n) RETURN n)"
        result, was_truncated = _truncate_to_first_action(text)
        assert not was_truncated
        assert result == text

    def test_multi_action(self):
        text = "Thought: t1\nAction: GRAPH_QUERY(q1)\nThought: t2\nAction: GRAPH_QUERY(q2)"
        result, was_truncated = _truncate_to_first_action(text)
        assert was_truncated
        assert "q1" in result
        assert "q2" not in result

    def test_no_action(self):
        text = "Thought: just thinking"
        result, was_truncated = _truncate_to_first_action(text)
        assert not was_truncated


class TestParseAction:
    def test_graph_query(self):
        text = "Thought: t\nAction: GRAPH_QUERY(MATCH (n) RETURN n)"
        action = _parse_action(text)
        assert action is not None
        assert action[0] == "GRAPH_QUERY"
        assert "MATCH" in action[1]

    def test_section_text(self):
        text = "Thought: t\nAction: SECTION_TEXT(0003)"
        action = _parse_action(text)
        assert action is not None
        assert action[0] == "SECTION_TEXT"
        assert action[1] == "0003"

    def test_answer(self):
        text = "Thought: t\nAction: ANSWER(The answer is 42.)"
        action = _parse_action(text)
        assert action is not None
        assert action[0] == "ANSWER"
        assert "42" in action[1]

    def test_no_action(self):
        text = "Thought: just rambling"
        assert _parse_action(text) is None

    def test_multi_action_returns_first(self):
        text = "Action: GRAPH_QUERY(q1)\nAction: GRAPH_QUERY(q2)"
        action = _parse_action(text)
        assert action is not None
        assert "q1" in action[1]
        assert action[2] is True  # was_multi
