"""LLM-driven document structure extraction — TOC detection, tree building, subdivision, summaries."""

from __future__ import annotations

import hashlib
import logging
import re

from synapse.llm.client import LLMClient
from synapse.models.document import Document, Section
from synapse.parsers.pdf import estimate_tokens, extract_pages, pages_to_tagged_text

logger = logging.getLogger(__name__)


def _file_content_hash(pages: list[str]) -> str:
    """Compute SHA-256 hash of all page text for document ID."""
    h = hashlib.sha256()
    for page in pages:
        h.update(page.encode())
    return h.hexdigest()


async def detect_toc(pages: list[str], llm: LLMClient, scan_pages: int = 10) -> bool:
    """Use LLM to check if the first N pages contain a table of contents."""
    tagged = pages_to_tagged_text(pages, 1, min(scan_pages, len(pages)))
    system = "You are a document structure analyst."
    user = f"""Examine the first pages of this document and determine if they contain a Table of Contents (TOC).

{tagged}

Return ONLY a JSON object: {{"toc_detected": "yes"}} or {{"toc_detected": "no"}}"""

    try:
        result = await llm.complete_json_lenient(system=system, user=user)
        return isinstance(result, dict) and result.get("toc_detected", "").lower() == "yes"
    except Exception as e:
        logger.warning("TOC detection failed: %s", e)
        return False


