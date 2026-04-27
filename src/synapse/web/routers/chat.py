"""Chat WebSocket and session management endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import uuid

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from synapse.web.deps import (
    get_graph,
    get_ontology,
    get_settings_dep,
    get_store,
    get_store_lock,
    get_text_cache,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


# -- Session REST endpoints --------------------------------------------------


class SessionCreate(BaseModel):
    name: str = ""


class SessionRename(BaseModel):
    name: str


@router.get("/sessions")
def list_sessions(store=Depends(get_store)):
    return store.list_sessions()


@router.post("/sessions")
def create_session(body: SessionCreate, store=Depends(get_store), settings=Depends(get_settings_dep)):
    session_id = str(uuid.uuid4())
    store.create_session(session_id, domain=settings.graph_name, name=body.name)
    return {"session_id": session_id}


@router.get("/sessions/{session_id}/episodes")
def get_episodes(session_id: str, store=Depends(get_store)):
    episodes = store.get_session_episodes(session_id)
    for ep in episodes:
        for field in ("actions_log", "section_ids", "assessment_gaps"):
            val = ep.get(field)
            if isinstance(val, str):
                try:
                    ep[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
    return episodes


@router.put("/sessions/{session_id}/name")
def rename_session(session_id: str, body: SessionRename, store=Depends(get_store)):
    store.rename_session(session_id, body.name)
    return {"ok": True}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, store=Depends(get_store)):
    store._conn.execute("DELETE FROM reasoning_episodes WHERE session_id = ?", (session_id,))
    store._conn.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
    store._conn.commit()
    return {"ok": True}


@router.get("/sessions/{session_id}/export")
def export_session(session_id: str, store=Depends(get_store)):
    from synapse.export import export_session_to_markdown
    md = export_session_to_markdown(session_id, store)
    return {"markdown": md}


# -- WebSocket chat ----------------------------------------------------------


def _ws_auth(username: str, password: str) -> bool:
    """Check Basic Auth credentials for WebSocket."""
    expected_user = os.environ.get("DEMO_USERNAME", "demo")
    expected_pass = os.environ.get("DEMO_PASSWORD", "wacker2026")
    return (
        secrets.compare_digest(username.encode(), expected_user.encode())
        and secrets.compare_digest(password.encode(), expected_pass.encode())
    )


@router.websocket("/chat/{session_id}")
async def chat_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
):
    # Auth via query param: ?token=user:pass (base64 or plain)
    if token:
        parts = token.split(":", 1)
        if len(parts) == 2 and _ws_auth(parts[0], parts[1]):
            pass
        else:
            await websocket.close(code=4001, reason="Unauthorized")
            return
    else:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()

    # Get dependencies
    settings = get_settings_dep()
    graph = get_graph()
    store = get_store()
    store_lock = get_store_lock()
    text_cache = get_text_cache()
    ontology = get_ontology()

    # Ensure session exists
    async with store_lock:
        session = store.get_session_by_name(session_id)
        if not session:
            store.create_session(session_id, domain=settings.graph_name)

    # Load chat history
    async with store_lock:
        episodes = store.get_session_episodes(session_id)
    chat_history = []
    for ep in episodes:
        actions_log = ep.get("actions_log", "[]")
        if isinstance(actions_log, str):
            try:
                actions_log = json.loads(actions_log)
            except (json.JSONDecodeError, TypeError):
                actions_log = []
        section_ids = ep.get("section_ids", "[]")
        if isinstance(section_ids, str):
            try:
                section_ids = json.loads(section_ids)
            except (json.JSONDecodeError, TypeError):
                section_ids = []
        chat_history.append({
            "question": ep["question"],
            "answer": ep["answer"],
            "actions_log": actions_log,
            "section_ids": section_ids,
        })

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") != "question":
                continue

            question = data.get("text", "").strip()
            if not question:
                continue

            await _handle_question(
                websocket=websocket,
                question=question,
                session_id=session_id,
                settings=settings,
                graph=graph,
                store=store,
                store_lock=store_lock,
                text_cache=text_cache,
                ontology=ontology,
                chat_history=chat_history,
            )
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session %s", session_id)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        try:
            await websocket.send_json({"type": "error", "detail": str(e)})
        except Exception:
            pass


async def _handle_question(
    websocket: WebSocket,
    question: str,
    session_id: str,
    settings,
    graph,
    store,
    store_lock,
    text_cache,
    ontology,
    chat_history: list[dict],
) -> None:
    """Run reasoning and stream steps over WebSocket."""
    from synapse.chat.reasoning import reason_full
    from synapse.llm.client import LLMClient

    queue: asyncio.Queue = asyncio.Queue()

    def on_step(step_num: int, phase: str, content: str) -> None:
        # Show first paragraph only (up to first blank line)
        text = content or ""
        first_para = text.split("\n")[0].strip()
        queue.put_nowait({
            "type": "step",
            "step": step_num,
            "phase": phase,
            "content": first_para,
        })

    chat_model = settings.chat_model or settings.llm_model
    llm = LLMClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=chat_model,
        timeout=settings.llm_timeout,
    )

    async def run_reasoning():
        return await reason_full(
            question=question,
            graph=graph,
            llm=llm,
            ontology=ontology,
            max_steps=settings.max_reasoning_steps,
            doom_threshold=settings.doom_loop_threshold,
            verbose=False,
            text_cache=text_cache,
            reasoning_timeout=settings.reasoning_timeout,
            step_max_tokens=settings.reasoning_step_max_tokens,
            store=store,
            chat_history=chat_history,
            session_id=session_id,
            context_max_tokens=settings.chat_context_max_tokens,
            on_step=on_step,
            stream=False,
        )

    async def send_steps():
        while True:
            msg = await queue.get()
            if msg is None:
                break
            try:
                await websocket.send_json(msg)
            except Exception:
                break

    # Run reasoning and step-sending concurrently
    reasoning_task = asyncio.create_task(run_reasoning())
    send_task = asyncio.create_task(send_steps())

    try:
        result = await reasoning_task
    except Exception as exc:
        queue.put_nowait(None)
        await send_task
        logger.error("Reasoning failed: %s", exc, exc_info=True)
        await websocket.send_json({"type": "error", "detail": str(exc)})
        return
    finally:
        queue.put_nowait(None)
        # send_task may already be done from the except branch
        if not send_task.done():
            await send_task

    # Low-confidence threshold — replace garbage answers
    LOW_CONFIDENCE_THRESHOLD = 0.10
    conf = result.assessment.confidence if result.assessment else 1.0
    if conf < LOW_CONFIDENCE_THRESHOLD:
        result.answer = (
            "I don't have enough information in the knowledge base to give a reliable answer to this question. "
            "Please try rephrasing, or check if the relevant documents have been ingested."
        )

    # Send answer
    answer_msg = {
        "type": "answer",
        "text": result.answer,
        "steps": result.steps_taken,
        "elapsed": round(result.elapsed_seconds, 1),
    }
    if result.assessment:
        answer_msg["confidence"] = round(result.assessment.confidence, 2)
        answer_msg["groundedness"] = round(result.assessment.groundedness, 2)
        answer_msg["completeness"] = round(result.assessment.completeness, 2)
        answer_msg["assessment"] = result.assessment.reasoning
        answer_msg["gaps"] = result.assessment.gaps
    if result.debate_rounds:
        answer_msg["debate_rounds"] = result.debate_rounds
    await websocket.send_json(answer_msg)
    await websocket.send_json({"type": "done"})

    # Update chat history for multi-turn
    chat_history.append({
        "question": question,
        "answer": result.answer,
        "actions_log": result.actions_log,
        "section_ids": result.section_ids_used,
    })

    # Auto-name session after first turn
    if len(chat_history) == 1:
        asyncio.create_task(_auto_name_session(question, session_id, settings, store, store_lock))


async def _auto_name_session(
    question: str, session_id: str, settings, store, store_lock
) -> None:
    """Generate a short session name from the first question using LLM."""
    try:
        from synapse.llm.client import LLMClient
        llm = LLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.compaction_model or settings.llm_model,
            timeout=settings.llm_timeout,
        )
        name = await llm.complete(
            system="Generate a short session name (2-5 words, lowercase, no quotes) "
                   "that captures the topic of this question. Reply with ONLY the name.",
            user=question,
            temperature=0.0,
            max_tokens=20,
        )
        name = str(name).strip().strip("\"'").lower()
        if name:
            async with store_lock:
                store.rename_session(session_id, name)
            logger.info("Auto-named session %s -> %s", session_id[:8], name)
    except Exception as e:
        logger.debug("Auto-name failed for session %s: %s", session_id[:8], e)
