"""Text file parsers for non-PDF formats: Markdown, plain text, HTML.

Each parser returns a list of "page" strings (chunks) compatible with the
existing Document model and pages_to_tagged_text() function.
"""

from __future__ import annotations

import re
from pathlib import Path


def extract_pages_from_markdown(path: str) -> list[str]:
    """Split a Markdown file into chunks by top-level headers (# or ##).

    Each chunk includes the header and all content until the next header
    of the same or higher level. If no headers are found, splits by
    double newlines into paragraph-based chunks.
    """
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        return [""]

    # Split by level-1 or level-2 headers
    parts = re.split(r"(?=^#{1,2}\s)", text, flags=re.MULTILINE)
    chunks = [p.strip() for p in parts if p.strip()]

    if len(chunks) <= 1:
        # No headers found — fall back to paragraph splitting
        return _split_by_paragraphs(text)

    return chunks


def extract_pages_from_plaintext(path: str) -> list[str]:
    """Split a plain text file into chunks by double newlines (paragraphs).

    Very long single-paragraph files are split into ~2000-char chunks.
    """
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        return [""]
    return _split_by_paragraphs(text)


def extract_pages_from_html(path: str) -> list[str]:
    """Extract visible text from an HTML file and split into chunks.

    Uses BeautifulSoup to strip tags, then splits by structural elements
    (headings, paragraphs).
    """
    from bs4 import BeautifulSoup

    html = Path(path).read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = soup.get_text(separator="\n\n")
    if not text.strip():
        return [""]
    return _split_by_paragraphs(text)


def extract_pages_from_email(path: str) -> list[str]:
    """Extract text from an email file (.eml).

    Handles multipart messages, extracts plain text or HTML parts.
    """
    import email
    from email import policy

    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    msg = email.message_from_string(raw, policy=policy.default)

    parts: list[str] = []

    # Add headers as first chunk
    header_lines = []
    for key in ("From", "To", "Subject", "Date"):
        val = msg.get(key, "")
        if val:
            header_lines.append(f"{key}: {val}")
    if header_lines:
        parts.append("\n".join(header_lines))

    # Extract body
    body = msg.get_body(preferencelist=("plain", "html"))
    if body:
        content = body.get_content()
        if body.get_content_type() == "text/html":
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(content, "html.parser")
            content = soup.get_text(separator="\n\n")
        if content.strip():
            parts.extend(_split_by_paragraphs(content))

    return parts if parts else [""]


# -- Shared helpers -----------------------------------------------------------

MAX_CHUNK_SIZE = 2000  # chars per chunk


def _split_by_paragraphs(text: str) -> list[str]:
    """Split text by double newlines. Merge tiny chunks, split huge ones."""
    raw_parts = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""

    for part in raw_parts:
        part = part.strip()
        if not part:
            continue
        if len(current) + len(part) < MAX_CHUNK_SIZE:
            current = f"{current}\n\n{part}" if current else part
        else:
            if current:
                chunks.append(current)
            # If single paragraph is too long, split it
            if len(part) > MAX_CHUNK_SIZE:
                for sub in _split_long_text(part):
                    chunks.append(sub)
            else:
                current = part
                continue
            current = ""

    if current:
        chunks.append(current)

    return chunks if chunks else [text.strip() or ""]


def _split_long_text(text: str, size: int = MAX_CHUNK_SIZE) -> list[str]:
    """Split a long text into chunks of ~size chars at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""

    for sentence in sentences:
        if len(current) + len(sentence) < size:
            current = f"{current} {sentence}" if current else sentence
        else:
            if current:
                chunks.append(current.strip())
            current = sentence

    if current:
        chunks.append(current.strip())

    return chunks if chunks else [text]
