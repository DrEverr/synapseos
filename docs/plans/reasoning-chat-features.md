# Reasoning & Chat — 4 features (priority = effort × impact)

## Priorytet

| # | Feature | Effort | Impact | Priority | Kolejność |
|---|---------|--------|--------|----------|-----------|
| 1 | Export chat to Markdown/PDF | 1-2d | Medium | **Quick win** | 1st |
| 2 | Streaming responses | 2-3d | High | **High** | 2nd |
| 3 | Multi-agent debate | 2-3d | High | **High** | 3rd |
| 4 | RAG vector search | 3-5d | Very High | **Strategic** | 4th |

---

## Feature 1: Export chat to Markdown/PDF

**Kontekst:** Sesje czatu z reasoning trace, metrykami confidence i actions_log — przydatne do raportów, audytów, dzielenia się wiedzą. Wszystkie dane już w SQLite.

**Implementacja:**
- Nowy moduł `src/synapse/export.py`:
  - `export_session_to_markdown(session_id, store) -> str`
  - `export_session_to_pdf(session_id, store, output_path)` (opcjonalny dep: `reportlab`)
- Format Markdown:
  ```
  # Session: <name>
  **Domain**: X | **Started**: Y

  ## Turn 1
  **Q:** pytanie
  **A:** odpowiedź (pełny Markdown)
  **Confidence:** 0.85 | **Groundedness:** 0.92 | **Steps:** 5 | **Time:** 12.3s

  <details><summary>Reasoning Trace</summary>
  1. GRAPH_QUERY: `MATCH ...` → wyniki
  2. SECTION_TEXT: section_id → tekst
  3. ANSWER: ...
  </details>
  ```
- Nowa komenda CLI: `synapse export <session> --format md|pdf --output file`
- Reuse: `InstanceStore.get_session_episodes()`, `get_session_by_name()`

**Pliki:**
- `src/synapse/export.py` (nowy)
- `src/synapse/cli.py` (dodaj komendę `export`)
- `pyproject.toml` (opcjonalnie: `pdf = ["reportlab>=4.0"]`)

---

## Feature 2: Streaming responses

**Kontekst:** Reasoning loop czeka na pełną odpowiedź LLM przed pokazaniem czegokolwiek. Przy 12+ krokach user czeka minuty bez feedbacku. Streaming pozwoli widzieć Thought/Action w real-time.

**Implementacja:**
- `llm/client.py` — nowa metoda `complete_messages_stream()`:
  - `stream=True` w OpenAI SDK → `AsyncGenerator[str, None]`
  - Yield tokeny po kolei
- `chat/reasoning.py` — zmodyfikować reasoning loop:
  - Dla każdego kroku: stream tokeny Thought → kumuluj → gdy complete → parse Action
  - Nowy callback `on_step: Callable[[str, str], None]` (step_type, content) — wywoływany per krok
  - `reason_full(..., on_step=callback)` — opcjonalny, backward-compatible
- CLI: `on_step` drukuje na stdout w real-time
- GUI: `on_step` emituje signal → update reasoning trace panel na żywo

**Pliki:**
- `src/synapse/llm/client.py` (dodaj `complete_messages_stream`)
- `src/synapse/chat/reasoning.py` (dodaj `on_step` callback do main loop)
- `src/synapse/cli.py` (wire callback do print)

---

## Feature 3: Multi-agent debate

**Kontekst:** Self-assessment (`_assess_answer`) ocenia odpowiedź, ale nie poprawia jej. Debate: drugi LLM kwestionuje odpowiedź, wymusza poprawki.

**Implementacja:**
- Po Phase 3 (self-assess), dodaj Phase 3B:
  1. Challenger LLM (może być inny model/temperatura) krytykuje odpowiedź
  2. Jeśli challenger znajdzie poważne problemy (confidence < threshold) → rewizja
  3. Rewizja: reasoning agent dostaje critique + dodatkowy budżet kroków (max 5)
  4. Ponowna ocena
  5. Max 2 rundy debaty (cap)
