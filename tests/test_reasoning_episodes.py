"""Tests for reasoning episode storage and retrieval in InstanceStore."""

import json

import pytest

from synapse.chat.reasoning import (
    _build_conversation_context,
    _estimate_tokens,
    _format_turn_compact,
    _format_turn_full,
    _log_episode,
    _summarize_turn_actions,
    _truncate_graph_obs,
    _truncate_section_obs,
)
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


class TestChatSessions:
    def test_create_session(self, store):
        sid = store.create_session("sess-001", domain="cooking")
        assert sid == "sess-001"

    def test_episode_with_session(self, store):
        store.create_session("sess-002", domain="cooking")
        eid = store.store_reasoning_episode(
            question="Q1", answer="A1", session_id="sess-002"
        )
        assert eid > 0

        episodes = store.get_session_episodes("sess-002")
        assert len(episodes) == 1
        assert episodes[0]["question"] == "Q1"
        assert episodes[0]["session_id"] == "sess-002"

    def test_session_episodes_ordered_oldest_first(self, store):
        store.create_session("sess-003")
        store.store_reasoning_episode(question="Q1", answer="A1", session_id="sess-003")
        store.store_reasoning_episode(question="Q2", answer="A2", session_id="sess-003")
        store.store_reasoning_episode(question="Q3", answer="A3", session_id="sess-003")

        episodes = store.get_session_episodes("sess-003")
        assert len(episodes) == 3
        assert episodes[0]["question"] == "Q1"
        assert episodes[2]["question"] == "Q3"

    def test_session_isolation(self, store):
        store.create_session("sess-A")
        store.create_session("sess-B")
        store.store_reasoning_episode(question="QA", answer="AA", session_id="sess-A")
        store.store_reasoning_episode(question="QB", answer="AB", session_id="sess-B")

        eps_a = store.get_session_episodes("sess-A")
        eps_b = store.get_session_episodes("sess-B")
        assert len(eps_a) == 1
        assert eps_a[0]["question"] == "QA"
        assert len(eps_b) == 1
        assert eps_b[0]["question"] == "QB"

    def test_episode_without_session(self, store):
        """Backward compat: session_id=None should still work."""
        eid = store.store_reasoning_episode(question="Q", answer="A")
        assert eid > 0
        episodes = store.get_reasoning_episodes(limit=1)
        assert episodes[0]["session_id"] is None

    def test_log_episode_with_session(self, store):
        store.create_session("sess-log")
        result = ReasoningResult(answer="42", question="What?")
        _log_episode(result, store, session_id="sess-log")

        episodes = store.get_session_episodes("sess-log")
        assert len(episodes) == 1
        assert episodes[0]["answer"] == "42"


class TestSessionManagement:
    def test_rename_session(self, store):
        store.create_session("sess-r1", domain="test")
        store.rename_session("sess-r1", "my-research")
        session = store.get_session_by_name("my-research")
        assert session is not None
        assert session["session_id"] == "sess-r1"

    def test_get_session_by_name_not_found(self, store):
        assert store.get_session_by_name("nonexistent") is None

    def test_get_last_session(self, store):
        store.create_session("sess-old", domain="test")
        store.create_session("sess-new", domain="test")
        last = store.get_last_session()
        assert last is not None
        assert last["session_id"] == "sess-new"

    def test_get_last_session_empty(self, store):
        assert store.get_last_session() is None

    def test_list_sessions(self, store):
        store.create_session("sess-l1")
        store.create_session("sess-l2")
        store.store_reasoning_episode(question="Q", answer="A", session_id="sess-l1")
        store.store_reasoning_episode(question="Q2", answer="A2", session_id="sess-l1")
        store.store_reasoning_episode(question="Q3", answer="A3", session_id="sess-l2")

        sessions = store.list_sessions()
        assert len(sessions) == 2
        # newest first
        assert sessions[0]["session_id"] == "sess-l2"
        assert sessions[1]["session_id"] == "sess-l1"
        assert sessions[1]["episode_count"] == 2
        assert sessions[0]["episode_count"] == 1

    def test_create_session_with_name(self, store):
        store.create_session("sess-named", name="alpha-research")
        session = store.get_session_by_name("alpha-research")
        assert session is not None
        assert session["session_id"] == "sess-named"

    def test_rebuild_chat_history_from_episodes(self, store):
        """Simulate what CLI does when resuming: rebuild chat_history from episodes."""
        store.create_session("sess-rebuild")
        store.store_reasoning_episode(
            question="Q1", answer="A1",
            actions_log=[{"tool": "GRAPH_QUERY", "args": "MATCH q1", "observation": "data1"}],
            section_ids=["s1"],
            session_id="sess-rebuild",
        )
        store.store_reasoning_episode(
            question="Q2", answer="A2",
            actions_log=[{"tool": "GRAPH_QUERY", "args": "MATCH q2", "observation": "data2"}],
            section_ids=["s2"],
            session_id="sess-rebuild",
        )

        episodes = store.get_session_episodes("sess-rebuild")
        chat_history = []
        for ep in episodes:
            import json
            chat_history.append({
                "question": ep["question"],
                "answer": ep["answer"],
                "actions_log": json.loads(ep["actions_log"]),
                "section_ids": json.loads(ep["section_ids"]),
            })

        assert len(chat_history) == 2
        assert chat_history[0]["question"] == "Q1"
        assert chat_history[0]["actions_log"][0]["tool"] == "GRAPH_QUERY"
        assert chat_history[1]["section_ids"] == ["s2"]


