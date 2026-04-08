"""Tests for AI graph tools — smart search and tool dispatch."""

import pytest
from unittest.mock import MagicMock

from synapse.tools.search import normalize_search_term, extract_keywords, smart_search
from synapse.tools.config import GraphToolsConfig
from synapse.tools.graph_tools import execute_tool, _config_cache


# ── normalize_search_term ────────────────────────────────────

class TestNormalizeSearchTerm:
    def test_basic(self):
        assert normalize_search_term("Hello World") == "hello world"

    def test_strip_registered(self):
        assert normalize_search_term("SILRES® BS 1052") == "silres bs 1052"

    def test_strip_trademark(self):
        assert normalize_search_term("Product™ Name") == "product name"

    def test_strip_stars(self):
        assert normalize_search_term("**bold text**") == "bold text"

    def test_strip_brackets(self):
        assert normalize_search_term("[test] (value)") == "test value"

    def test_collapse_spaces(self):
        assert normalize_search_term("too   many    spaces") == "too many spaces"

    def test_empty(self):
        assert normalize_search_term("") == ""

    def test_only_special(self):
        assert normalize_search_term("®™©") == ""


# ── extract_keywords ─────────────────────────────────────────

class TestExtractKeywords:
    def test_basic(self):
        kws = extract_keywords("silres bs 1052")
        assert "silres bs 1052" in kws
        assert "bs 1052" in kws

    def test_strips_noise(self):
        kws = extract_keywords("the viscosity of the product")
        assert "the" not in kws
        assert "of" not in kws
        assert "viscosity" in kws
        assert "product" in kws

    def test_single_word(self):
        kws = extract_keywords("viscosity")
        assert kws == ["viscosity"]

    def test_full_term_first(self):
        kws = extract_keywords("bs 1052")
        assert kws[0] == "bs 1052"


# ── smart_search ─────────────────────────────────────────────

class _FakeNode:
    """Minimal fake FalkorDB Node for testing."""
    def __init__(self, props):
        self.properties = props


class TestSmartSearch:
    CFG = GraphToolsConfig(name_property="canonical_name", exclude_labels={"Document", "Section"})

    def _mock_graph(self, search_responses):
        """Mock graph with field discovery + search responses."""
        graph = MagicMock()
        # First call is field discovery (RETURN n LIMIT 1)
        discovery_node = _FakeNode({"canonical_name": "x", "name": "X", "text": "X"})
        all_responses = [[discovery_node]] + search_responses
        graph.query = MagicMock(side_effect=all_responses)
        # Clear field cache
        from synapse.tools.search import _fields_cache
        _fields_cache.pop(str(id(graph)), None)
        return graph

    def _node_row(self, name, etype, node_id=1, conf=1.0):
        return [_FakeNode({"canonical_name": name, "confidence": conf}), etype, node_id]

    def test_finds_with_full_name(self):
        graph = self._mock_graph([
            [self._node_row("silres bs 1052", "PRODUCT", node_id=1)],
        ])
        results = smart_search("SILRES® BS 1052", graph, self.CFG)
        assert len(results) == 1
        assert results[0]["canonical_name"] == "silres bs 1052"

    def test_fallback_to_keyword(self):
        graph = self._mock_graph([
            [],
            [self._node_row("silres bs 1052", "PRODUCT", node_id=1)],
        ])
        results = smart_search("SILRES® BS 1052", graph, self.CFG)
        assert len(results) == 1

    def test_deduplicates_same_node(self):
        """Same node found via different fields should appear once."""
        graph = self._mock_graph([
            [
                self._node_row("silres bs 1052", "PRODUCT", node_id=42),
                self._node_row("silres bs 1052", "PRODUCT", node_id=42),  # duplicate
            ],
        ])
        results = smart_search("BS 1052", graph, self.CFG)
        assert len(results) == 1

    def test_no_results(self):
        graph = self._mock_graph([[], [], [], []])
        results = smart_search("nonexistent", graph, self.CFG)
        assert results == []

    def test_empty_input(self):
        graph = MagicMock()
        results = smart_search("", graph, self.CFG)
        assert results == []


# ── execute_tool ─────────────────────────────────────────────

class TestExecuteTool:
    def _mock_graph(self):
        graph = MagicMock()
        graph.query = MagicMock(return_value=[])
        graph.get_entity_counts = MagicMock(return_value={"PRODUCT": 10, "CHEMICAL": 5})
        graph.get_relationship_counts = MagicMock(return_value={"HAS_PROPERTY": 20})
        # Pre-inject config to avoid auto-discovery on mock
        _config_cache[str(id(graph))] = GraphToolsConfig()
        return graph

    def test_find_tool(self):
        graph = self._mock_graph()
        result = execute_tool("FIND", "test", graph)
        assert "No entities found" in result or "Found" in result

    def test_schema_tool(self):
        graph = self._mock_graph()
        result = execute_tool("SCHEMA", "", graph)
        assert "PRODUCT" in result
        assert "10 instances" in result

    def test_unknown_tool(self):
        graph = self._mock_graph()
        result = execute_tool("INVALID", "test", graph)
        assert "Unknown tool" in result

    def test_find_empty_arg(self):
        graph = self._mock_graph()
        result = execute_tool("FIND", "", graph)
        assert "Error" in result

    def test_details_not_found(self):
        graph = self._mock_graph()
        result = execute_tool("DETAILS", "nonexistent", graph)
        assert "not found" in result.lower()

    def test_list_empty_type(self):
        graph = self._mock_graph()
        result = execute_tool("LIST", "", graph)
        assert "Error" in result

    def test_compare_needs_two(self):
        graph = self._mock_graph()
        result = execute_tool("COMPARE", "only_one", graph)
        assert "Error" in result or "requires two" in result.lower()

    def test_related_no_entity(self):
        graph = self._mock_graph()
        result = execute_tool("RELATED", "nonexistent", graph)
        assert "not found" in result.lower()


# ── Action parser with new tools ─────────────────────────────

class TestParseActionNewTools:
    def test_find(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Thought: search\nAction: FIND(BS 1052)")
        assert r is not None
        assert r[0] == "FIND"
        assert r[1] == "BS 1052"

    def test_details(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Thought: get info\nAction: DETAILS(silres bs 1052)")
        assert r is not None
        assert r[0] == "DETAILS"
        assert r[1] == "silres bs 1052"

    def test_schema(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Thought: check types\nAction: SCHEMA()")
        assert r is not None
        assert r[0] == "SCHEMA"
        assert r[1] == ""

    def test_compare(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Thought: compare\nAction: COMPARE(bs 1052, bs 5137)")
        assert r is not None
        assert r[0] == "COMPARE"
        assert "bs 1052" in r[1]
        assert "bs 5137" in r[1]

    def test_related_with_type(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Thought: find substrates\nAction: RELATED(silres bs 1052, TREATS_SUBSTRATE)")
        assert r is not None
        assert r[0] == "RELATED"
        assert "TREATS_SUBSTRATE" in r[1]

    def test_list(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Action: LIST(PRODUCT)")
        assert r is not None
        assert r[0] == "LIST"
        assert r[1] == "PRODUCT"

    def test_legacy_graph_query(self):
        from synapse.chat.reasoning import _parse_action
        r = _parse_action("Action: GRAPH_QUERY(MATCH (n) RETURN n LIMIT 5)")
        assert r is not None
        assert r[0] == "GRAPH_QUERY"
