"""ReAct reasoning loop for graph-RAG question answering.

Integrates three autonomous-learning features inspired by
Dupoux, LeCun & Malik (2026) — "Why AI systems don't learn":

1. **Enrichment loop** (System B → System A feedback):
   After answering, extracts new entities/relationships from the answer
   and merges them into the knowledge graph.

2. **Episode logging** (Episodic Memory):
   Stores complete reasoning traces (question, actions, answer, metrics)
   in SQLite for later replay and meta-cognition.

3. **Self-assessment** (Meta-cognition / System M telemetry):
   After answering, the LLM evaluates its own confidence, groundedness,
   and completeness — producing epistemic signals for future use.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from synapse.chat.prompts import (
    COMPACTION_SYSTEM,
    COMPACTION_USER,
    REASONING_SYSTEM,
    REASONING_USER,
    SELF_ASSESSMENT_SYSTEM,
    SELF_ASSESSMENT_USER,
)
from synapse.chat.retrieval import get_section_summaries, get_section_text, tree_search
from synapse.config import OntologyRegistry
from synapse.llm.client import LLMClient
from synapse.llm.templates import safe_format
from synapse.models.reasoning import EnrichmentResult, ReasoningResult, SelfAssessment
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

logger = logging.getLogger(__name__)

_CYPHER_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|DELETE|DETACH|SET|REMOVE|DROP|MERGE|CALL\s*\{)\b",
    re.IGNORECASE,
)


def _sanitize_cypher(raw: str) -> str:
    """Sanitize LLM-generated Cypher before execution."""
    cypher = raw.strip()
    # Strip 'cypher:' prefix some models prepend
    if cypher.lower().startswith("cypher:"):
        cypher = cypher[7:].strip()
    # Strip wrapping quotes
    if (cypher.startswith('"') and cypher.endswith('"')) or (
        cypher.startswith("'") and cypher.endswith("'")
    ):
        cypher = cypher[1:-1].strip()
    # Strip markdown code fences
    if cypher.startswith("```"):
        cypher = cypher.lstrip("`").strip()
        if cypher.lower().startswith("cypher"):
            cypher = cypher[6:].strip()
        if cypher.endswith("```"):
            cypher = cypher[:-3].strip()
    while cypher.endswith(")") and cypher.count("(") < cypher.count(")"):
        cypher = cypher[:-1].strip()
    if _CYPHER_WRITE_KEYWORDS.search(cypher):
        raise ValueError("Write operations are not allowed in reasoning queries.")
    return cypher


def _suggest_entity_alternatives(cypher: str, graph: GraphStore) -> str:
    """When a query returns no results, suggest similar entities."""
    contains_matches = re.findall(r"CONTAINS\s+['\"]([^'\"]+)['\"]", cypher, re.IGNORECASE)
    if not contains_matches:
        return ""
    suggestions: list[str] = []
    for term in contains_matches:
        words = term.split()
        for word in words:
            if len(word) < 3:
                continue
            try:
                result = graph.query(
                    "MATCH (n) WHERE NOT n:Document AND NOT n:Section "
                    "AND COALESCE(n.verified, true) = true "
                    f"AND toLower(n.canonical_name) CONTAINS toLower('{word}') "
                    "RETURN DISTINCT n.canonical_name, labels(n)[0] LIMIT 8"
                )
                for row in result:
                    entry = f"{row[0]} ({row[1]})"
                    if entry not in suggestions:
                        suggestions.append(entry)
            except Exception:
                continue
    if not suggestions:
        return ""
    suggestion_list = ", ".join(suggestions[:10])
    return f"\n\nHINT: No exact match found. Similar entities: [{suggestion_list}]."


def _truncate_to_first_action(text: str) -> tuple[str, bool]:
    """If the LLM emitted multiple Action: lines, keep only the first."""
    action_positions = [m.start() for m in re.finditer(r"^Action:\s*", text, re.MULTILINE)]
    if len(action_positions) <= 1:
        return text, False
    truncated = text[: action_positions[1]].rstrip()
    return truncated, True


def _parse_action(text: str) -> tuple[str, str, bool] | None:
    """Parse the first action from the LLM response."""
    text, was_multi = _truncate_to_first_action(text)

    # Standard format: Action: TOOL(args) — for single-line tools
    match = re.search(
        r"Action:\s*(GRAPH_QUERY|SECTION_TEXT)\s*\((.+)\)\s*$",
        text,
        re.MULTILINE,
    )
    if match:
        tool = match.group(1)
        args = match.group(2).strip()
        return tool, args, was_multi

    # ANSWER format — can be multi-line, greedy match to last closing paren
    match = re.search(
        r"Action:\s*ANSWER\s*\(\s*([\s\S]+)\)\s*$",
        text,
    )
    if match:
        args = match.group(1).strip()
        # Strip trailing ) if unbalanced (answer text may contain parens)
        while args.endswith(")") and args.count("(") < args.count(")"):
            args = args[:-1].rstrip()
        return "ANSWER", args, was_multi

    # Some models emit [TOOL_CALL] wrapper instead of Action: format
    tc_match = re.search(
        r'\[TOOL_CALL\]\s*\{tool\s*=>\s*"(\w+)".*?--text\s+"(.*?)"\s*\}\s*\}\s*\[/TOOL_CALL\]',
        text,
        re.DOTALL,
    )
    if tc_match:
        tool = tc_match.group(1).upper()
        args = tc_match.group(2).strip()
        if tool == "ANSWER":
            return "ANSWER", args, was_multi
        if tool in ("GRAPH_QUERY", "SECTION_TEXT"):
            return tool, args, was_multi

    return None


def _extract_inline_answer(text: str) -> str:
    """Extract an answer from text that doesn't use the ANSWER() format."""
    if not text or not text.strip():
        return "I was unable to determine an answer from the available information."
    for pattern in [
        r"(?:final\s+)?answer[:\s]+(.+)",
        r"(?:in\s+)?conclusion[:\s]+(.+)",
        r"(?:therefore|thus|so)[,:\s]+(.+)",
        r"(?:based on (?:the|my) (?:analysis|findings|queries?)[,:\s]+)(.+)",
    ]:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            answer = match.group(1).strip()
            if answer:
                return answer
    thought_matches = list(re.finditer(r"Thought:\s*(.+?)(?=\nAction:|\Z)", text, re.DOTALL))
    if thought_matches:
        last_thought = thought_matches[-1].group(1).strip()
        if len(last_thought) > 20:
            return last_thought
    return text.strip() if text.strip() else "I was unable to determine an answer."


