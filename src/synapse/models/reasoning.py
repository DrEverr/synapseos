"""Data models for reasoning results and episode logging."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SelfAssessment(BaseModel):
    """LLM self-assessment of an answer's quality."""

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    groundedness: float = Field(default=0.0, ge=0.0, le=1.0)
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
    gaps: list[str] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    """Summary of graph enrichment from an answer."""

    entities_added: int = 0
    relationships_added: int = 0


class ReasoningResult(BaseModel):
    """Full result from a reasoning episode, including answer, metadata, and assessments."""

    answer: str
    question: str
    steps_taken: int = 0
    empty_result_count: int = 0
    timed_out: bool = False
    max_steps_reached: bool = False
    doom_loop_triggered: bool = False
    elapsed_seconds: float = 0.0
    section_ids_used: list[str] = Field(default_factory=list)
    actions_log: list[dict[str, str]] = Field(default_factory=list)
    assessment: SelfAssessment | None = None
    enrichment: EnrichmentResult | None = None
