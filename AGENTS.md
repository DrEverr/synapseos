# AGENTS.md — SynapseOS

## Project Overview

SynapseOS is a domain-agnostic knowledge operating system written in Python 3.12.
It bootstraps ontologies from documents, extracts entities/relationships into a
knowledge graph (FalkorDB), and answers questions via a ReAct reasoning loop with
structured graph tools.

**Version:** 0.5.0 (current) → 0.6.0 (planned, see PLAN.md)

**Stack:** Python 3.12 / Click CLI / Pydantic v2 / AsyncOpenAI / FalkorDB / PyMuPDF / pytest

**Package layout:** `src/synapse/` (src-layout with setuptools)

---

## Build & Run Commands

```bash
# Install (editable, with dev dependencies)
pip install -e ".[dev]"

# Start FalkorDB (required for ingest/chat, NOT for tests)
docker compose up -d

# CLI entry point
synapse --help
```

## Lint & Type-Check Commands

```bash
ruff check src/                # Lint (E, F, I, W rules; line-length=100)
ruff check src/ --fix          # Lint with auto-fix
black src/                     # Format (default 88-char line)
black --check src/             # Check formatting without changes
mypy src/                      # Type-check (strict mode, python 3.12)
```

## Test Commands

Tests do NOT require FalkorDB or an API key — they exercise pure logic and SQLite.

```bash
pytest tests/ -v                                          # All tests
pytest tests/test_json_repair.py -v                       # Single file
pytest tests/test_json_repair.py::test_valid_json -v      # Single function
pytest tests/test_reasoning.py::TestSanitizeCypher -v     # Single class
pytest tests/test_reasoning.py::TestSanitizeCypher::test_plain_cypher -v  # Single method
pytest tests/ -v -k "normaliz"                            # Keyword filter
```

---

## Code Style Guidelines

### Imports

Every file must begin with `from __future__ import annotations` as the very first
import. After that, imports are grouped in order, separated by blank lines:

1. `from __future__ import annotations`
2. Standard library (`asyncio`, `json`, `logging`, `re`, `pathlib`, etc.)
3. Third-party (`click`, `pydantic`, `openai`, `tenacity`, `falkordb`, etc.)
4. Local (`from synapse.models.entity import Entity`, etc.)

Within each group, imports are alphabetized. Enforced by Ruff rule `I` (isort).
Use absolute imports only — no relative imports (`from .foo import bar`).
Deferred imports inside functions are acceptable to avoid circular imports or reduce
startup time (used in `cli.py`).

### Formatting

- **Line length:** 100 characters (Ruff). Black uses its default 88.
- **Quotes:** Double quotes everywhere (Black default).
- **Trailing commas:** Required in multi-line structures (Black-enforced).
- **Blank lines:** Two between top-level definitions, one between class methods.
- **Indentation:** 4 spaces (standard Python).

### Naming Conventions

| Element               | Convention        | Example                          |
|-----------------------|-------------------|----------------------------------|
| Modules/files         | `snake_case`      | `instance_store.py`              |
| Classes               | `PascalCase`      | `GraphStore`, `LLMClient`        |
| Functions/methods     | `snake_case`      | `extract_entities`               |
| Private funcs/attrs   | `_snake_case`     | `_sanitize_cypher`, `self._conn` |
| Constants             | `UPPER_SNAKE_CASE` | `PROJECT_ROOT`, `CONFIG_DIR`    |
| Private constants     | `_UPPER_SNAKE_CASE`| `_SCHEMA`, `_FALLBACK_SYSTEM`   |
| Variables             | `snake_case`      | `all_entities`, `sample_text`    |

### Type Annotations

- **mypy strict mode** is enabled — all functions must have full type annotations.
- Use modern Python 3.10+ syntax: `str | None`, `dict[str, str]`, `list[Entity]`.
  Never use `Optional`, `Union`, `Dict`, `List`, `Tuple`, `Set` from `typing`.
- All functions must have explicit return type annotations, including `-> None`.
- Use `Any` sparingly, only when genuinely unavoidable.
- Data models use Pydantic `BaseModel` (not dataclasses).
- Configuration uses `pydantic_settings.BaseSettings`.

### Docstrings & Comments

- Every module has a top-level docstring (`"""PDF text extraction using PyMuPDF."""`).
- Classes get a short one-line docstring.
- Public functions get imperative-mood docstrings (`"""Extract entities from..."""`).
- No `:param:` or `Args:` sections — use plain prose.
- Inline comments explain "why", not "what".
- Section separators use Unicode box-drawing:
  `# ── Section Name ──────────────────────────────────────`
- Use `%`-style formatting for `logger` calls: `logger.info("Found %d items", count)`.
- Use f-strings for all other string construction.

### Error Handling

