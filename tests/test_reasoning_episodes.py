"""Tests for reasoning episode storage and retrieval in InstanceStore."""

import json

import pytest

from synapse.chat.reasoning import _log_episode
from synapse.models.reasoning import (
    EnrichmentResult,
    ReasoningResult,
    SelfAssessment,
)
from synapse.storage.instance_store import InstanceStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_episodes.db"
    s = InstanceStore(db_path)
    yield s
    s.close()


class TestStoreReasoningEpisode:
    def test_store_minimal(self, store):
        episode_id = store.store_reasoning_episode(
            question="What is X?",
            answer="X is Y.",
        )
        assert episode_id is not None
        assert episode_id > 0

    def test_store_full(self, store):
        episode_id = store.store_reasoning_episode(
            question="Who invented relativity?",
            answer="Albert Einstein",
            steps_taken=5,
            empty_results=1,
            timed_out=False,
            max_steps_reached=False,
            doom_loop_triggered=False,
            elapsed_seconds=12.5,
            section_ids=["0001", "0003"],
            actions_log=[
                {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n", "observation": "data"},
            ],
            confidence=0.95,
            groundedness=0.9,
            completeness=0.8,
            assessment_reasoning="Well supported.",
            assessment_gaps=["Missing birth date"],
            entities_added=2,
            rels_added=3,
        )
        assert episode_id > 0

    def test_retrieve_episodes(self, store):
        store.store_reasoning_episode(question="Q1", answer="A1", confidence=0.5)
        store.store_reasoning_episode(question="Q2", answer="A2", confidence=0.9)

        episodes = store.get_reasoning_episodes(limit=10)
        assert len(episodes) == 2
        # Newest first
        assert episodes[0]["question"] == "Q2"
        assert episodes[1]["question"] == "Q1"

    def test_retrieve_with_limit(self, store):
        for i in range(5):
            store.store_reasoning_episode(question=f"Q{i}", answer=f"A{i}")

        episodes = store.get_reasoning_episodes(limit=3)
        assert len(episodes) == 3

    def test_retrieve_with_offset(self, store):
        for i in range(5):
            store.store_reasoning_episode(question=f"Q{i}", answer=f"A{i}")

        episodes = store.get_reasoning_episodes(limit=10, offset=3)
        assert len(episodes) == 2

    def test_json_fields_roundtrip(self, store):
        section_ids = ["sec_001", "sec_002"]
        actions_log = [{"tool": "GRAPH_QUERY", "args": "q", "observation": "data"}]
        gaps = ["gap1", "gap2"]

        store.store_reasoning_episode(
            question="Q",
            answer="A",
            section_ids=section_ids,
            actions_log=actions_log,
            assessment_gaps=gaps,
        )

        episodes = store.get_reasoning_episodes(limit=1)
        ep = episodes[0]
        assert json.loads(ep["section_ids"]) == section_ids
        assert json.loads(ep["actions_log"]) == actions_log
        assert json.loads(ep["assessment_gaps"]) == gaps

    def test_created_at_populated(self, store):
        store.store_reasoning_episode(question="Q", answer="A")
        episodes = store.get_reasoning_episodes(limit=1)
        assert episodes[0]["created_at"] != ""


class TestGetReasoningStats:
    def test_empty_stats(self, store):
        stats = store.get_reasoning_stats()
        assert stats["total_episodes"] == 0

    def test_aggregate_stats(self, store):
        store.store_reasoning_episode(
            question="Q1",
            answer="A1",
            steps_taken=4,
            elapsed_seconds=10.0,
            confidence=0.8,
            groundedness=0.7,
            completeness=0.6,
            entities_added=2,
            rels_added=1,
        )
        store.store_reasoning_episode(
            question="Q2",
            answer="A2",
            steps_taken=6,
            elapsed_seconds=20.0,
            confidence=0.9,
            groundedness=0.8,
            completeness=0.7,
            entities_added=3,
            rels_added=2,
            timed_out=True,
            doom_loop_triggered=True,
        )

        stats = store.get_reasoning_stats()
        assert stats["total_episodes"] == 2
        assert stats["avg_steps"] == 5.0
        assert stats["avg_elapsed"] == 15.0
        assert abs(stats["avg_confidence"] - 0.85) < 1e-9
        assert abs(stats["avg_groundedness"] - 0.75) < 1e-9
        assert abs(stats["avg_completeness"] - 0.65) < 1e-9
        assert stats["total_entities_added"] == 5
        assert stats["total_rels_added"] == 3
        assert stats["total_timeouts"] == 1
        assert stats["total_doom_loops"] == 1


class TestLogEpisode:
    def test_log_episode_with_store(self, store):
        result = ReasoningResult(
            answer="42",
            question="What is the answer?",
            steps_taken=3,
            empty_result_count=1,
            elapsed_seconds=5.0,
            section_ids_used=["0001"],
            actions_log=[{"tool": "GRAPH_QUERY", "args": "q", "observation": "data"}],
            assessment=SelfAssessment(confidence=0.8, groundedness=0.7, completeness=0.9),
            enrichment=EnrichmentResult(entities_added=1, relationships_added=2),
        )
        _log_episode(result, store)

        episodes = store.get_reasoning_episodes(limit=1)
        assert len(episodes) == 1
        ep = episodes[0]
        assert ep["question"] == "What is the answer?"
        assert ep["answer"] == "42"
        assert ep["steps_taken"] == 3
        assert ep["confidence"] == 0.8
        assert ep["entities_added"] == 1
        assert ep["rels_added"] == 2

    def test_log_episode_without_store(self):
        """Should not raise when store is None."""
        result = ReasoningResult(answer="ok", question="test")
        _log_episode(result, None)  # Should be a no-op

    def test_log_episode_without_assessment(self, store):
        """Should handle None assessment/enrichment gracefully."""
        result = ReasoningResult(
            answer="ok",
            question="test",
            assessment=None,
            enrichment=None,
        )
        _log_episode(result, store)

        episodes = store.get_reasoning_episodes(limit=1)
        assert len(episodes) == 1
        assert episodes[0]["confidence"] == 0.0
        assert episodes[0]["entities_added"] == 0