def _format_query_result(result: list, limit: int = 20) -> str:
    """Format Cypher query results. Use limit=0 for all rows."""
    if not result:
        return "(no results)"
    rows = result if limit == 0 else result[:limit]
    lines: list[str] = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            lines.append(" | ".join(str(cell) for cell in row))
        else:
            lines.append(str(row))
    if limit and len(result) > limit:
        lines.append(f"... ({len(result)} total rows)")
    return "\n".join(lines)


ChatTurn = dict[str, object]
"""A single turn in chat history: question, answer, actions_log, section_ids."""

# Max rows from a graph query result to keep in full-detail turns.
_MAX_RESULT_ROWS = 15
# Max sentences from section text to keep in full-detail turns.
_MAX_SECTION_SENTENCES = 5
# Default token budget for the conversation context block.
_DEFAULT_CONTEXT_MAX_TOKENS = 4000


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return len(text) // 4


def _truncate_graph_obs(obs: str, max_rows: int = _MAX_RESULT_ROWS) -> str:
    """Truncate graph query observation at row (line) boundaries."""
    if not obs or obs == "(no results)":
        return obs
    rows = obs.split("\n")
    if len(rows) <= max_rows:
        return obs
    kept = "\n".join(rows[:max_rows])
    return f"{kept}\n... ({len(rows)} rows total, showing first {max_rows})"


def _truncate_section_obs(obs: str, max_sentences: int = _MAX_SECTION_SENTENCES) -> str:
    """Truncate section text at sentence boundaries."""
    if not obs:
        return obs
    sentences: list[str] = []
    current = ""
    for char in obs:
        current += char
        if char in ".!?" and len(current.strip()) > 1:
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())

    if len(sentences) <= max_sentences:
        return obs
    kept = " ".join(sentences[:max_sentences])
    return f"{kept} [...{len(sentences)} sentences total]"


def _summarize_turn_actions(actions: list[dict[str, str]]) -> str:
    """Produce a compact summary of actions for older (compressed) turns.

    Lists Cypher queries and key entity names found — so the LLM knows
    what data was already explored.
    """
    if not actions:
        return ""
    queries: list[str] = []
    entities_found: list[str] = []
    for act in actions:
        tool = act.get("tool", "")
        if tool == "ANSWER":
            continue
        if tool == "GRAPH_QUERY":
            queries.append(act.get("args", ""))
            obs = act.get("observation", "")
            if obs and obs != "(no results)":
                for line in obs.split("\n")[:8]:
                    parts = line.split(" | ")
                    if parts and parts[0].strip():
                        name = parts[0].strip()
                        if name and name not in entities_found:
                            entities_found.append(name)
    summary_parts: list[str] = []
    if queries:
        summary_parts.append(f"    Queries: {len(queries)} graph queries executed")
    if entities_found:
        summary_parts.append(f"    Entities found: {', '.join(entities_found[:10])}")
    return "\n".join(summary_parts)


def _format_turn_full(turn: ChatTurn, turn_num: int) -> str:
    """Format a single turn with full graph query results."""
    question = turn.get("question", "")
    answer = turn.get("answer", "")
    actions: list[dict[str, str]] = turn.get("actions_log", [])  # type: ignore[assignment]

    lines: list[str] = [f"Turn {turn_num}:"]
    lines.append(f"  Question: {question}")

    if actions:
        query_lines: list[str] = []
        for act in actions:
            tool = act.get("tool", "")
            if tool == "ANSWER":
                continue
            args = act.get("args", "")
            obs = act.get("observation", "")
            if tool == "GRAPH_QUERY":
                obs = _truncate_graph_obs(obs)
                entry = f"    {args}"
                if obs and obs != "(no results)":
                    entry += f" → {obs}"
                elif obs == "(no results)":
                    entry += " → (no results)"
                query_lines.append(entry)
            elif tool == "SECTION_TEXT":
                obs = _truncate_section_obs(obs)
                entry = f"    SECTION_TEXT({args}) → {obs}" if obs else f"    SECTION_TEXT({args})"
                query_lines.append(entry)
        if query_lines:
            lines.append("  Graph queries & results:")
            lines.extend(query_lines)

    lines.append(f"  Answer: {answer}")
    return "\n".join(lines)


def _format_turn_compact(turn: ChatTurn, turn_num: int) -> str:
    """Format a turn with only entity summary (no full results)."""
    question = turn.get("question", "")
    answer = turn.get("answer", "")
    actions: list[dict[str, str]] = turn.get("actions_log", [])  # type: ignore[assignment]

    lines: list[str] = [f"Turn {turn_num}:"]
    lines.append(f"  Question: {question}")
    if actions:
        summary = _summarize_turn_actions(actions)
        if summary:
            lines.append("  Context from this turn:")
            lines.append(summary)
    lines.append(f"  Answer: {answer}")
    return "\n".join(lines)


def _format_turns_for_compaction(turns: list[ChatTurn]) -> str:
    """Format turns for the compaction LLM prompt (full detail)."""
    parts: list[str] = []
    for i, turn in enumerate(turns):
        parts.append(_format_turn_full(turn, i + 1))
    return "\n\n".join(parts)


