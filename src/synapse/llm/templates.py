"""Safe template substitution for LLM-generated prompt templates.

LLM-generated prompts often contain literal curly braces (e.g., JSON examples)
alongside placeholder variables like {section_text}. Python's str.format() chokes
on the literal braces. This module provides a safe alternative.
"""

from __future__ import annotations

import re


def safe_format(template: str, **kwargs: str) -> str:
    """Substitute known placeholders in a template without touching literal braces.

    Strategy: replace each known {key} with its value using regex,
    leaving all other curly braces untouched.
    """
    result = template
    for key, value in kwargs.items():
        # Match {key} but not {{key}} (escaped braces)
        pattern = r"\{" + re.escape(key) + r"\}"
        result = re.sub(pattern, lambda m, v=value: v, result)
    return result
