"""Document and Section models — the core data structures for parsed content."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Section(BaseModel):
    """A hierarchical section of a document."""

    title: str
    start_page: int = 1
    end_page: int = 1
    node_id: str = ""
    text: str = ""
    summary: str = ""
    children: list[Section] = Field(default_factory=list)

    def leaf_sections(self) -> list[Section]:
        """Return all leaf (childless) sections."""
        if not self.children:
            return [self]
        leaves: list[Section] = []
        for child in self.children:
            leaves.extend(child.leaf_sections())
        return leaves

    def all_sections(self) -> list[Section]:
        """Return all sections in pre-order traversal."""
        result: list[Section] = [self]
        for child in self.children:
            result.extend(child.all_sections())
        return result

    @property
    def page_count(self) -> int:
        return max(1, self.end_page - self.start_page + 1)

    def to_tree_string(self, indent: int = 0) -> str:
        prefix = "  " * indent
        summary_part = f" — {self.summary}" if self.summary else ""
        line = f"{prefix}[{self.node_id}] {self.title} (pp. {self.start_page}-{self.end_page}){summary_part}\n"
        for child in self.children:
            line += child.to_tree_string(indent + 1)
        return line


class Document(BaseModel):
    """A parsed source document (PDF, web page, etc.)."""

    id: str = ""
    filename: str = ""
    title: str = ""
    page_count: int = 0
    pages: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    source_url: str = ""
    ingested_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def model_post_init(self, _context: object) -> None:
        if not self.id and self.pages:
            content = "".join(self.pages)
            self.id = hashlib.sha256(content.encode()).hexdigest()

    def leaf_sections(self) -> list[Section]:
        leaves: list[Section] = []
        for sec in self.sections:
            leaves.extend(sec.leaf_sections())
        return leaves

    def all_sections(self) -> list[Section]:
        result: list[Section] = []
        for sec in self.sections:
            result.extend(sec.all_sections())
        return result

    def get_page_text(self, page_num: int) -> str:
        idx = page_num - 1
        if 0 <= idx < len(self.pages):
            return self.pages[idx]
        return ""

    def get_pages_text(self, start: int, end: int) -> str:
        parts = [self.get_page_text(p) for p in range(start, end + 1)]
        return "\n".join(parts)

    def tree_string(self) -> str:
        return "".join(sec.to_tree_string() for sec in self.sections)

    def tree_with_summaries(self) -> str:
        return self.tree_string()