async def compact_history(
    turns: list[ChatTurn],
    llm: LLMClient,
    existing_summary: str = "",
) -> str:
    """Use LLM to produce a structured summary of conversation turns.

    If an existing_summary is provided (from prior compaction), it is
    included so the new summary builds on top of it.
    """
    turns_text = _format_turns_for_compaction(turns)
    if existing_summary:
        turns_text = (
            f"PREVIOUS SUMMARY (from earlier turns):\n{existing_summary}\n\n"
            f"NEW TURNS TO INCORPORATE:\n{turns_text}"
        )

    from synapse.llm.templates import safe_format

    user_prompt = safe_format(COMPACTION_USER, turns_text=turns_text)

    try:
        summary = await llm.complete(
            system=COMPACTION_SYSTEM,
            user=user_prompt,
            temperature=0.0,
            max_tokens=1024,
        )
        return summary.strip()
    except Exception as e:
        logger.warning("Compaction LLM call failed: %s", e)
        # Fallback: entity-based summary without LLM
        parts: list[str] = []
        for turn in turns:
            actions: list[dict[str, str]] = turn.get("actions_log", [])  # type: ignore[assignment]
            summary = _summarize_turn_actions(actions)
            if summary:
                parts.append(summary)
        return "\n".join(parts) if parts else ""


def _build_conversation_context(
    chat_history: list[ChatTurn],
    cached_summary: str = "",
    compacted_turns: int = 0,
    max_tokens: int = _DEFAULT_CONTEXT_MAX_TOKENS,
) -> str:
    """Format previous turns for injection into the reasoning prompt.

    Uses a token budget: fills from newest turns backward with full detail.
    If a cached LLM summary exists for older turns, it's prepended.
    If budget is still exceeded, older full turns degrade to compact form.
    """
    if not chat_history:
        return ""

    # Turns that haven't been compacted yet
    uncompacted = chat_history[compacted_turns:]
    if not uncompacted and not cached_summary:
        return ""

    header = "═══ CONVERSATION CONTEXT (previous turns) ═══\n"
    footer = "\n═══ CURRENT QUESTION ═══\n"
    overhead = _estimate_tokens(header + footer)
    budget = max_tokens - overhead

    # Reserve space for cached summary if present
    summary_block = ""
    if cached_summary:
        summary_block = f"[Summary of turns 1–{compacted_turns}]\n{cached_summary}\n"
        budget -= _estimate_tokens(summary_block)

    # Build turn blocks from newest to oldest, tracking budget
    turn_blocks: list[tuple[int, str]] = []  # (original_index, formatted_text)
    tokens_used = 0

    for rev_idx, turn in enumerate(reversed(uncompacted)):
        original_idx = len(chat_history) - 1 - rev_idx
        turn_num = original_idx + 1

        # Try full format first
        full_text = _format_turn_full(turn, turn_num)
        full_tokens = _estimate_tokens(full_text)

        if tokens_used + full_tokens <= budget:
            turn_blocks.append((original_idx, full_text))
            tokens_used += full_tokens
        else:
            # Try compact format
            compact_text = _format_turn_compact(turn, turn_num)
            compact_tokens = _estimate_tokens(compact_text)
            if tokens_used + compact_tokens <= budget:
                turn_blocks.append((original_idx, compact_text))
                tokens_used += compact_tokens
            else:
                # Budget exhausted — skip remaining older turns
                break

    # Reverse to chronological order
    turn_blocks.reverse()

    parts: list[str] = []
    if summary_block:
        parts.append(summary_block)
    for _, text in turn_blocks:
        parts.append(text)

    if not parts:
        return ""

    return header + "\n".join(parts) + footer


def _build_evidence_summary(actions_log: list[dict[str, str]]) -> str:
    """Build a concise summary of evidence gathered during reasoning for self-assessment."""
    evidence_parts: list[str] = []
    for entry in actions_log:
        tool = entry.get("tool", "")
        observation = entry.get("observation", "")
        if tool == "GRAPH_QUERY" and observation and "(no results)" not in observation:
            # Truncate long query results
            obs_truncated = observation[:500] + "..." if len(observation) > 500 else observation
            evidence_parts.append(f"[Graph] {obs_truncated}")
        elif tool == "SECTION_TEXT" and observation:
            obs_truncated = observation[:300] + "..." if len(observation) > 300 else observation
            evidence_parts.append(f"[Section] {obs_truncated}")
    if not evidence_parts:
        return "(no evidence gathered from graph or documents)"
    return "\n".join(evidence_parts[:10])


