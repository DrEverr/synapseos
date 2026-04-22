"""Run 20 QA benchmark questions against mnh-det only. Save detailed results.

Usage:
    python tests/run_det_bench.py
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from synapse.config import OntologyRegistry, Settings
from synapse.chat.reasoning import reason_full
from synapse.llm.client import LLMClient
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

QUESTIONS_PATH = Path(__file__).parent.parent / "research" / "table-extraction" / "table_bench_questions.json"
OUTPUT_DIR = Path(__file__).parent.parent / "research" / "table-extraction"


async def run_question(
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    store: InstanceStore,
    text_cache: TextCache | None,
    settings: Settings,
) -> tuple[str, float, int]:
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            reason_full(
                question=question,
                graph=graph,
                llm=llm,
                ontology=ontology,
                store=store,
                text_cache=text_cache,
                max_steps=settings.max_reasoning_steps,
                doom_threshold=settings.doom_loop_threshold,
                reasoning_timeout=settings.reasoning_timeout,
                step_max_tokens=settings.reasoning_step_max_tokens,
            ),
            timeout=settings.reasoning_timeout + 60,
        )
        elapsed = time.time() - t0
        return result.answer, elapsed, result.steps_taken
    except asyncio.TimeoutError:
        return f"(timeout after {settings.reasoning_timeout + 60:.0f}s)", time.time() - t0, 0
    except Exception as e:
        return f"(error: {e})", time.time() - t0, 0


async def main():
    questions = json.loads(QUESTIONS_PATH.read_text())
    print(f"Loaded {len(questions)} questions")

    graph_name = "mnh-det"
    settings = Settings(graph_name=graph_name)
    store = settings.get_instance_store()
    ontology = OntologyRegistry(store=store)
    text_cache = TextCache(settings.get_text_cache_dir())

    model = settings.chat_model or settings.llm_model
    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=model,
        timeout=settings.llm_timeout,
    )
    graph = GraphStore(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        password=settings.falkordb_password,
        graph_name=graph_name,
    )

    results = []
    total_start = time.time()

    for q in questions:
        qid = q["id"]
        print(f"\n[{graph_name}] Q{qid}: {q['question']}", flush=True)
        answer, elapsed, steps = await run_question(
            q["question"], graph, llm, ontology, store, text_cache, settings
        )
        results.append({
            "id": qid,
            "type": q["type"],
            "question": q["question"],
            "expected": q["expected_answer"],
            "answer": answer,
            "elapsed": round(elapsed, 1),
            "steps": steps,
        })
        print(f"  -> {elapsed:.1f}s, {steps} steps", flush=True)
        print(f"  ANSWER: {answer[:300]}", flush=True)

    total_elapsed = time.time() - total_start
    store.close()

    # Save JSON
    out_json = OUTPUT_DIR / "det_bench_results.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\nResults saved to: {out_json}")

    # Save markdown with full answers
    md_lines = [
        "# mnh-det Benchmark — Pełne odpowiedzi\n",
        f"**Total time:** {total_elapsed:.1f}s  \n",
        f"**Questions:** {len(questions)}\n",
        "",
        "| Q# | Typ | Czas | Kroki | Wynik |",
        "|----|-----|------|-------|-------|",
    ]
    for r in results:
        md_lines.append(f"| Q{r['id']} | {r['type']} | {r['elapsed']}s | {r['steps']} | {r['answer'][:80]}... |")

    md_lines += ["", "---", ""]

    for r in results:
        md_lines.append(f"## Q{r['id']}: {r['question']}")
        md_lines.append(f"**Typ:** {r['type']}  ")
        md_lines.append(f"**Czas:** {r['elapsed']}s | **Kroki:** {r['steps']}  ")
        md_lines.append(f"**Oczekiwana:** {r['expected']}\n")
        md_lines.append(f"**Odpowiedź:**")
        md_lines.append(f"> {r['answer']}\n")
        md_lines.append("---\n")

    out_md = OUTPUT_DIR / "det_bench_answers.md"
    out_md.write_text("\n".join(md_lines))
    print(f"Answers saved to: {out_md}")

    # Print summary table
    print(f"\n{'='*70}")
    print(f"{'Q#':<4} {'Typ':<20} {'Czas':>8} {'Kroki':>6}  Odpowiedź (skrót)")
    print("-" * 70)
    for r in results:
        ans_short = r["answer"][:40].replace("\n", " ")
        print(f"Q{r['id']:<3} {r['type']:<20} {r['elapsed']:>7.1f}s {r['steps']:>5}  {ans_short}")
    print(f"\nTotal: {total_elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
