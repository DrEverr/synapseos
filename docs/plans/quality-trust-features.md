# Quality & Trust — 4 features (priority = effort × impact)

## Priorytet

| # | Feature | Effort | Impact | Priority | Kolejność |
|---|---------|--------|--------|----------|-----------|
| 1 | Graph health dashboard | 1-2d | Medium | **Quick win** | 1st |
| 2 | Conflict detection | 1-2d | High | **High** | 2nd |
| 3 | Confidence decay | 1-2d | High | **High** | 3rd |
| 4 | Source provenance | 2-3d | Very High | **Strategic** | 4th |

---

## Feature 1: Graph health dashboard

**Kontekst:** Brak widoczności na jakość grafu — ile jest orphan nodes, nisko-confidence entities, jakie typy ontologii są niewykorzystane. Istniejące metody analytics (`get_entity_counts`, `find_duplicates`) pokrywają ~30%.

**Implementacja:**
- `storage/graph.py` — nowa metoda `get_graph_health() -> dict`:
  - `orphan_nodes` — entities bez żadnych relacji
  - `low_confidence_entities/relationships` — confidence < 0.6
  - `unverified_count` — ile czeka na review
  - `avg_confidence` — średnia confidence entity/rels
  - `relationship_density` — edges/nodes
  - `unused_ontology_types` — typy z ontologii bez żadnego entity w grafie
  - `document_coverage` — % sekcji z wyekstrahowanymi entities
- CLI: `synapse inspect --health` — wyświetla metryki
- Cypher queries:
  ```cypher
  -- Orphans
  MATCH (n) WHERE NOT n:Document AND NOT n:Section AND NOT (n)-[]-() RETURN count(n)
  -- Low confidence
  MATCH (n) WHERE n.confidence < 0.6 RETURN count(n)
  -- Unused types (porównaj z ontologią w InstanceStore)
  ```

**Pliki:**
- `src/synapse/storage/graph.py` (dodaj `get_graph_health()`, `get_orphan_nodes()`)
- `src/synapse/cli.py` (dodaj `--health` do `inspect`)
- `src/synapse/storage/instance_store.py` (reuse `get_entity_types()` do porównania)

---

## Feature 2: Conflict detection

**Kontekst:** Graf może zawierać sprzeczne relacje (A CAUSES B i A PROTECTS_AGAINST B). Brak mechanizmu wykrywania — użytkownik nie wie o niespójnościach.

**Implementacja:**
- Definicja: dwa typy konfliktów:
  1. **Opposite relationships** — ten sam subject-object z semantycznie sprzecznymi predykatami
  2. **Value conflicts** — ten sam entity z różnymi wartościami tego samego property
- Conflict rules: `config/conflict_rules.json` — lista par predykatów uznanych za sprzeczne:
  ```json
  {
    "contradictory_pairs": [
      ["CAUSES", "PROTECTS_AGAINST"],
      ["COMPATIBLE_WITH", "INCOMPATIBLE_WITH"],
      ["SUITABLE_FOR", "INEFFECTIVE_AGAINST"],
      ["VULNERABLE_TO", "PROTECTS_AGAINST"]
    ]
  }
  ```
- `storage/graph.py` — nowa metoda `find_conflicts(rules) -> list[dict]`:
  - Dla każdej pary: Cypher query szukający obu predykatów na tym samym (subject, object)
  - Return: `[{subject, rel1, rel2, object, confidence1, confidence2}]`
- CLI: `synapse inspect --conflicts`
- Opcjonalnie: auto-flag as `verified=false` jeśli conflict detected

**Pliki:**
- `src/synapse/storage/graph.py` (dodaj `find_conflicts()`)
- `src/synapse/cli.py` (dodaj `--conflicts` do `inspect`)
- `config/conflict_rules.json` (nowy)

---

## Feature 3: Confidence decay

**Kontekst:** Entity wyekstrahowany 6 miesięcy temu ma tę samą confidence co entity z wczoraj. Stare dane powinny tracić confidence jeśli nie są repotwierdzane.

**Implementacja:**
- Dodaj do Entity/Relationship models: `created_at: str`, `last_confirmed_at: str`
- `storage/graph.py` — MERGE queries:
  - ON CREATE: `n.created_at = timestamp(), n.last_confirmed_at = timestamp()`
  - ON MATCH: `n.last_confirmed_at = timestamp()` (reconfirmation)
- Decay formula: `effective_confidence = base_confidence * (decay_rate ^ days_since_confirmed)`
  - Default: `decay_rate = 0.99` (1% per dzień, ~50% po 70 dniach)
  - Config: `SYNAPSE_CONFIDENCE_DECAY_RATE=0.99`
- Podejście: **lazy decay** — obliczaj effective confidence w query time:
  ```cypher
  RETURN n.confidence * (0.99 ^ (duration.inDays(date(n.last_confirmed_at), date()))) AS effective_confidence
  ```
- Batch reconfirmation: re-ingest dokumentu → entities z tego dokumentu dostają fresh `last_confirmed_at`
- CLI: `synapse inspect --decayed` — entities z effective_confidence < threshold

**Pliki:**
- `src/synapse/models/entity.py` (dodaj timestamps)
- `src/synapse/models/relationship.py` (dodaj timestamps)
- `src/synapse/storage/graph.py` (MERGE z timestamps, decay query)
- `src/synapse/config.py` (dodaj `confidence_decay_rate`)
- `src/synapse/cli.py` (dodaj `--decayed` do `inspect`)

---

## Feature 4: Source provenance

**Kontekst:** Kliknij entity → pokaż dokładne zdanie z dokumentu. Obecnie mamy section-level linkage (`EXTRACTED_FROM` edge, `source_section` field) ale nie sentence-level.

**Implementacja:**
- Rozszerz LLM extraction prompt aby zwracał `text_span` (oryginalne zdanie/fragment):
  - `entity_extraction_user` prompt → dodaj: `"source_text": "exact sentence from the document"`
  - LLM zwraca text_span per entity → przechowujemy
- Dodaj do Entity model: `source_text: str = ""` (oryginalny fragment z dokumentu)
- Graph storage: `n.source_text = $source_text` w MERGE ON CREATE
- TextCache helper: `get_context(section_id, query) -> str` — zwraca tekst sekcji z podświetleniem
- CLI: `synapse inspect --provenance <entity_name>` → pokaż source document + text
- GUI: double-click entity w Graph Inspector → dialog z source text

**Pliki:**
- `src/synapse/models/entity.py` (dodaj `source_text`)
- `src/synapse/extraction/entities.py` (zmodyfikuj prompt, parsuj source_text)
- `src/synapse/storage/graph.py` (store source_text)
- `src/synapse/storage/text_cache.py` (dodaj `get_context()`)
- `src/synapse/cli.py` (dodaj `--provenance`)

---

## Implementacja — kolejność

1. **Graph health** — szybki win, zero ryzyka, immediate visibility
2. **Conflict detection** — proste Cypher queries, duża wartość dla specjalistów
3. **Confidence decay** — wymaga timestamp migration, ale podnosi trust w stare dane
4. **Source provenance** — największy effort (LLM prompt changes), ale najwyższy impact na trust

---

## Weryfikacja

1. **Health:** `synapse inspect --health` → lista metryk, orphan count, coverage %
2. **Conflicts:** `synapse inspect --conflicts` → lista sprzecznych relacji (jeśli istnieją)
3. **Decay:** entity z `last_confirmed_at` sprzed 30 dni ma niższy effective_confidence
4. **Provenance:** `synapse inspect --provenance "silres bs 1052"` → dokładne zdanie z dokumentu
