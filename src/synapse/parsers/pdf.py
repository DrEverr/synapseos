"""PDF text extraction using PyMuPDF."""

from __future__ import annotations

import logging

import pymupdf

logger = logging.getLogger(__name__)


def extract_pages(pdf_path: str) -> list[str]:
    """Extract text from each page of a PDF, returning a list of page texts."""
    doc = pymupdf.open(pdf_path)
    pages: list[str] = []
    for page in doc:
        text = page.get_text()
        pages.append(text)
    doc.close()
    logger.info("Extracted %d pages from %s", len(pages), pdf_path)
    return pages


def pages_to_tagged_text(pages: list[str], start_page: int = 1, end_page: int | None = None) -> str:
    """Wrap page texts in positional XML tags for LLM consumption.

    Returns text like:
        <page_1>contents of page 1</page_1>
        <page_2>contents of page 2</page_2>
    """
    if end_page is None:
        end_page = len(pages)
    parts: list[str] = []
    for i in range(start_page - 1, min(end_page, len(pages))):
        page_num = i + 1
        text = pages[i].strip()
        if text:
            parts.append(f"<page_{page_num}>{text}</page_{page_num}>")
    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token)."""
    return len(text) // 4
