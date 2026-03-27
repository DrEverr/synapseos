"""Chat message bubble widget with proper Markdown rendering.

Uses QTextBrowser for rich HTML display — supports headers, bold, italic,
code blocks, inline code, lists (ordered/unordered), tables, and links.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QLabel,
    QVBoxLayout,
    QWidget,
)

# -- Markdown → HTML converter -----------------------------------------------


def markdown_to_html(text: str) -> str:
    """Convert Markdown text to HTML suitable for QTextBrowser.

    Handles: headers, bold, italic, code blocks (fenced), inline code,
    unordered lists, ordered lists, tables, links, horizontal rules.
    """
    # Handle escaped newlines from JSON storage and strip wrapping quotes
    text = text.replace("\\n", "\n")
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    lines = text.split("\n")
    html_parts: list[str] = []
    i = 0
    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []

    while i < len(lines):
        line = lines[i]

        # --- Fenced code blocks ---
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                code_lines = []
                i += 1
                continue
            else:
                code_text = _escape("\n".join(code_lines))
                html_parts.append(
                    f'<pre style="background-color:#222230; padding:10px; '
                    f'border-radius:6px; font-family:Menlo,monospace; '
                    f'font-size:12px; color:#e4e4ed; overflow-x:auto;">'
                    f"<code>{code_text}</code></pre>"
                )
                in_code_block = False
                code_lines = []
                i += 1
                continue

        if in_code_block:
            code_lines.append(line)
            i += 1
            continue

        # --- Table detection ---
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|[-:|  ]+\|\s*$", lines[i + 1]):
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            html_parts.append(_render_table(table_lines))
            continue

        # --- Horizontal rule ---
        if re.match(r"^-{3,}$|^\*{3,}$|^_{3,}$", line.strip()):
            html_parts.append('<hr style="border-color:#2a2a38;">')
            i += 1
            continue

        # --- Headers ---
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            level = len(m.group(1))
            content = _inline_format(m.group(2))
            sizes = {1: 20, 2: 17, 3: 15, 4: 14, 5: 13, 6: 12}
            size = sizes.get(level, 13)
            html_parts.append(
                f'<p style="font-size:{size}px; font-weight:bold; '
                f'margin:8px 0 4px 0; color:#e4e4ed;">{content}</p>'
            )
            i += 1
            continue

        # --- Unordered list ---
        if re.match(r"^[-*+]\s+", line.strip()):
            list_items = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i].strip()):
                content = re.sub(r"^[-*+]\s+", "", lines[i].strip())
                list_items.append(f"<li>{_inline_format(content)}</li>")
                i += 1
            html_parts.append(
                '<ul style="margin:4px 0; padding-left:20px;">'
                + "".join(list_items)
                + "</ul>"
            )
            continue

        # --- Ordered list (use manual numbering — Qt doesn't render <ol> correctly) ---
        if re.match(r"^\d+\.\s+", line.strip()):
            list_items = []
            num = 1
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                m_num = re.match(r"^(\d+)\.\s+", lines[i].strip())
                if m_num:
                    num = int(m_num.group(1))
                content = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                list_items.append(
                    f'<p style="margin:2px 0 2px 8px;">'
                    f'<b>{num}.</b> {_inline_format(content)}</p>'
                )
                num += 1
                i += 1
            html_parts.append("".join(list_items)
            )
            continue

        # --- Blockquote ---
        if line.strip().startswith(">"):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            quote_html = "<br>".join(_inline_format(l) for l in quote_lines)
            html_parts.append(
                f'<blockquote style="border-left:3px solid #7c5cfc; '
                f'padding-left:10px; margin:4px 0; color:#8e8ea0;">'
                f"{quote_html}</blockquote>"
            )
            continue

        # --- Empty line ---
        if not line.strip():
            html_parts.append("<br>")
            i += 1
            continue

        # --- Regular paragraph ---
        html_parts.append(f"<p style=\"margin:2px 0;\">{_inline_format(line)}</p>")
        i += 1

    # Close unclosed code block
    if in_code_block and code_lines:
        code_text = _escape("\n".join(code_lines))
        html_parts.append(
            f'<pre style="background-color:#222230; padding:10px; border-radius:6px; '
            f'font-family:Menlo,monospace; font-size:12px; color:#e4e4ed;">'
            f"<code>{code_text}</code></pre>"
        )

    return "\n".join(html_parts)


def _inline_format(text: str) -> str:
    """Apply inline Markdown formatting: bold, italic, code, links."""
    text = _escape(text)
    # Inline code (must be before bold/italic to avoid conflicts)
    text = re.sub(
        r"`([^`]+)`",
        r'<code style="background-color:#222230; padding:2px 5px; border-radius:3px; '
        r'font-family:Menlo,monospace; font-size:12px;">\1</code>',
        text,
    )
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    text = re.sub(r"_(.+?)_", r"<i>\1</i>", text)
    # Links
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" style="color:#9b7dff;">\1</a>',
        text,
    )
    return text


def _render_table(lines: list[str]) -> str:
    """Render a Markdown table as an HTML table."""
    if len(lines) < 2:
        return ""

    def parse_row(line: str) -> list[str]:
        cells = line.strip().strip("|").split("|")
        return [c.strip() for c in cells]

    headers = parse_row(lines[0])
    # lines[1] is the separator row
    rows = [parse_row(line) for line in lines[2:]]

    html = (
        '<table style="border-collapse:collapse; margin:8px 0; width:100%;">'
        "<thead><tr>"
    )
    for h in headers:
        html += (
            f'<th style="border:1px solid #2a2a38; padding:6px 10px; '
            f'background-color:#222230; color:#e4e4ed; font-weight:bold; '
            f'text-align:left;">{_inline_format(h)}</th>'
        )
    html += "</tr></thead><tbody>"
    for row in rows:
        html += "<tr>"
        for cell in row:
            html += (
                f'<td style="border:1px solid #2a2a38; padding:6px 10px; '
                f'color:#e4e4ed;">{_inline_format(cell)}</td>'
            )
        html += "</tr>"
    html += "</tbody></table>"
    return html


from html import escape as _escape


# -- Chat bubble widget ------------------------------------------------------


class ChatBubble(QWidget):
    """A single chat message bubble (user or assistant).

    Assistant messages render full Markdown via QTextBrowser.
    User messages display as plain text.
    """

    def __init__(
        self,
        text: str,
        is_user: bool,
        metadata: dict | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_user = is_user

        if is_user:
            # Simple label for user messages — right-aligned, subtle bg
            widget = QLabel(_escape(text).replace("\n", "<br>"))
            widget.setWordWrap(True)
            widget.setTextFormat(Qt.TextFormat.RichText)
            widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            widget.setStyleSheet("""
                QLabel {
                    background-color: #7c5cfc;
                    border-radius: 16px 16px 4px 16px;
                    padding: 12px 16px;
                    font-size: 13px;
                    color: #ffffff;
                }
            """)
        else:
            # QLabel with rich HTML for assistant messages (stable, no resize crashes)
            html_content = markdown_to_html(text)
            widget = QLabel()
            widget.setWordWrap(True)
            widget.setTextFormat(Qt.TextFormat.RichText)
            widget.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
            widget.setOpenExternalLinks(True)
            widget.setStyleSheet("""
                QLabel {
                    background-color: #1a1a22;
                    border: 1px solid #2a2a38;
                    border-radius: 16px 16px 16px 4px;
                    padding: 14px 18px;
                    font-size: 13px;
                    color: #e4e4ed;
                }
            """)
            widget.setText(html_content)

        # Metadata badges (confidence, steps, elapsed)
        meta_label = None
        if metadata and not is_user:
            parts = []
            if "confidence" in metadata:
                parts.append(f"Confidence {metadata['confidence']:.0%}")
            if "groundedness" in metadata:
                parts.append(f"Grounded {metadata['groundedness']:.0%}")
            if "steps" in metadata:
                parts.append(f"{metadata['steps']} steps")
            if "elapsed" in metadata:
                parts.append(f"{metadata['elapsed']:.1f}s")
            if parts:
                meta_label = QLabel("  \u00b7  ".join(parts))
                meta_label.setStyleSheet(
                    "color: #55556a; font-size: 11px; padding: 2px 18px;"
                )

        # Layout — full width, user gets right-aligned text color accent
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        layout.addWidget(widget)
        if meta_label:
            layout.addWidget(meta_label)
