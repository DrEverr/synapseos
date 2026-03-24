"""Document parsers — dispatch to format-specific parsers by file extension."""

from __future__ import annotations

from pathlib import Path

# Supported file extensions (lowercase)
SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".text", ".html", ".htm", ".eml"}


def extract_pages(file_path: str) -> list[str]:
    """Extract text chunks from a document file (any supported format).

    Returns a list of strings ("pages" / chunks) ready for the Document model.
    """
    ext = Path(file_path).suffix.lower()

    if ext == ".pdf":
        from synapse.parsers.pdf import extract_pages as extract_pdf
        return extract_pdf(file_path)

    if ext == ".md":
        from synapse.parsers.text import extract_pages_from_markdown
        return extract_pages_from_markdown(file_path)

    if ext in (".txt", ".text"):
        from synapse.parsers.text import extract_pages_from_plaintext
        return extract_pages_from_plaintext(file_path)

    if ext in (".html", ".htm"):
        from synapse.parsers.text import extract_pages_from_html
        return extract_pages_from_html(file_path)

    if ext == ".eml":
        from synapse.parsers.text import extract_pages_from_email
        return extract_pages_from_email(file_path)

    raise ValueError(f"Unsupported file type: {ext}. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")


def is_supported(file_path: str) -> bool:
    """Check if a file has a supported extension."""
    return Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS
