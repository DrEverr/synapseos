"""Entity model — a node in the knowledge graph."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class Entity(BaseModel):
    """A named entity extracted from a document section."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    canonical_name: str = ""
    entity_type: str = ""
    confidence: float = 0.5
    properties: dict = Field(default_factory=dict)
    source_doc: str = ""
    source_section: str = ""
    verified: bool = True

    def model_post_init(self, _context: object) -> None:
        if not self.canonical_name and self.text:
            self.canonical_name = self.text.strip().lower()
