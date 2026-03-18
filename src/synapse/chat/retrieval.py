"""Graph-based retrieval for the chat system — tree search and section text."""

from __future__ import annotations

import json
import logging

from synapse.chat.prompts import TREE_SEARCH_SYSTEM, TREE_SEARCH_USER
from synapse.llm.client import LLMClient
from synapse.llm.templates import safe_format
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

logger = logging.getLogger(__name__)


def _format_section_tree(sections: list[dict], indent: int = 0) -> str:
    """Recursively format a section tree to text at any depth."""
    prefix = "  " * indent
    text = ""
    for section in sections:
        title = section.get("title", "Unknown")
        summary = section.get("summary", "")
        node_id = section.get("node_id", "")
        start_p = section.get("start_page", "?")
        end_p = section.get("end_page", "?")
        summary_part = f" — {summary}" if summary else ""
        text += f"{prefix}[{node_id}] {title} (pp. {start_p}-{end_p}){summary_part}\n"
        children = section.get("children", [])
        if children:
            text += _format_section_tree(children, indent + 1)
    return text


async def tree_search(
    query: str,
    graph: GraphStore,
    llm: LLMClient,
    store: InstanceStore | None = None,
) -> list[str]:
    """Phase 1: Use document trees to find relevant section IDs."""
    docs = graph.get_documents()
    if not docs:
        logger.warning("No documents in graph for tree search")
        return []

    tree_parts: list[str] = []
    for doc in docs:
        tree_json = doc.get("tree_json", "[]")
        try:
            tree = json.loads(tree_json) if tree_json else []
        except json.JSONDecodeError:
            tree = []
        tree_text = f"\nDocument: {doc['title']} ({doc['filename']})\n"
        tree_text += _format_section_tree(tree, indent=1)
        tree_parts.append(tree_text)

    document_trees = "\n".join(tree_parts)

    # Get prompts from store or fallback
    system = TREE_SEARCH_SYSTEM
    user_template = TREE_SEARCH_USER
    if store:
        custom_system = store.get_prompt("tree_search_system")
        custom_user = store.get_prompt("tree_search_user")
        if custom_system:
            system = custom_system
        if custom_user:
            user_template = custom_user

    user_prompt = safe_format(user_template, query=query, document_trees=document_trees)

    try:
        result = await llm.complete_json_lenient(system=system, user=user_prompt)
        sections = result.get("sections", []) if isinstance(result, dict) else []
        logger.info("Tree search found %d relevant sections", len(sections))
        return sections
    except Exception as e:
        logger.error("Tree search failed: %s", e)
        return []


def get_section_text(
    section_id: str,
    graph: GraphStore,
    text_cache: TextCache | None = None,
) -> str:
    """Retrieve the full text of a section by its node_id."""
    result = graph.query(
        """MATCH (s:Section)
           WHERE s.node_id = $node_id OR s.id ENDS WITH $suffix
           RETURN s.title, s.summary, s.start_page, s.end_page, s.id
           LIMIT 1""",
        params={"node_id": section_id, "suffix": f":{section_id}"},
    )
    if not result:
        return f"Section {section_id} not found"

    row = result[0]
    title, summary, start_page, end_page, full_id = row[0], row[1], row[2], row[3], row[4]

    full_text = None
    if text_cache is not None:
        full_text = text_cache.get(full_id) or text_cache.get(section_id)

    if full_text:
        return f"Section: {title}\nPages: {start_page}-{end_page}\n\nFull text:\n{full_text}"
    else:
        return f"Section: {title}\nPages: {start_page}-{end_page}\nSummary: {summary}"


def get_section_summaries(
    section_ids: list[str],
    graph: GraphStore,
    text_cache: TextCache | None = None,
) -> str:
    """Get formatted summaries for a list of section IDs."""
    parts: list[str] = []
    for sid in section_ids:
        text = get_section_text(sid, graph, text_cache)
        parts.append(text)
    return "\n\n".join(parts) if parts else "No relevant sections found."