- Nowe prompty w `chat/prompts.py`:
  - `CHALLENGER_SYSTEM` / `CHALLENGER_USER` — rola sceptycznego recenzenta
  - `REVISION_USER` — "fix your answer based on this critique: ..."
- Nowa funkcja: `_challenge_answer(answer, question, evidence, llm) -> ChallengeResult`
- Model: `ChallengeResult(agree: bool, critique: str, suggested_improvements: list[str])`
- Config: `SYNAPSE_DEBATE_ENABLED=false` (domyślnie wyłączone), `SYNAPSE_DEBATE_ROUNDS=2`
- CLI: `synapse chat --debate`

**Pliki:**
- `src/synapse/chat/reasoning.py` (dodaj `_challenge_answer`, revision loop)
- `src/synapse/chat/prompts.py` (dodaj challenger/revision prompts)
- `src/synapse/models/reasoning.py` (rozszerz `ReasoningResult` o `debate_rounds`, `challenger_critique`)
- `src/synapse/config.py` (dodaj `debate_enabled`, `debate_rounds`)
- `src/synapse/storage/instance_store.py` (migracja: `debate_rounds` column)

---

## Feature 4: RAG vector search

**Kontekst:** Obecne retrieval opiera się na LLM tree search (structural) + Cypher queries (explicit relationships). Brak semantic similarity — pytanie "co jest trujące?" nie znajdzie sekcji o "toksyczności" bez exact match.

**Implementacja:**
- Embedding: OpenAI `text-embedding-3-small` (tani, szybki, przez istniejący OpenRouter/API)
- Storage: nowa tabela SQLite `section_embeddings(section_id TEXT PK, embedding BLOB)`
  - Blob = numpy float32 array serialized
  - Indeks na section_id
- Embed pipeline:
  - Przy ingest: po zapisaniu text cache, embed każdą sekcję → store w SQLite
  - Batch embedding (max 100 sekcji per API call)
- Retrieval hybrid:
  - Krok 1: Vector similarity → top-K (cosine) sekcji
  - Krok 2: LLM tree search (istniejący) → sekcje strukturalne
  - Krok 3: Merge + deduplicate → final section list
- Nowy moduł: `src/synapse/retrieval/embeddings.py`
- Config: `SYNAPSE_EMBEDDING_MODEL=text-embedding-3-small`, `SYNAPSE_VECTOR_TOP_K=10`

**Pliki:**
- `src/synapse/retrieval/embeddings.py` (nowy: embed, store, search)
- `src/synapse/chat/retrieval.py` (hybrid: vector + tree search)
- `src/synapse/extraction/pipeline.py` (embed po ingest)
- `src/synapse/storage/instance_store.py` (tabela `section_embeddings`)
- `src/synapse/config.py` (embedding settings)
- `pyproject.toml` (dodaj `numpy`)

---

## Implementacja — kolejność

1. **Export** (feature 1) — szybki win, zero ryzyka, natychmiastowa wartość
2. **Streaming** (feature 2) — duży UX improvement, fundament dla GUI real-time
3. **Debate** (feature 3) — podnosi quality odpowiedzi, leverages existing assessment
4. **Vector RAG** (feature 4) — największy impact na retrieval, ale też największy effort

Każdy feature jest niezależny — można implementować w dowolnej kolejności.

---

## Weryfikacja

1. **Export:** `synapse export <session> --format md` → poprawny Markdown z pytaniami, odpowiedziami, trace
2. **Streaming:** `synapse chat -q "pytanie"` → Thought/Action pojawiają się w real-time, nie po zakończeniu
3. **Debate:** `synapse chat --debate -q "pytanie"` → odpowiedź zawiera debate_rounds > 0, critique w trace
4. **Vector:** `synapse chat -q "semantyczne pytanie"` → lepszy recall sekcji niż tree search alone
