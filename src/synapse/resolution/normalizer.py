"""Entity name normalization — Unicode, trademarks, whitespace, lowercase."""

from __future__ import annotations

import re
import unicodedata


def normalize_entity_name(name: str) -> str:
    """Normalize an entity name to a canonical form for deduplication.

    Steps:
    1. Strip trademark, registered, and copyright symbols
    2. Apply Unicode NFKD normalization
    3. Remove combining characters (accents)
    4. Collapse whitespace
    5. Lowercase
    """
    # Strip trademark/registered/copyright symbols
    text = re.sub(r"[®™©]", "", name)
    # NFKD normalization
    text = unicodedata.normalize("NFKD", text)
    # Remove combining characters
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()