async def _assess_answer(
    question: str,
    answer: str,
    actions_log: list[dict[str, str]],
    llm: LLMClient,
    store: InstanceStore | None = None,
) -> SelfAssessment:
    """Run LLM self-assessment on the generated answer."""
    evidence_summary = _build_evidence_summary(actions_log)

    system = SELF_ASSESSMENT_SYSTEM
    user_template = SELF_ASSESSMENT_USER
    if store:
        custom_system = store.get_prompt("self_assessment_system")
        custom_user = store.get_prompt("self_assessment_user")
        if custom_system:
            system = custom_system
        if custom_user:
            user_template = custom_user

    user_prompt = safe_format(
        user_template,
        question=question,
        answer=answer,
        evidence_summary=evidence_summary,
    )

    try:
        data: Any = await llm.complete_json_lenient(
            system=system, user=user_prompt, temperature=0.0, max_tokens=1024
        )
    except Exception as e:
        logger.warning("Self-assessment LLM call failed: %s", e)
        return SelfAssessment()

    if not isinstance(data, dict):
        return SelfAssessment()

    return SelfAssessment(
        confidence=float(data.get("confidence", 0.0)),
        groundedness=float(data.get("groundedness", 0.0)),
        completeness=float(data.get("completeness", 0.0)),
        reasoning=str(data.get("reasoning", "")),
        gaps=[str(g) for g in data.get("gaps", []) if isinstance(g, str)],
    )


async def _enrich_from_answer(
    answer: str,
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    store: InstanceStore | None = None,
) -> EnrichmentResult:
    """Extract new knowledge from the answer and merge into the graph."""
    from synapse.chat.enrichment import enrich_graph_from_answer

    try:
        entities_added, rels_added = await enrich_graph_from_answer(
            answer=answer,
            question=question,
            graph=graph,
            llm=llm,
            ontology=ontology,
            store=store,
        )
        return EnrichmentResult(entities_added=entities_added, relationships_added=rels_added)
    except Exception as e:
        logger.warning("Enrichment failed: %s", e)
        return EnrichmentResult()


def _log_episode(
    result: ReasoningResult,
    store: InstanceStore | None,
    session_id: str | None = None,
) -> None:
    """Persist a reasoning episode to the instance store."""
    if not store:
        return
    try:
        assessment = result.assessment or SelfAssessment()
        enrichment = result.enrichment or EnrichmentResult()
        store.store_reasoning_episode(
            question=result.question,
            answer=result.answer,
            steps_taken=result.steps_taken,
            empty_results=result.empty_result_count,
            timed_out=result.timed_out,
            max_steps_reached=result.max_steps_reached,
            doom_loop_triggered=result.doom_loop_triggered,
            elapsed_seconds=result.elapsed_seconds,
            section_ids=result.section_ids_used,
            actions_log=result.actions_log,
            confidence=assessment.confidence,
            groundedness=assessment.groundedness,
            completeness=assessment.completeness,
            assessment_reasoning=assessment.reasoning,
            assessment_gaps=assessment.gaps,
            entities_added=enrichment.entities_added,
            rels_added=enrichment.relationships_added,
            session_id=session_id,
        )
    except Exception as e:
        logger.warning("Failed to log reasoning episode: %s", e)


# ── Main entry point ──────────────────────────────────────


async def reason(
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    max_steps: int = 20,
    doom_threshold: int = 5,
    verbose: bool = False,
    text_cache: TextCache | None = None,
    reasoning_timeout: float = 300,
    step_max_tokens: int = 2048,
    store: InstanceStore | None = None,
    chat_history: list[ChatTurn] | None = None,
    session_id: str | None = None,
    cached_summary: str = "",
    compacted_turns: int = 0,
    context_max_tokens: int = _DEFAULT_CONTEXT_MAX_TOKENS,
) -> str:
    """Execute a ReAct reasoning loop to answer a question.

    This is the backward-compatible entry point that returns just the answer string.
    For the full result including assessment and enrichment data, use reason_full().
    """
    result = await reason_full(
        question=question,
        graph=graph,
        llm=llm,
        ontology=ontology,
        max_steps=max_steps,
        doom_threshold=doom_threshold,
        verbose=verbose,
        text_cache=text_cache,
        reasoning_timeout=reasoning_timeout,
        step_max_tokens=step_max_tokens,
        store=store,
        chat_history=chat_history,
        session_id=session_id,
        cached_summary=cached_summary,
        compacted_turns=compacted_turns,
        context_max_tokens=context_max_tokens,
    )
    return result.answer