- Wrap external calls (LLM, DB, file I/O) in `try/except Exception` with logging.
  Return safe defaults (empty list, empty dict, `None`) rather than propagating.
  ```python
  try:
      result = await llm.complete_json_lenient(...)
  except Exception as e:
      logger.error("Entity extraction failed: %s", e)
      return []
  ```
- Catch specific exceptions when meaningful (`json.JSONDecodeError`,
  `asyncio.TimeoutError`).
- Raise `ValueError` for input validation failures. No custom exception classes.
- Use `@retry` from tenacity for LLM calls (3 attempts, exponential backoff).
- Cypher queries from LLM output must be sanitized against write keywords
  (`CREATE`, `DELETE`, `SET`, `MERGE`, etc.) before execution.

### Async Patterns

- Pipeline code (`bootstrap`, `extraction`, `chat`) is fully async.
- CLI commands bridge sync to async with `asyncio.run()`.
- Use `asyncio.Semaphore` + `asyncio.gather()` for bounded parallel processing.
- No global event loop — clean separation at the CLI boundary.

### Architecture Patterns

- **Module-level logger:** `logger = logging.getLogger(__name__)` in every module.
- **Singleton settings:** `get_settings()` returns a cached `Settings` instance.
- **Prompt fallback chain:** Check `InstanceStore` for generated prompts, fall back
  to hardcoded constants in `prompts.py` modules.
- **Pipeline functions:** Pipelines are sequences of `async def` functions, each
  taking data and returning transformed data (not classes).
- **One class or function set per file.** Flat module structure, no deep nesting.
- **Defensive LLM parsing:** After every LLM call, validate results with
  `isinstance` checks and multiple fallback key lookups before use.

### Reasoning & Graph Tools

The chat reasoning loop uses **structured graph tools** (`src/synapse/tools/`) instead
of raw Cypher. Tools are domain-agnostic and auto-discover the graph schema:

| Tool | Purpose |
|------|---------|
| `FIND(name)` | Smart entity search with normalization, partial matching, keyword fallback |
| `DETAILS(name)` | All properties + incoming/outgoing relationships in one call |
| `RELATED(name, REL_TYPE)` | Explore connections, optionally filtered by type |
| `COMPARE(name1, name2)` | Side-by-side property/relationship comparison |
| `LIST(TYPE)` | List all entities of a given type |
| `SCHEMA()` | Entity/relationship type counts — graph introspection |

The runtime (`chat/reasoning.py`) dispatches these tools via `execute_tool()` from
`tools/graph_tools.py`. Legacy `GRAPH_QUERY(cypher)` is still supported for backward
compatibility with older bootstrapped prompts but should not be used in new prompts.

**Bootstrap prompt generation** (`bootstrap/prompts.py`) instructs the LLM to generate
reasoning prompts using the structured tools — not raw Cypher.

### Exports

- No `__all__` declarations. Consumers import directly from specific modules
  (e.g., `from synapse.models.entity import Entity`).
- `__init__.py` files are minimal (docstring only, or just `__version__`).

### Testing Conventions

- **Framework:** pytest with pytest-asyncio (`asyncio_mode = "auto"`).
- **File naming:** `tests/test_<module>.py`.
- **Function tests:** `def test_<behavior>()` for simple pure-function tests.
- **Class grouping:** `class Test<Unit>:` with `def test_<case>(self):` methods.
- **Fixtures:** Use `@pytest.fixture` with `tmp_path` for temp resources; define
  fixtures locally in the test file (no root `conftest.py`).
- **No mocking:** Current tests exercise real code with real (temp) resources.
  No external service dependencies in tests.
- **Assertions:** Plain `assert` statements; `pytest.raises` for expected errors.

---

### Known Limitations (targeted for v0.6.0, see PLAN.md)

- **Table extraction:** PDF tables are parsed as flat text by PyMuPDF `get_text()`. LLM extracts
  a few example rows but misses most of the data in large tables (30+ rows).
- **Catalog layout:** Repeating product-page layouts (product catalogs) are not detected —
  the structure parser treats them as narrative documents, leading to incorrect section nesting.
- **Visual elements:** Icons, badges, and layout-encoded information (e.g., material availability
  circles on product pages) are not captured.
- **Entity resolution:** Global fuzzy threshold (0.90) misses duplicates with different verbosity
  (e.g., `"aluzink (az) - c4"` vs `"aluzink - c4"`).
- **Legacy prompts:** Domains bootstrapped before the prompt update use `GRAPH_QUERY(cypher)`
  instead of structured tools. Re-bootstrap to get new-style prompts.

---

## Environment Setup

Copy `.env.example` to `.env` and set at minimum `SYNAPSE_LLM_API_KEY`.
All settings use the `SYNAPSE_` prefix (see `src/synapse/config.py` for full list).
FalkorDB is required only for `synapse ingest` and `synapse chat`, not for tests.
