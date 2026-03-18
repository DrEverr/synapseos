"""Robust JSON extraction and repair from messy LLM output."""

from __future__ import annotations

import json
import re


def repair_and_parse_json(text: str) -> dict | list:
    """Extract and parse JSON from LLM output, repairing common issues.

    Handles: markdown fences, Python literals, trailing commas,
    comments, truncated output, nested extraction.
    """
    if not text or not text.strip():
        raise ValueError("Empty input")

    # Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    # Try direct parse
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Fix Python literals
    fixed = cleaned.replace("True", "true").replace("False", "false").replace("None", "null")
    # Remove trailing commas before closing brackets
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    # Remove line comments
    fixed = re.sub(r"//[^\n]*", "", fixed)
    # Remove block comments
    fixed = re.sub(r"/\*.*?\*/", "", fixed, flags=re.DOTALL)

    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object or array in surrounding text
    best: dict | list | None = None
    best_len = 0

    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start_idx = fixed.find(start_char)
        while start_idx != -1:
            # Find matching end bracket by counting
            depth = 0
            for i in range(start_idx, len(fixed)):
                if fixed[i] == start_char:
                    depth += 1
                elif fixed[i] == end_char:
                    depth -= 1
                if depth == 0:
                    candidate = fixed[start_idx : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if len(candidate) > best_len:
                            best = parsed
                            best_len = len(candidate)
                    except json.JSONDecodeError:
                        pass
                    break
            start_idx = fixed.find(start_char, start_idx + 1)

    if best is not None:
        return best

    # Truncation repair: try closing unclosed brackets
    bracket_stack: list[str] = []
    in_string = False
    escape = False
    for ch in fixed:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            bracket_stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if bracket_stack:
                bracket_stack.pop()

    if bracket_stack:
        repaired = fixed + "".join(reversed(bracket_stack))
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from: {text[:200]}")