class TestTruncateGraphObs:
    def test_short_result_unchanged(self):
        obs = "jan kowalski | CEO\nmaria nowak | CTO"
        assert _truncate_graph_obs(obs) == obs

    def test_no_results_unchanged(self):
        assert _truncate_graph_obs("(no results)") == "(no results)"

    def test_empty_unchanged(self):
        assert _truncate_graph_obs("") == ""

    def test_truncates_at_row_boundary(self):
        rows = [f"entity_{i} | type_{i}" for i in range(20)]
        obs = "\n".join(rows)
        result = _truncate_graph_obs(obs, max_rows=5)
        result_lines = result.split("\n")
        # First 5 rows preserved intact
        for i in range(5):
            assert result_lines[i] == f"entity_{i} | type_{i}"
        # Last line is the summary
        assert "20 rows total" in result_lines[-1]
        assert "showing first 5" in result_lines[-1]

    def test_exact_limit_no_truncation(self):
        rows = [f"row_{i}" for i in range(5)]
        obs = "\n".join(rows)
        assert _truncate_graph_obs(obs, max_rows=5) == obs


class TestTruncateSectionObs:
    def test_short_text_unchanged(self):
        text = "This is a sentence. And another one."
        assert _truncate_section_obs(text) == text

    def test_empty_unchanged(self):
        assert _truncate_section_obs("") == ""

    def test_truncates_at_sentence_boundary(self):
        sentences = [f"Sentence number {i} here." for i in range(10)]
        text = " ".join(sentences)
        result = _truncate_section_obs(text, max_sentences=3)
        # First 3 sentences preserved
        assert "Sentence number 0 here." in result
        assert "Sentence number 1 here." in result
        assert "Sentence number 2 here." in result
        # 4th sentence NOT present
        assert "Sentence number 3 here." not in result
        assert "10 sentences total" in result

    def test_handles_question_marks_and_exclamations(self):
        text = "What happened? It exploded! Then it stopped."
        result = _truncate_section_obs(text, max_sentences=2)
        assert "What happened?" in result
        assert "It exploded!" in result
        assert "Then it stopped." not in result


