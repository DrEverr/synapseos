"""Tests for reasoning result models and evidence summary building."""

from synapse.chat.reasoning import _build_evidence_summary
from synapse.models.reasoning import EnrichmentResult, ReasoningResult, SelfAssessment


class TestSelfAssessment:
    def test_defaults(self):
        sa = SelfAssessment()
        assert sa.confidence == 0.0
        assert sa.groundedness == 0.0
        assert sa.completeness == 0.0
        assert sa.reasoning == ""
        assert sa.gaps == []

    def test_from_values(self):
        sa = SelfAssessment(
            confidence=0.85,
            groundedness=0.9,
            completeness=0.7,
            reasoning="Well supported by graph data.",
            gaps=["Missing author details"],
        )
        assert sa.confidence == 0.85
        assert len(sa.gaps) == 1

    def test_clamps_to_range(self):
        """Pydantic should enforce ge=0.0, le=1.0."""
        try:
            SelfAssessment(confidence=1.5)
            assert False, "Should have raised"
        except Exception:
            pass


class TestEnrichmentResult:
    def test_defaults(self):
        er = EnrichmentResult()
        assert er.entities_added == 0
        assert er.relationships_added == 0

    def test_from_values(self):
        er = EnrichmentResult(entities_added=3, relationships_added=5)
        assert er.entities_added == 3
        assert er.relationships_added == 5


class TestReasoningResult:
    def test_minimal(self):
        rr = ReasoningResult(answer="42", question="What is the meaning of life?")
        assert rr.answer == "42"
        assert rr.question == "What is the meaning of life?"
        assert rr.steps_taken == 0
        assert rr.assessment is None
        assert rr.enrichment is None

    def test_full(self):
        rr = ReasoningResult(
            answer="Einstein",
            question="Who developed relativity?",
            steps_taken=5,
            empty_result_count=1,
            timed_out=False,
            max_steps_reached=False,
            doom_loop_triggered=False,
            elapsed_seconds=12.5,
            section_ids_used=["0001", "0003"],
            actions_log=[
                {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n", "observation": "Einstein"},
            ],
            assessment=SelfAssessment(confidence=0.95, groundedness=0.9, completeness=0.8),
            enrichment=EnrichmentResult(entities_added=1, relationships_added=2),
        )
        assert rr.steps_taken == 5
        assert len(rr.actions_log) == 1
        assert rr.assessment is not None
        assert rr.assessment.confidence == 0.95
        assert rr.enrichment is not None
        assert rr.enrichment.entities_added == 1


class TestBuildEvidenceSummary:
    def test_empty_log(self):
        result = _build_evidence_summary([])
        assert "no evidence" in result.lower()

    def test_graph_results_included(self):
        log = [
            {
                "tool": "GRAPH_QUERY",
                "args": "MATCH (n) RETURN n",
                "observation": "einstein | PERSON",
            },
        ]
        result = _build_evidence_summary(log)
        assert "[GRAPH_QUERY]" in result
        assert "einstein" in result

    def test_empty_results_excluded(self):
        log = [
            {
                "tool": "GRAPH_QUERY",
                "args": "MATCH (n) RETURN n",
                "observation": "(no results)",
            },
        ]
        result = _build_evidence_summary(log)
        assert "no evidence" in result.lower()

    def test_section_text_included(self):
        log = [
            {
                "tool": "SECTION_TEXT",
                "args": "0003",
                "observation": "This section discusses quantum physics.",
            },
        ]
        result = _build_evidence_summary(log)
        assert "[Section]" in result
        assert "quantum" in result

    def test_truncates_long_observations(self):
        long_obs = "x" * 1000
        log = [
            {"tool": "GRAPH_QUERY", "args": "q", "observation": long_obs},
        ]
        result = _build_evidence_summary(log)
        assert len(result) < len(long_obs)
        assert "..." in result

    def test_mixed_log(self):
        log = [
            {"tool": "GRAPH_QUERY", "args": "q1", "observation": "(no results)"},
            {"tool": "GRAPH_QUERY", "args": "q2", "observation": "data found"},
            {"tool": "SECTION_TEXT", "args": "0001", "observation": "section content"},
            {"tool": "ANSWER", "args": "", "observation": ""},
        ]
        result = _build_evidence_summary(log)
        assert "[GRAPH_QUERY]" in result
        assert "[Section]" in result
        assert "data found" in result

    def test_max_entries_cap(self):
        """Should include at most 10 evidence entries."""
        log = [
            {"tool": "GRAPH_QUERY", "args": f"q{i}", "observation": f"result {i}"}
            for i in range(20)
        ]
        result = _build_evidence_summary(log)
        # Count [Graph] entries
        assert result.count("[GRAPH_QUERY]") <= 10
