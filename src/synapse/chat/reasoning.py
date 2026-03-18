"""ReAct reasoning loop for graph-RAG question answering."""

from __future__ import annotations

import logging
import re
import time

from synapse.chat.prompts import REASONING_SYSTEM, REASONING_USER
from synapse.chat.retrieval import get_section_summaries, get_section_text, tree_search
from synapse.config import OntologyRegistry
from synapse.llm.templates import safe_format
from synapse.llm.client import LLMClient
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
    if (cypher.startswith('"') and cypher.endswith('"')) or (
        cypher.startswith("'") and cypher.endswith("'")
    ):
        cypher = cypher[1:-1].strip()
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
    match = re.search(
        r"Action:\s*(GRAPH_QUERY|SECTION_TEXT|ANSWER)\s*\((.+)\)\s*$",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        tool = match.group(1)
        args = match.group(2).strip()
        if args.endswith(")") and args.count("(") < args.count(")"):
            args = args[:-1]
        return tool, args, was_multi
    match = re.search(r"Action:\s*ANSWER\s*\(\s*([\s\S]+?)(?:\)\s*$)", text, re.MULTILINE)
    if match:
        return "ANSWER", match.group(1).strip(), was_multi
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
) -> str:
    """Execute a ReAct reasoning loop to answer a question."""
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
    user_formatted = safe_format(
        user_template,
        question=question,
        section_summaries=section_summaries,
    )

    messages = [
        {"role": "system", "content": system_formatted},
        {"role": "user", "content": user_formatted},
    ]

    empty_result_count = 0

    for step in range(max_steps):
        elapsed = time.monotonic() - t0
        if elapsed >= reasoning_timeout:
            logger.warning("Reasoning timeout at step %d", step + 1)
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
            if "answer" in response.lower() and step > 0:
                return _extract_inline_answer(response)
            messages.append(
                {
                    "role": "user",
                    "content": "You did NOT provide an action. Every response MUST end with exactly one of:\n"
                    "Action: GRAPH_QUERY(MATCH ...)\nAction: SECTION_TEXT(section_id)\nAction: ANSWER(your answer)\n"
                    "Do it now.",
                }
            )
            continue

        tool, args, had_multi = action

        if tool == "ANSWER":
            return args

        multi_warning = ""
        if had_multi:
            multi_warning = "\n\n(NOTE: Multiple actions detected. Only the first was executed.)"

        if tool == "GRAPH_QUERY":
            try:
                sanitized = _sanitize_cypher(args)
                result = graph.query(sanitized)
                if result:
                    result_text = _format_query_result(result)
                    empty_result_count = 0
                else:
                    hint = _suggest_entity_alternatives(sanitized, graph)
                    result_text = f"(no results){hint}"
                    empty_result_count += 1
            except Exception as e:
                result_text = f"(query error: {e})"
                empty_result_count += 1

            if verbose:
                print(f"Result: {result_text[:500]}")
            messages.append(
                {"role": "user", "content": f"Query result:\n{result_text}{multi_warning}"}
            )

        elif tool == "SECTION_TEXT":
            text = get_section_text(args, graph, text_cache)
            if verbose:
                print(f"Section: {text[:500]}")
            messages.append({"role": "user", "content": f"Section text:\n{text}{multi_warning}"})

        if empty_result_count >= doom_threshold:
            logger.warning("Doom loop detected, forcing answer")
            messages.append(
                {
                    "role": "user",
                    "content": "Multiple queries returned no results. STOP querying. "
                    "Provide your best answer using ANSWER() now.",
                }
            )
            empty_result_count = 0

    # Max steps or timeout — force final answer
    messages.append(
        {
            "role": "user",
            "content": "Maximum steps reached. Provide your final answer using ANSWER() now.",
        }
    )
    final = await llm.complete_messages(messages=messages, temperature=0.0, max_tokens=1024)
    final_action = _parse_action(final)
    if final_action and final_action[0] == "ANSWER":
        return final_action[1]
    return _extract_inline_answer(final)


def _format_query_result(result: list) -> str:
    if not result:
        return "(no results)"
    lines: list[str] = []
    for row in result[:20]:
        if isinstance(row, (list, tuple)):
            lines.append(" | ".join(str(cell) for cell in row))
        else:
            lines.append(str(row))
    if len(result) > 20:
        lines.append(f"... ({len(result)} total rows)")
    return "\n".join(lines)