class TestSummarizeTurnActions:
    def test_empty_actions(self):
        assert _summarize_turn_actions([]) == ""

    def test_answer_only_ignored(self):
        actions = [{"tool": "ANSWER", "args": "some answer", "observation": ""}]
        assert _summarize_turn_actions(actions) == ""

    def test_extracts_entity_names(self):
        actions = [
            {
                "tool": "GRAPH_QUERY",
                "args": "MATCH (n) RETURN n",
                "observation": "jan kowalski | Person\nmaria nowak | Person",
            }
        ]
        result = _summarize_turn_actions(actions)
        assert "jan kowalski" in result
        assert "maria nowak" in result
        assert "1 graph queries" in result

    def test_no_results_no_entities(self):
        actions = [
            {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n", "observation": "(no results)"}
        ]
        result = _summarize_turn_actions(actions)
        assert "1 graph queries" in result
        assert "Entities found" not in result


class TestBuildConversationContext:
    def test_empty_history(self):
        assert _build_conversation_context([]) == ""

    def test_single_turn(self):
        history = [
            {
                "question": "Who is CEO?",
                "answer": "Jan Kowalski",
                "actions_log": [
                    {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n", "observation": "jan kowalski | CEO"},
                ],
                "section_ids": [],
            }
        ]
        ctx = _build_conversation_context(history)
        assert "Turn 1:" in ctx
        assert "Who is CEO?" in ctx
        assert "Jan Kowalski" in ctx
        assert "MATCH (n) RETURN n" in ctx
        assert "jan kowalski | CEO" in ctx
        assert "CURRENT QUESTION" in ctx

    def test_budget_fills_from_newest(self):
        """With limited budget, newest turns should be included first."""
        # Use longer observations so each turn uses ~100 tokens
        history = [
            {
                "question": f"Question number {i} about the company",
                "answer": f"Answer {i}: " + "x" * 200,
                "actions_log": [
                    {"tool": "GRAPH_QUERY", "args": f"MATCH q{i}",
                     "observation": "\n".join([f"entity_{i}_{j} | Type" for j in range(5)])},
                ],
                "section_ids": [],
            }
            for i in range(5)
        ]
        # Large budget: all turns fit
        ctx = _build_conversation_context(history, max_tokens=10000)
        assert "MATCH q0" in ctx
        assert "MATCH q4" in ctx

        # Tiny budget: only newest turn(s) fit
        ctx_small = _build_conversation_context(history, max_tokens=120)
        assert "Question number 4" in ctx_small
        # Oldest turn should be dropped
        assert "MATCH q0" not in ctx_small

    def test_cached_summary_prepended(self):
        """When a cached summary exists, it should appear in the context."""
        # 3 turns total: first 2 compacted, 3rd uncompacted
        history = [
            {"question": "Q1", "answer": "A1", "actions_log": [], "section_ids": []},
            {"question": "Q2", "answer": "A2", "actions_log": [], "section_ids": []},
            {"question": "Q3", "answer": "A3", "actions_log": [], "section_ids": []},
        ]
        ctx = _build_conversation_context(
            history,
            cached_summary="CEO is Jan Kowalski of Acme Corp.",
            compacted_turns=2,
            max_tokens=5000,
        )
        assert "Summary of turns 1–2" in ctx
        assert "CEO is Jan Kowalski" in ctx
        # Only uncompacted turn (Q3) should appear as individual turn
        assert "Q3" in ctx
        # Compacted turns should NOT appear individually
        assert "Turn 1:" not in ctx

    def test_no_results_shown(self):
        history = [
            {
                "question": "Q",
                "answer": "A",
                "actions_log": [
                    {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n", "observation": "(no results)"},
                ],
                "section_ids": [],
            }
        ]
        ctx = _build_conversation_context(history)
        assert "(no results)" in ctx

    def test_graph_results_truncated_at_row_boundary(self):
        """Ensure long graph results are cut at full row boundaries."""
        rows = "\n".join([f"entity_{i} | type_{i}" for i in range(20)])
        history = [
            {
                "question": "Q",
                "answer": "A",
                "actions_log": [
                    {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n", "observation": rows},
                ],
                "section_ids": [],
            }
        ]
        ctx = _build_conversation_context(history)
        assert "rows total" in ctx
        # No cut mid-row
        assert "entity_0 | type_0" in ctx

    def test_section_text_truncated_at_sentence_boundary(self):
        """Section text should be cut at sentence boundaries."""
        sentences = " ".join([f"Sentence {i}." for i in range(20)])
        history = [
            {
                "question": "Q",
                "answer": "A",
                "actions_log": [
                    {"tool": "SECTION_TEXT", "args": "0042", "observation": sentences},
                ],
                "section_ids": ["0042"],
            }
        ]
        ctx = _build_conversation_context(history)
        assert "sentences total" in ctx
        assert "Sentence 0." in ctx


class TestSessionSummary:
    def test_update_and_retrieve_summary(self, store):
        store.create_session("sess-sum")
        store.update_session_summary("sess-sum", "CEO is Jan Kowalski.", 3)

        session = store.get_session_by_name("")  # won't find by empty name
        # Retrieve directly
        last = store.get_last_session()
        assert last is not None
        assert last["summary"] == "CEO is Jan Kowalski."
        assert last["compacted_turns"] == 3

    def test_summary_survives_resume(self, store):
        """Summary should be available when resuming a session."""
        store.create_session("sess-resume-sum", name="my-session")
        store.update_session_summary("sess-resume-sum", "Some summary", 5)

        session = store.get_session_by_name("my-session")
        assert session is not None
        assert session["summary"] == "Some summary"
        assert session["compacted_turns"] == 5

    def test_summary_initially_empty(self, store):
        store.create_session("sess-empty-sum")
        session = store.get_last_session()
        assert session["summary"] == ""
        assert session["compacted_turns"] == 0


class TestTokenBudget:
    def test_estimate_tokens(self):
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("abcd") == 1
        assert _estimate_tokens("a" * 400) == 100

    def test_full_vs_compact_format(self):
        """Full format should be larger than compact format when observations are substantial."""
        obs_rows = "\n".join([f"entity_{i} | Type_{i} | description_{i}" for i in range(10)])
        turn = {
            "question": "Who is CEO?",
            "answer": "Jan Kowalski",
            "actions_log": [
                {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n",
                 "observation": obs_rows},
                {"tool": "GRAPH_QUERY", "args": "MATCH (n)-[r]-(m) RETURN n,r,m",
                 "observation": obs_rows},
            ],
        }
        full = _format_turn_full(turn, 1)
        compact = _format_turn_compact(turn, 1)
        assert len(full) > len(compact)
        # Full has the raw query results
        assert "entity_0 | Type_0" in full
        # Compact has entity summary but not raw results
        assert "entity_0" in compact
        assert "2 graph queries" in compact

    def test_context_degrades_gracefully(self):
        """When budget is tight, turns degrade from full to compact to dropped."""
        turn = {
            "question": "Q",
            "answer": "A" * 200,
            "actions_log": [
                {"tool": "GRAPH_QUERY", "args": "MATCH (n) RETURN n",
                 "observation": "\n".join([f"row_{i}" for i in range(10)])},
            ],
            "section_ids": [],
        }
        history = [turn] * 6

        # Huge budget — all turns present
        ctx_big = _build_conversation_context(history, max_tokens=50000)
        for i in range(1, 7):
            assert f"Turn {i}:" in ctx_big

        # Tiny budget — not all turns fit
        ctx_tiny = _build_conversation_context(history, max_tokens=200)
        assert "Turn 6:" in ctx_tiny  # newest always present
