"""Run 20 QA benchmark questions against two graph instances and score results.

Usage:
    python tests/run_qa_bench.py
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


async def run_question(
    question: str,
    graph: GraphStore,
    llm: LLMClient,
    ontology: OntologyRegistry,
    store: InstanceStore,
    text_cache: TextCache | None,
    settings: Settings,
) -> tuple[str, float, int]:
    """Run one question through the reasoning agent. Returns (answer, elapsed, steps)."""
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
        answer = result.answer
        steps = result.steps_taken
        return answer, elapsed, steps
    except asyncio.TimeoutError:
        return f"(timeout after {settings.reasoning_timeout + 60:.0f}s)", time.time() - t0, 0
    except Exception as e:
        return f"(error: {e})", time.time() - t0, 0


async def bench_instance(graph_name: str, questions: list[dict]) -> list[dict]:
    """Run all questions against one instance."""
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
    for q in questions:
        qid = q["id"]
        print(f"  [{graph_name}] Q{qid}: {q['question'][:60]}...", flush=True)
        answer, elapsed, steps = await run_question(
            q["question"], graph, llm, ontology, store, text_cache, settings
        )
        results.append({
            "id": qid,
            "question": q["question"],
            "expected": q["expected_answer"],
            "answer": answer,
            "type": q["type"],
            "elapsed": round(elapsed, 1),
            "steps": steps,
        })
        print(f"    -> {elapsed:.1f}s, {steps} steps", flush=True)

    store.close()
    return results


def score_answer(answer: str, expected: str) -> tuple[int, str]:
    """Score an answer against expected. Returns (score 0-2, reason)."""
    answer_lower = answer.lower()
    expected_lower = expected.lower()

    # Extract key values from expected answer
    import re
    numbers = re.findall(r"[\d]+(?:[.,]\d+)?", expected)
    key_terms = [t.strip() for t in re.split(r"[,;()\[\]]", expected) if len(t.strip()) > 2]

    # Check exact key values
    numbers_found = sum(1 for n in numbers if n in answer)
    terms_found = sum(1 for t in key_terms if t.lower() in answer_lower)

    total_keys = len(numbers) + len(key_terms)
    if total_keys == 0:
        return 1, "no keys to check"

    ratio = (numbers_found + terms_found) / total_keys

    if ratio >= 0.7:
        return 2, f"good ({numbers_found}/{len(numbers)} nums, {terms_found}/{len(key_terms)} terms)"
    elif ratio >= 0.3:
        return 1, f"partial ({numbers_found}/{len(numbers)} nums, {terms_found}/{len(key_terms)} terms)"
    else:
        return 0, f"miss ({numbers_found}/{len(numbers)} nums, {terms_found}/{len(key_terms)} terms)"


async def main():
    questions = json.loads(QUESTIONS_PATH.read_text())
    print(f"Loaded {len(questions)} questions")
    print()

    all_results = {}

    for graph_name in ["mnh-det", "mnh-hyb"]:
        print(f"\n{'='*60}")
        print(f"Running benchmark on: {graph_name}")
        print(f"{'='*60}")
        results = await bench_instance(graph_name, questions)
        all_results[graph_name] = results

    # Score and compare
    print(f"\n{'='*70}")
    print("RESULTS COMPARISON")
    print(f"{'='*70}")
    print(f"{'Q#':<4} {'Type':<18} {'DET score':>10} {'HYB score':>10} {'DET time':>9} {'HYB time':>9}")
    print("-" * 70)

    det_total = hyb_total = 0
    det_time = hyb_time = 0.0
    type_scores: dict[str, dict[str, list[int]]] = {}

    for det_r, hyb_r in zip(all_results["mnh-det"], all_results["mnh-hyb"]):
        det_score, det_reason = score_answer(det_r["answer"], det_r["expected"])
        hyb_score, hyb_reason = score_answer(hyb_r["answer"], hyb_r["expected"])

        qtype = det_r["type"]
        if qtype not in type_scores:
            type_scores[qtype] = {"det": [], "hyb": []}
        type_scores[qtype]["det"].append(det_score)
        type_scores[qtype]["hyb"].append(hyb_score)

        det_total += det_score
        hyb_total += hyb_score
        det_time += det_r["elapsed"]
        hyb_time += hyb_r["elapsed"]

        det_marker = ["X", "~", "V"][det_score]
        hyb_marker = ["X", "~", "V"][hyb_score]

        print(f"Q{det_r['id']:<3} {qtype:<18} {det_marker:>5} ({det_score}/2) {hyb_marker:>5} ({hyb_score}/2) {det_r['elapsed']:>8.1f}s {hyb_r['elapsed']:>8.1f}s")

    max_score = len(questions) * 2
    print("-" * 70)
    print(f"{'TOTAL':<23} {det_total:>5}/{max_score}   {hyb_total:>5}/{max_score}   {det_time:>8.1f}s {hyb_time:>8.1f}s")
    print(f"{'PERCENTAGE':<23} {det_total/max_score*100:>5.0f}%      {hyb_total/max_score*100:>5.0f}%")

    # Scores by question type
    print(f"\n{'='*50}")
    print("SCORES BY QUESTION TYPE")
    print(f"{'='*50}")
    print(f"{'Type':<20} {'DET':>10} {'HYB':>10}")
    print("-" * 42)
    for qtype, scores in sorted(type_scores.items()):
        det_avg = sum(scores["det"]) / len(scores["det"])
        hyb_avg = sum(scores["hyb"]) / len(scores["hyb"])
        det_pct = sum(scores["det"]) / (len(scores["det"]) * 2) * 100
        hyb_pct = sum(scores["hyb"]) / (len(scores["hyb"]) * 2) * 100
        print(f"{qtype:<20} {det_pct:>8.0f}%  {hyb_pct:>8.0f}%")

    # Save detailed results
    output_path = QUESTIONS_PATH.parent / "qa_bench_results.json"
    output = {
        "summary": {
            "det_score": det_total,
            "hyb_score": hyb_total,
            "max_score": max_score,
            "det_time": round(det_time, 1),
            "hyb_time": round(hyb_time, 1),
        },
        "mnh-det": all_results["mnh-det"],
        "mnh-hyb": all_results["mnh-hyb"],
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"\nDetailed results saved to: {output_path}")

    # Save detailed answers comparison
    md_path = QUESTIONS_PATH.parent / "qa_bench_answers.md"
    md_lines = ["# QA Benchmark — Detailed Answers\n"]
    for det_r, hyb_r in zip(all_results["mnh-det"], all_results["mnh-hyb"]):
        det_score, _ = score_answer(det_r["answer"], det_r["expected"])
        hyb_score, _ = score_answer(hyb_r["answer"], hyb_r["expected"])
        md_lines.append(f"## Q{det_r['id']}: {det_r['question']}")
        md_lines.append(f"**Type:** {det_r['type']}  ")
        md_lines.append(f"**Expected:** {det_r['expected']}\n")
        md_lines.append(f"**DET ({det_score}/2, {det_r['elapsed']:.1f}s, {det_r['steps']} steps):**")
        md_lines.append(f"> {det_r['answer']}\n")
        md_lines.append(f"**HYB ({hyb_score}/2, {hyb_r['elapsed']:.1f}s, {hyb_r['steps']} steps):**")
        md_lines.append(f"> {hyb_r['answer']}\n")
        md_lines.append("---\n")
    md_path.write_text("\n".join(md_lines))
    print(f"Detailed answers saved to: {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
