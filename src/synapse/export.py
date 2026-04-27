"""Export chat sessions to Markdown or PDF.

Usage::

    from synapse.export import export_session_to_markdown
    md = export_session_to_markdown("session-id-or-name", store)

    from synapse.export import export_session_to_pdf
    export_session_to_pdf("session-id-or-name", store, "output.pdf")
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from synapse.storage.instance_store import InstanceStore


def export_session_to_markdown(session_ref: str, store: InstanceStore) -> str:
    """Export a chat session to a Markdown string.

    *session_ref* can be a session name, full session_id, or prefix.
    """
    session = _resolve_session(session_ref, store)
    episodes = store.get_session_episodes(session["session_id"])

    lines: list[str] = []

    # Header
    name = session.get("name") or session["session_id"][:8]
    domain = session.get("domain", "")
    started = (session.get("started_at") or "")[:19]
    lines.append(f"# Session: {name}")
    lines.append("")
    meta_parts = []
    if domain:
        meta_parts.append(f"**Domain**: {domain}")
    if started:
        meta_parts.append(f"**Started**: {started}")
    meta_parts.append(f"**Turns**: {len(episodes)}")
    lines.append(" | ".join(meta_parts))
    lines.append("")
    lines.append("---")
    lines.append("")

    # Turns
    for i, ep in enumerate(episodes, 1):
        lines.append(f"## Turn {i}")
        lines.append("")

        # Question
        lines.append(f"**Q:** {ep['question']}")
        lines.append("")

        # Answer
        answer = ep.get("answer", "")
        lines.append("**A:**")
        lines.append("")
        lines.append(answer)
        lines.append("")

        # Metrics
        metrics = []
        if ep.get("confidence") is not None:
            metrics.append(f"Confidence: {ep['confidence']:.0%}")
        if ep.get("groundedness") is not None:
            metrics.append(f"Groundedness: {ep['groundedness']:.0%}")
        if ep.get("completeness") is not None:
            metrics.append(f"Completeness: {ep['completeness']:.0%}")
        if ep.get("steps_taken"):
            metrics.append(f"Steps: {ep['steps_taken']}")
        if ep.get("elapsed_seconds"):
            metrics.append(f"Time: {ep['elapsed_seconds']:.1f}s")
        if ep.get("entities_added"):
            metrics.append(f"Entities added: {ep['entities_added']}")
        if ep.get("rels_added"):
            metrics.append(f"Relationships added: {ep['rels_added']}")
        if metrics:
            lines.append(f"*{' | '.join(metrics)}*")
            lines.append("")

        # Assessment
        if ep.get("assessment_reasoning"):
            lines.append(f"> **Assessment:** {ep['assessment_reasoning']}")
            lines.append("")
        if ep.get("assessment_gaps"):
            gaps = ep["assessment_gaps"]
            if isinstance(gaps, str):
                try:
                    gaps = json.loads(gaps)
                except (json.JSONDecodeError, TypeError):
                    gaps = [gaps] if gaps else []
            if gaps:
                lines.append("**Knowledge gaps:**")
                for gap in gaps:
                    lines.append(f"- {gap}")
                lines.append("")

        # Reasoning trace
        actions_log = ep.get("actions_log", "[]")
        if isinstance(actions_log, str):
            try:
                actions_log = json.loads(actions_log)
            except (json.JSONDecodeError, TypeError):
                actions_log = []

        if actions_log:
            lines.append("<details>")
            lines.append(f"<summary>Reasoning Trace ({len(actions_log)} steps)</summary>")
            lines.append("")
            for j, action in enumerate(actions_log, 1):
                tool = action.get("tool", "?")
                args = action.get("args", "")
                obs = action.get("observation", "")
                lines.append(f"**Step {j}: {tool}**")
                if args:
                    lines.append("```")
                    lines.append(args)
                    lines.append("```")
                if obs:
                    # Truncate very long observations for readability
                    obs_str = str(obs)
                    if len(obs_str) > 1000:
                        obs_str = obs_str[:1000] + "\n\n... (truncated)"
                    lines.append("Result:")
                    lines.append("```")
                    lines.append(obs_str)
                    lines.append("```")
                lines.append("")
            lines.append("</details>")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Footer
    lines.append(f"*Exported from SynapseOS on {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


def export_session_to_pdf(
    session_ref: str,
    store: InstanceStore,
    output_path: str | Path,
) -> None:
    """Export a chat session to PDF. Requires ``reportlab`` (optional dependency)."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError:
        raise ImportError(
            "PDF export requires 'reportlab'. Install with: pip install reportlab"
        )

    session = _resolve_session(session_ref, store)
    episodes = store.get_session_episodes(session["session_id"])

    output_path = Path(output_path)
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    # Register Unicode-capable TTF font
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    _register_unicode_font(pdfmetrics, TTFont)

    styles = getSampleStyleSheet()

    # Override all styles to use Unicode font
    unicode_font = "DejaVuSans"
    unicode_font_bold = "DejaVuSans-Bold"
    unicode_font_mono = "DejaVuSansMono"
    for style in styles.byName.values():
        if hasattr(style, "fontName"):
            if "Bold" in style.fontName or "Heading" in style.name or "Title" in style.name:
                style.fontName = unicode_font_bold
            else:
                style.fontName = unicode_font

    title_style = styles["Title"]
    heading_style = styles["Heading2"]
    normal_style = styles["Normal"]
    code_style = ParagraphStyle(
        "Code",
        parent=normal_style,
        fontName=unicode_font_mono,
        fontSize=8,
        leading=10,
        leftIndent=12,
        backColor="#f0f0f0",
    )
    meta_style = ParagraphStyle(
        "Meta",
        parent=normal_style,
        fontSize=9,
        textColor="#666666",
        spaceAfter=6,
    )

    story: list[Any] = []

    # Title
    name = session.get("name") or session["session_id"][:8]
    domain = session.get("domain", "")
    started = (session.get("started_at") or "")[:19]
    story.append(Paragraph(f"Session: {_pdf_escape(name)}", title_style))
    story.append(Paragraph(
        f"Domain: {_pdf_escape(domain)} | Started: {started} | Turns: {len(episodes)}",
        meta_style,
    ))
    story.append(Spacer(1, 12))

    for i, ep in enumerate(episodes, 1):
        story.append(Paragraph(f"Turn {i}", heading_style))

        story.append(Paragraph(f"<b>Q:</b> {_pdf_escape(ep['question'])}", normal_style))
        story.append(Spacer(1, 6))

        answer = _pdf_escape(ep.get("answer", ""))
        # Simple line-break handling for PDF
        answer = answer.replace("\n", "<br/>")
        story.append(Paragraph(f"<b>A:</b><br/>{answer}", normal_style))
        story.append(Spacer(1, 6))

        # Metrics
        metrics = []
        if ep.get("confidence") is not None:
            metrics.append(f"Confidence: {ep['confidence']:.0%}")
        if ep.get("steps_taken"):
            metrics.append(f"Steps: {ep['steps_taken']}")
        if ep.get("elapsed_seconds"):
            metrics.append(f"Time: {ep['elapsed_seconds']:.1f}s")
        if metrics:
            story.append(Paragraph(" | ".join(metrics), meta_style))

        # Reasoning trace summary
        actions_log = ep.get("actions_log", "[]")
        if isinstance(actions_log, str):
            try:
                actions_log = json.loads(actions_log)
            except (json.JSONDecodeError, TypeError):
                actions_log = []
        if actions_log:
            story.append(Paragraph(f"<b>Reasoning Trace ({len(actions_log)} steps):</b>", normal_style))
            for j, action in enumerate(actions_log, 1):
                tool = action.get("tool", "?")
                args = action.get("args", "")
                if args:
                    args_short = args[:200] + ("..." if len(args) > 200 else "")
                    story.append(Paragraph(
                        f"Step {j} [{tool}]: {_pdf_escape(args_short)}",
                        code_style,
                    ))
            story.append(Spacer(1, 6))

        story.append(Spacer(1, 12))

    # Footer
    story.append(Paragraph(
        f"Exported from SynapseOS on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        meta_style,
    ))

    doc.build(story)