async def reason_full(
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    max_steps: int = 20,
    doom_threshold: int = 5,
    verbose: bool = False,
    text_cache: TextCache | None = None,
    reasoning_timeout: float = 300,
    step_max_tokens: int = 2048,
    store: InstanceStore | None = None,
    chat_history: list[ChatTurn] | None = None,
    session_id: str | None = None,
    cached_summary: str = "",
    compacted_turns: int = 0,
    context_max_tokens: int = _DEFAULT_CONTEXT_MAX_TOKENS,
) -> ReasoningResult:
    """Execute a ReAct reasoning loop with enrichment, self-assessment, and episode logging.

    Returns a ReasoningResult containing the answer plus all metadata.
    """
    t0 = time.monotonic()

    # Phase 1: Tree search
    section_ids = await tree_search(question, graph, llm, store)
    section_summaries = get_section_summaries(section_ids, graph, text_cache)

    # Phase 2: ReAct loop — use generated prompts if available
    system_prompt = REASONING_SYSTEM
    user_template = REASONING_USER

    if store:
        custom_system = store.get_prompt("reasoning_system")
        custom_user = store.get_prompt("reasoning_user")
        if custom_system:
            system_prompt = custom_system
        if custom_user:
            user_template = custom_user

    system_formatted = safe_format(
        system_prompt,
        entity_types=", ".join(sorted(ontology.entity_types.keys())),
        relationship_types=", ".join(sorted(ontology.relationship_types.keys())),
    )
    conversation_context = _build_conversation_context(
        chat_history or [],
        cached_summary=cached_summary,
        compacted_turns=compacted_turns,
        max_tokens=context_max_tokens,
    )
    user_formatted = safe_format(
        user_template,
        question=question,
        section_summaries=section_summaries,
        conversation_context=conversation_context,
    )

    messages = [
        {"role": "system", "content": system_formatted},
        {"role": "user", "content": user_formatted},
    ]

    empty_result_count = 0
    total_empty_results = 0
    actions_log: list[dict[str, str]] = []
    doom_loop_triggered = False
    timed_out = False
    max_steps_reached = False
    answer = ""
    steps_completed = 0

    for step in range(max_steps):
        elapsed = time.monotonic() - t0
        if elapsed >= reasoning_timeout:
            logger.warning("Reasoning timeout at step %d", step + 1)
            timed_out = True
            break

        if verbose:
            print(f"\n--- Step {step + 1} ({elapsed:.1f}s) ---")

        response = await llm.complete_messages(
            messages=messages, temperature=0.0, max_tokens=step_max_tokens
        )

        if verbose:
            print(response)

        if len(response) > 600 and "Action:" not in response:
            truncated_for_history = "..." + response[-300:]
        else:
            truncated_for_history = response
        messages.append({"role": "assistant", "content": truncated_for_history})

        action = _parse_action(response)
        if action is None:
            # Log the unparsed response as a "THOUGHT" step
            actions_log.append({"tool": "THOUGHT", "args": "", "observation": response})
            if "answer" in response.lower() and step > 0:
                answer = _extract_inline_answer(response)
                steps_completed = step + 1
                break
            messages.append(
                {
                    "role": "user",
                    "content": "You did NOT provide an action. Every response MUST end with "
                    "exactly one of:\n"
                    "Action: GRAPH_QUERY(MATCH ...)\n"
                    "Action: SECTION_TEXT(section_id)\n"
                    "Action: ANSWER(your answer)\n"
                    "Do it now.",
                }
            )
            continue

        tool, args, had_multi = action

        if tool == "ANSWER":
            answer = args
            steps_completed = step + 1
            actions_log.append({"tool": "ANSWER", "args": "", "observation": args})
            break

        multi_warning = ""
        if had_multi:
            multi_warning = "\n\n(NOTE: Multiple actions detected. Only the first was executed.)"

        if tool == "GRAPH_QUERY":
            try:
                sanitized = _sanitize_cypher(args)
                result = graph.query(sanitized)
                if result:
                    result_text = _format_query_result(result)
                    result_full = _format_query_result(result, limit=0)
                    empty_result_count = 0
                else:
                    hint = _suggest_entity_alternatives(sanitized, graph)
                    result_text = f"(no results){hint}"
                    result_full = result_text
                    empty_result_count += 1
                    total_empty_results += 1
            except Exception as e:
                result_text = f"(query error: {e})"
                result_full = result_text
                empty_result_count += 1
                total_empty_results += 1

            actions_log.append({"tool": "GRAPH_QUERY", "args": args, "observation": result_full})

            if verbose:
                print(f"Result: {result_text[:500]}")
            messages.append(
                {"role": "user", "content": f"Query result:\n{result_text}{multi_warning}"}
            )

        elif tool == "SECTION_TEXT":
            text = get_section_text(args, graph, text_cache)
            actions_log.append({"tool": "SECTION_TEXT", "args": args, "observation": text})
            if verbose:
                print(f"Section: {text[:500]}")
            messages.append({"role": "user", "content": f"Section text:\n{text}{multi_warning}"})

        steps_completed = step + 1

        if empty_result_count >= doom_threshold:
            logger.warning("Doom loop detected, forcing answer")
            doom_loop_triggered = True
            messages.append(
                {
                    "role": "user",
                    "content": "Multiple queries returned no results. STOP querying. "
                    "Provide your best answer using ANSWER() now.",
                }
            )
            empty_result_count = 0

    # If no answer was produced in the loop, force a final answer
    if not answer:
        if not timed_out:
            max_steps_reached = True
        messages.append(
            {
                "role": "user",
                "content": "Maximum steps reached. Provide your final answer using ANSWER() now.",
            }
        )
        final = await llm.complete_messages(messages=messages, temperature=0.0, max_tokens=1024)
        final_action = _parse_action(final)
        if final_action and final_action[0] == "ANSWER":
            answer = final_action[1]
        else:
            answer = _extract_inline_answer(final)

    elapsed_total = time.monotonic() - t0

    # Phase 3: Self-assessment — evaluate answer quality
    assessment = await _assess_answer(
        question=question,
        answer=answer,
        actions_log=actions_log,
        llm=llm,
        store=store,
    )

    if verbose and assessment:
        print(
            f"\n--- Self-Assessment ---\n"
            f"Confidence: {assessment.confidence:.2f}  "
            f"Groundedness: {assessment.groundedness:.2f}  "
            f"Completeness: {assessment.completeness:.2f}\n"
            f"Reasoning: {assessment.reasoning}"
        )
        if assessment.gaps:
            print(f"Gaps: {', '.join(assessment.gaps)}")

    # Phase 4: Enrichment — extract new knowledge from the answer
    enrichment = await _enrich_from_answer(
        answer=answer,
        question=question,
        graph=graph,
        llm=llm,
        ontology=ontology,
        store=store,
    )

    if verbose and enrichment:
        added = enrichment.entities_added + enrichment.relationships_added
        if added > 0:
            print(
                f"\n--- Enrichment ---\n"
                f"Added {enrichment.entities_added} entities, "
                f"{enrichment.relationships_added} relationships to graph"
            )

    # Build result
    result = ReasoningResult(
        answer=answer,
        question=question,
        steps_taken=steps_completed,
        empty_result_count=total_empty_results,
        timed_out=timed_out,
        max_steps_reached=max_steps_reached,
        doom_loop_triggered=doom_loop_triggered,
        elapsed_seconds=round(elapsed_total, 2),
        section_ids_used=section_ids,
        actions_log=actions_log,
        assessment=assessment,
        enrichment=enrichment,
    )

    # Phase 5: Log episode to SQLite
    _log_episode(result, store, session_id=session_id)

    return result
