"""File-based text cache for section content, keeping large text out of the graph DB."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class TextCache:
    """Maps section IDs to full text files on disk.

    Default location: ~/.synapse/text_cache/ (or instance_dir/text_cache/).
    """

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        if cache_dir is None:
            cache_dir = Path.home() / ".synapse" / "text_cache"
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._dir / "_index.json"
        self._index: dict[str, str] = self._load_index()

    def _load_index(self) -> dict[str, str]:
        if self._index_path.exists():
            try:
                return json.loads(self._index_path.read_text())
            except Exception:
                return {}
        return {}

    def _save_index(self) -> None:
        self._index_path.write_text(json.dumps(self._index, ensure_ascii=False, indent=2))

    def _safe_filename(self, section_id: str) -> str:
        return hashlib.sha256(section_id.encode()).hexdigest() + ".txt"

    def store(self, section_id: str, text: str) -> None:
        """Store text for a section ID."""
        filename = self._safe_filename(section_id)
        (self._dir / filename).write_text(text, encoding="utf-8")
        self._index[section_id] = filename
        self._save_index()

    def store_batch(self, items: dict[str, str]) -> None:
        """Store multiple section texts at once."""
        for section_id, text in items.items():
            filename = self._safe_filename(section_id)
            (self._dir / filename).write_text(text, encoding="utf-8")
            self._index[section_id] = filename
        self._save_index()
        logger.info("Cached %d section texts", len(items))

    def get(self, section_id: str) -> str | None:
        """Retrieve cached text for a section ID, or None if not found."""
        filename = self._index.get(section_id)
        if filename:
            path = self._dir / filename
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None