# -- Helpers ------------------------------------------------------------------

def _resolve_session(session_ref: str, store: InstanceStore) -> dict[str, Any]:
    """Resolve a session reference (name, ID, or prefix) to a session dict."""
    session = store.get_session_by_name(session_ref)
    if not session:
        session = store.get_last_session()
    if not session:
        raise ValueError(f"Session not found: {session_ref}")
    return session


_UNICODE_FONTS_REGISTERED = False


def _register_unicode_font(pdfmetrics, TTFont) -> None:
    """Register a Unicode-capable TTF font for PDF export (supports all languages)."""
    global _UNICODE_FONTS_REGISTERED
    if _UNICODE_FONTS_REGISTERED:
        return

    import os

    # Search for Unicode TTF fonts on the system (ordered by preference)
    candidates = [
        # macOS
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
        # Linux — DejaVu
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # Homebrew
        "/opt/homebrew/share/fonts/dejavu/DejaVuSans.ttf",
        # Windows
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]

    found = None
    for path in candidates:
        if os.path.exists(path):
            found = path
            break

    if found:
        pdfmetrics.registerFont(TTFont("DejaVuSans", found))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", found))
        pdfmetrics.registerFont(TTFont("DejaVuSansMono", found))
        _UNICODE_FONTS_REGISTERED = True
        return

    # No suitable font found — ReportLab will use Helvetica (ASCII only)
    import logging
    logging.getLogger(__name__).warning(
        "No Unicode TTF font found. PDF may not display non-ASCII characters correctly."
    )


def _pdf_escape(text: str) -> str:
    """Escape text for ReportLab XML paragraphs."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