async def extract_toc_structure(
    pages: list[str], llm: LLMClient, scan_pages: int = 10
) -> list[dict]:
    """Extract hierarchical section entries from the TOC pages."""
    tagged = pages_to_tagged_text(pages, 1, min(scan_pages, len(pages)))
    system = "You are a document structure analyst."
    user = f"""Extract the table of contents from these pages into a structured format.

{tagged}

Return a JSON array of sections:
[{{"structure": "1", "title": "Section Title", "start_page": 5}},
 {{"structure": "1.1", "title": "Subsection Title", "start_page": 6}},
 ...]

Rules:
- "structure" uses dot notation for hierarchy (1, 1.1, 1.1.1, etc.)
- "start_page" is the page number where the section begins
- Include ALL entries from the TOC
- Return ONLY the JSON array"""

    try:
        result = await llm.complete_json_lenient(system=system, user=user)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("sections", "toc", "data", "result"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []
    except Exception as e:
        logger.error("TOC structure extraction failed: %s", e)
        return []


async def extract_structure_no_toc(pages: list[str], llm: LLMClient) -> list[dict]:
    """Extract structure directly from the document content (no TOC available)."""
    tagged = pages_to_tagged_text(pages)
    system = "You are a document structure analyst."
    user = f"""Analyze this document and identify its hierarchical section structure.

{tagged}

Return a JSON array of sections:
[{{"structure": "1", "title": "Section Title", "start_page": 1}},
 {{"structure": "1.1", "title": "Subsection Title", "start_page": 2}},
 ...]

Rules:
- Identify natural section breaks by headings, topic changes, or formatting
- "structure" uses dot notation for hierarchy
- "start_page" is the page where the section begins
- Include ALL significant sections
- Return ONLY the JSON array"""

    try:
        result = await llm.complete_json_lenient(system=system, user=user)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("sections", "data", "result"):
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []
    except Exception as e:
        logger.error("Structure extraction failed: %s", e)
        return []


def verify_toc_accuracy(entries: list[dict], pages: list[str], tolerance: int = 2) -> float:
    """Verify TOC accuracy by checking if titles appear near claimed pages."""
    if not entries:
        return 0.0
    verified = 0
    for entry in entries:
        title = entry.get("title", "")
        start = entry.get("start_page", 0)
        if not title or start < 1:
            continue
        # Check pages within tolerance
        for offset in range(-tolerance, tolerance + 1):
            page_idx = start - 1 + offset
            if 0 <= page_idx < len(pages):
                # Normalize both for comparison
                title_words = set(re.findall(r"\w+", title.lower()))
                page_words = set(re.findall(r"\w+", pages[page_idx].lower()))
                if title_words and title_words.issubset(page_words):
                    verified += 1
                    break
    return verified / len(entries) if entries else 0.0


def build_section_tree(entries: list[dict], total_pages: int) -> list[Section]:
    """Build a hierarchical Section tree from flat TOC entries."""
    if not entries:
        return [
            Section(
                title="Full Document",
                start_page=1,
                end_page=total_pages,
                node_id="0001",
            )
        ]

    # Sort by start_page
    entries = sorted(entries, key=lambda e: e.get("start_page", 0))

    # Assign end pages (next section's start - 1, or total_pages)
    for i, entry in enumerate(entries):
        if i + 1 < len(entries):
            entry["end_page"] = entries[i + 1].get("start_page", total_pages) - 1
        else:
            entry["end_page"] = total_pages

    # Build flat list of sections with node_ids
    flat: list[dict] = []
    for i, entry in enumerate(entries):
        flat.append(
            {
                "structure": entry.get("structure", str(i + 1)),
                "title": entry.get("title", f"Section {i + 1}"),
                "start_page": max(1, entry.get("start_page", 1)),
                "end_page": max(1, entry.get("end_page", total_pages)),
                "node_id": f"{i + 1:04d}",
            }
        )

    return _nest_sections(flat)


def _nest_sections(flat: list[dict]) -> list[Section]:
    """Nest flat sections into a tree based on the 'structure' field (e.g., '1', '1.1', '1.2')."""
    sections: list[Section] = []
    stack: list[tuple[str, Section]] = []  # (structure, section)

    for item in flat:
        section = Section(
            title=item["title"],
            start_page=item["start_page"],
            end_page=item["end_page"],
            node_id=item["node_id"],
        )
        structure = item["structure"]
        depth = structure.count(".")

        # Pop stack until we find the parent
        while stack and stack[-1][0].count(".") >= depth:
            stack.pop()

        if stack:
            stack[-1][1].children.append(section)
        else:
            sections.append(section)

        stack.append((structure, section))

    return sections


def _chunk_by_pages(section: Section, max_pages: int) -> list[Section]:
    """Fallback: split a section into fixed-size page chunks."""
    children: list[Section] = []
    start = section.start_page
    idx = 1
    while start <= section.end_page:
        end = min(start + max_pages - 1, section.end_page)
        children.append(
            Section(
                title=f"{section.title} (pp. {start}-{end})",
                start_page=start,
                end_page=end,
                node_id=f"{section.node_id}{idx:02d}",
            )
        )
        start = end + 1
        idx += 1
    return children


async def subdivide_large_sections(
    sections: list[Section],
    pages: list[str],
    llm: LLMClient,
    max_pages: int = 10,
    max_tokens: int = 20000,
    _depth: int = 0,
) -> None:
    """Recursively subdivide sections that are too large.

    Strategy:
    1. Try LLM-based subdivision (find logical subsections)
    2. If LLM fails or children are still too large, recurse
    3. Final fallback: chunk by fixed page count
    """
    max_depth = 4  # prevent infinite recursion

    for section in sections:
        if section.children:
            await subdivide_large_sections(
                section.children, pages, llm, max_pages, max_tokens, _depth + 1
            )
            continue

        text = pages_to_tagged_text(pages, section.start_page, section.end_page)
        if section.page_count <= max_pages and estimate_tokens(text) <= max_tokens:
            continue

        logger.info(
            "Subdividing large section '%s' (%d pages, depth=%d)",
            section.title,
            section.page_count,
            _depth,
        )

        # At max depth or very large sections, skip LLM and chunk directly
        if _depth >= max_depth:
            logger.info("Max subdivision depth reached, chunking '%s' by pages", section.title)
            section.children = _chunk_by_pages(section, max_pages)
            continue

        # Try LLM-based subdivision
        llm_succeeded = False
        system = "You are a document structure analyst."
        user = f"""This section is too large and needs to be split into subsections.

Section: "{section.title}" (pages {section.start_page}-{section.end_page})

{text}

Identify 2-5 logical subsections within this content.
Return a JSON array:
[{{"title": "Subsection Title", "start_page": N}}, ...]

Return ONLY the JSON array."""

        try:
            result = await llm.complete_json_lenient(system=system, user=user)
            if isinstance(result, dict):
                for key in ("sections", "subsections", "data"):
                    if key in result and isinstance(result[key], list):
                        result = result[key]
                        break
                else:
                    result = []

            if isinstance(result, list) and len(result) >= 2:
                children: list[Section] = []
                for i, sub in enumerate(result):
                    start = sub.get("start_page", section.start_page)
                    end = (
                        result[i + 1].get("start_page", section.end_page) - 1
                        if i + 1 < len(result)
                        else section.end_page
                    )
                    children.append(
                        Section(
                            title=sub.get("title", f"Part {i + 1}"),
                            start_page=max(section.start_page, start),
                            end_page=min(section.end_page, end),
                            node_id=f"{section.node_id}{i + 1:02d}",
                        )
                    )
                section.children = children
                llm_succeeded = True
        except Exception as e:
            logger.warning("LLM subdivision failed for '%s': %s", section.title, e)

        if not llm_succeeded:
            # Fallback: chunk by pages
            logger.info("LLM subdivision failed, chunking '%s' by pages", section.title)
            section.children = _chunk_by_pages(section, max_pages)

        # Recurse into children — they may still be too large
        if section.children:
            await subdivide_large_sections(
                section.children, pages, llm, max_pages, max_tokens, _depth + 1
            )


async def generate_summaries(
    sections: list[Section],
    pages: list[str],
    llm: LLMClient,
) -> None:
    """Generate LLM summaries for all leaf sections."""
    for section in sections:
        if section.children:
            await generate_summaries(section.children, pages, llm)
            continue

        text = pages_to_tagged_text(pages, section.start_page, section.end_page)
        if not text.strip():
            section.summary = "(empty section)"
            continue

        # Truncate for summary prompt
        truncated = text[:6000]

        system = "You are a document summarizer. Write concise 1-2 sentence summaries."
        user = f"""Summarize this section in 1-2 sentences.

Section: "{section.title}" (pages {section.start_page}-{section.end_page})

{truncated}

Return ONLY the summary text, nothing else."""

        try:
            section.summary = await llm.complete(
                system=system,
                user=user,
                max_tokens=200,
            )
        except Exception as e:
            logger.warning("Summary generation failed for '%s': %s", section.title, e)
            section.summary = section.title

    # Assign text to leaf sections
    for section in sections:
        if section.children:
            continue
        section.text = pages_to_tagged_text(pages, section.start_page, section.end_page)


async def extract_document_structure(
    pdf_path: str,
    llm: LLMClient,
    toc_scan_pages: int = 10,
    toc_accuracy_threshold: float = 0.60,
    max_pages_per_node: int = 10,
    max_tokens_per_node: int = 20000,
) -> Document:
    """Full pipeline: PDF -> Document with hierarchical section tree.

    Steps:
    1. Extract pages from PDF
    2. Detect and extract TOC (or generate structure without TOC)
    3. Build section tree
    4. Subdivide large sections
    5. Generate summaries for leaf sections
    """
    from pathlib import Path

    pages = extract_pages(pdf_path)
    if not pages:
        raise ValueError(f"No pages extracted from {pdf_path}")

    doc_id = _file_content_hash(pages)
    filename = Path(pdf_path).name
    total_pages = len(pages)

    # Try TOC-based extraction
    has_toc = await detect_toc(pages, llm, toc_scan_pages)

    if has_toc:
        logger.info("TOC detected, extracting structure from TOC")
        entries = await extract_toc_structure(pages, llm, toc_scan_pages)
        accuracy = verify_toc_accuracy(entries, pages)
        logger.info("TOC accuracy: %.1f%%", accuracy * 100)

        if accuracy < toc_accuracy_threshold:
            logger.warning(
                "TOC accuracy too low (%.1f%%), falling back to content analysis", accuracy * 100
            )
            entries = await extract_structure_no_toc(pages, llm)
    else:
        logger.info("No TOC detected, extracting structure from content")
        entries = await extract_structure_no_toc(pages, llm)

    sections = build_section_tree(entries, total_pages)

    # Subdivide oversized sections
    await subdivide_large_sections(sections, pages, llm, max_pages_per_node, max_tokens_per_node)

    # Generate summaries
    await generate_summaries(sections, pages, llm)

    doc = Document(
        id=doc_id,
        filename=filename,
        title=filename.replace(".pdf", "").replace(".PDF", ""),
        page_count=total_pages,
        pages=pages,
        sections=sections,
    )

    logger.info(
        "Extracted document structure: %d sections (%d leaves) from %d pages",
        len(doc.all_sections()),
        len(doc.leaf_sections()),
        total_pages,
    )
    return doc
