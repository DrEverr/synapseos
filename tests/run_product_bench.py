"""Run product selection questions against mnh-det and save detailed results.

Usage:
    python tests/run_product_bench.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from pathlib import Path

from synapse.config import OntologyRegistry, Settings
from synapse.chat.reasoning import reason_full
from synapse.llm.client import LLMClient
from synapse.storage.graph import GraphStore
from synapse.storage.instance_store import InstanceStore
from synapse.storage.text_cache import TextCache

OUTPUT_DIR = Path(__file__).parent.parent / "research" / "table-extraction"

QUESTIONS = [
    "I need a GDB housing, size 600x600, Galvanized FZ, airflow 2500 m³/h.",
    "I need a GDB housing 600x600 for 3800 m³/h airflow. FZ material.",
    "I need a GDB housing for 15000 m³/h airflow. The maximum height of the housing cannot exceed 1500mm. Standard Galvanized FZ.",
    "I need a GDB housing 1500x1200 for 16000 m³/h airflow. FZ material.",
    "I need a GDC-FLEX carbon housing 600x600 for 3000 m³/h airflow. Indoor ventilation system.",
    "I need a GDMI insulated housing 600x600 for 4000 m³/h airflow. ZM material.",
    "I need an insulated filter housing with C5 corrosion resistance for a coastal industrial facility. Size 600x600, airflow 3400 m³/h.",
    "I need a GDMI insulated housing 600x600 in Stainless Steel (RF) for indoor ventilation. 3400 m³/h.",
    "We require an insulated GDMI housing in Syrafast Stainless Steel (SF / 316) for a marine chemical plant (C5-M). Airflow: 3400 m³/h. Please provide the code.",
    "Pharmaceutical rooftop installation. We want GDC-FLEX in Stainless Steel (RF). Airflow: 1750 m³/h. Please confirm availability.",
    "Ship installation. We require insulation and Syrafast stainless (SF). Please provide GDMI-SF.",
    "We want GDB-600x600 in FZ, housing length 900 mm. Airflow: 3400 m³/h.",
    "We have bag filters 600 mm long. Please provide GDB-550 housing.",
    "Anatomy laboratory exhaust. Formaldehyde vapors present. We want GDC-600x600 in RF. Airflow: 2000 m³/h.",
    "Rooftop kitchen exhaust for a hospital. Marine climate. 3000 m³/h. We want GDC-FLEX RF 600x600.",
    "We need 3400 m³/h. Instead of one 600x600 housing, we want four separate 300x300 housings.",
]


async def run_question(
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    store: InstanceStore,
    text_cache,
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

    for i, question in enumerate(QUESTIONS, 1):
        print(f"\n[Q{i}] {question}", flush=True)
        answer, elapsed, steps = await run_question(
            question, graph, llm, ontology, store, text_cache, settings
        )
        results.append({
            "id": i,
            "elapsed": round(elapsed, 1),
            "steps": steps,
            "question": question,
            "answer": answer,
        })
        print(f"  -> {elapsed:.1f}s, {steps} steps", flush=True)
        print(f"  ANSWER: {answer[:200]}", flush=True)

    total_elapsed = time.time() - total_start
    store.close()

    # Save CSV
    out_csv = OUTPUT_DIR / "product_bench_results.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["id", "elapsed_s", "steps", "question", "answer"])
        for r in results:
            writer.writerow([r["id"], r["elapsed"], r["steps"], r["question"], r["answer"]])
    print(f"\nCSV saved to: {out_csv}")

    # Print summary
    print(f"\n{'='*70}")
    print(f"{'Q#':<4} {'Czas':>8} {'Kroki':>6}  Pytanie (skrót)")
    print("-" * 70)
    for r in results:
        q_short = r["question"][:50]
        print(f"Q{r['id']:<3} {r['elapsed']:>7.1f}s {r['steps']:>5}  {q_short}")
    print(f"\nTotal: {total_elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
