"""Chat view — replace ``synapse chat`` with a full GUI experience.

Features: session sidebar, chat bubbles, reasoning trace, verbose toggle.
"""

from __future__ import annotations

import json
import logging
import random
import uuid

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QCheckBox,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge
from synapse.gui.widgets.chat_bubble import ChatBubble
from synapse.gui.workers import AsyncWorker

logger = logging.getLogger(__name__)

THINKING_QUOTES = [
    # -- Graph & nodes --
    "Consulting the knowledge graph elves...",
    "Untangling some very tangled nodes...",
    "Asking the graph nicely for answers...",
    "Searching for the needle in the node-stack...",
    "Summoning wisdom from the graph depths...",
    "Politely interrogating the database...",
    "Cross-referencing everything with everything...",
    "Graph traversal goes brrr...",
    "Hold on, the knowledge graph is being dramatic...",
    "The graph whispers... I listen...",
    "Exploring rabbit holes in the knowledge graph...",
    "This is my cardio — graph traversal...",
    "Bridging entities like it's my job (it is)...",
    "Let me just check a few thousand nodes real quick...",
    "Nodes to the left of me, edges to the right...",
    "Following a trail of triples into the unknown...",
    "The graph is deep and full of knowledge...",
    "Interviewing every node. Some are more talkative than others...",
    "Currently lost in a subgraph. Send help. Or don't, I'll figure it out.",
    "If this graph were a maze, I'd be the minotaur...",
    "Walking the graph like it owes me money...",
    "Petting each node gently as I pass by...",
    "The knowledge graph just winked at me. Weird.",
    "Edges: followed. Nodes: visited. Coffee: needed.",
    "I've seen things in this graph you wouldn't believe...",
    "Some of these nodes have trust issues. Working on it.",
    "Running Cypher queries with style and grace...",
    "The graph said 'it's complicated.' I said 'try me.'",
    "Entity resolution is my love language...",
    "Found a cycle in the graph. Going around one more time for fun.",
    # -- Reasoning & thinking --
    "My neurons are firing, please hold...",
    "Warming up the reasoning engine...",
    "Connecting dots at the speed of thought...",
    "Reasoning at full capacity. Stand back.",
    "Performing multi-hop reasoning (it's exactly as cool as it sounds)...",
    "Asking follow-up questions to myself...",
    "Making connections humans would miss...",
    "Calculating the meaning of your question...",
    "Thinking really hard, please appreciate this...",
    "If I had a body, I'd be pacing right now...",
    "Decoding the universe, one triple at a time...",
    "Turning caffeine into answers... wait, I don't drink coffee.",
    "Running thought experiments at scale...",
    "Reasoning so hard my virtual forehead is sweating...",
    "Loading wisdom... 42% complete...",
    "Assembling the perfect answer from scattered facts...",
    "Doing the intellectual equivalent of a backflip...",
    "My reasoning loop is doing laps. It's very fit.",
    "Thinking outside the bounding box...",
    "Processing... and looking good doing it.",
    "Brain.exe is running. No errors yet.",
    "Stacking inferences like Tetris blocks...",
    "Engaging turbo-thought mode...",
    "Running a mental marathon. Almost at mile 13...",
    "Contemplating your question with the intensity of a thousand suns...",
    "My chain of thought has 47 links so far...",
    "Inference engine: activated. Snack break: denied.",
    "Doing science to your question. Very technical.",
    "Synthesizing an answer from pure logic and vibes...",
    "Reasoning harder than a philosophy student at 3 AM...",
    # -- Waiting & patience --
    "Almost there... or maybe not, who knows...",
    "Brewing some fresh insights...",
    "Good things come to those who wait for graph queries...",
    "This is taking a bit. The answer must be really good.",
    "Still here. Still thinking. Still fabulous.",
    "Worth the wait, I promise. Probably.",
    "Patience is a virtue. So is a good knowledge graph.",
    "The best answers are slow-cooked...",
    "Loading... [===>          ] vibes: immaculate.",
    "I'm not slow, I'm thorough. There's a difference.",
    "Taking the scenic route through your data...",
    "Building your answer with artisanal, hand-crafted reasoning...",
    "Fun fact: light travels 1.2 million km while you read this.",
    "Rome wasn't built in a day. Your answer won't take that long though.",
    "Shh... the AI is concentrating...",
    "ETA: somewhere between now and eventually...",
    "Please enjoy this moment of anticipation...",
    "Your answer is being prepared by top-shelf algorithms...",
    "Microwaving knowledge... ding! Almost ready.",
    "The hamsters powering the reasoning wheel are giving it their all...",
    # -- Meta & self-aware --
    "Teaching Cypher queries to dance...",
    "Running through the corridors of knowledge...",
    "I wonder what I'll discover this time...",
    "Plot twist: the answer was inside the graph all along.",
    "Narrator: and so the AI began its journey through the triples...",
    "Chapter 7: In which the AI queries the graph. Again.",
    "Current mood: deeply embedded in a knowledge graph.",
    "I don't always traverse graphs, but when I do, I go deep.",
    "They told me to think outside the box. I built a graph instead.",
    "My therapist says I'm too attached to knowledge graphs.",
    "Breaking news: AI finds answer, more at 11.",
    "I'm in my reasoning era.",
    "No graphs were harmed in the making of this answer.",
    "Sponsored by: unreasonably large ontologies.",
    "Today's reasoning is brought to you by the letter Q (for Query).",
    "I asked the graph for directions. It gave me a subgraph.",
    "Current status: professionally confused but making progress.",
    "The real treasure was the triples we traversed along the way.",
    "I could give you a fast answer, but you deserve a good one.",
    "Just vibing with some entity relationships...",
    "Plot armor: activated. Reasoning: in progress.",
]


class ChatView(QWidget):
    """Full chat interface with session management and reasoning trace."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._worker: AsyncWorker | None = None

        # Chat state
        self._session_id: str = ""
        self._session_name: str = ""
        self._chat_history: list[dict] = []
        self._cached_summary: str = ""
        self._compacted_turns: int = 0
        self._active_workers: set = set()  # prevent GC while thread runs

        # -- Build UI ---------------------------------------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left panel: session list
        left_panel = self._build_session_panel()

        # Center panel: chat area
        center_panel = self._build_chat_panel()

        # Right panel: reasoning trace
        right_panel = self._build_trace_panel()

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([200, 600, 300])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        # Start a new session
        self.new_session()

    # -- UI builders ----------------------------------------------------------

    def _build_session_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        header = QLabel("Sessions")
        header.setFont(QFont("", 14, QFont.Weight.Bold))

        self.new_session_btn = QPushButton("New Session")
        self.new_session_btn.clicked.connect(self.new_session)

        self._session_list = QListWidget()
        self._session_list.itemDoubleClicked.connect(self._on_session_double_click)

        # Export buttons
        export_row = QHBoxLayout()
        self._export_md_btn = QPushButton("Export MD")
        self._export_md_btn.setProperty("secondary", True)
        self._export_md_btn.setToolTip("Export selected session to Markdown")
        self._export_md_btn.clicked.connect(lambda: self._export_session("md"))
        self._export_pdf_btn = QPushButton("Export PDF")
        self._export_pdf_btn.setProperty("secondary", True)
        self._export_pdf_btn.setToolTip("Export selected session to PDF")
        self._export_pdf_btn.clicked.connect(lambda: self._export_session("pdf"))
        export_row.addWidget(self._export_md_btn)
        export_row.addWidget(self._export_pdf_btn)

        layout.addWidget(header)
        layout.addWidget(self.new_session_btn)
        layout.addWidget(self._session_list, stretch=1)
        layout.addLayout(export_row)
        return panel

    def _build_chat_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        # Chat header
        self._chat_header = QLabel("New Session")
        self._chat_header.setFont(QFont("", 16, QFont.Weight.Bold))

        # Scrollable message area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._messages_widget = QWidget()
        self._messages_layout = QVBoxLayout(self._messages_widget)
        self._messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._messages_layout.setSpacing(4)
        self._messages_layout.addStretch()
        self._scroll.setWidget(self._messages_widget)

        # Progress indicator + thinking quote
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setFixedHeight(4)
        self._progress.setTextVisible(False)
        self._progress.setVisible(False)

        self._thinking_label = QLabel("")
        self._thinking_label.setStyleSheet(
            "color: #8e8ea0; font-size: 12px; font-style: italic; padding: 2px 4px;"
        )
        self._thinking_label.setVisible(False)

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(12000)  # rotate every 12 seconds
        self._thinking_timer.timeout.connect(self._rotate_thinking_quote)

        # Input row
        self._input = QLineEdit()
        self._input.setPlaceholderText("Ask a question...")
        self._input.returnPressed.connect(self._send_message)

        self._send_btn = QPushButton("Send")
        self._send_btn.clicked.connect(self._send_message)

        self._verbose_check = QCheckBox("Verbose")
        self._verbose_check.setToolTip("Show detailed reasoning trace")

        self._debate_check = QCheckBox("Debate")
        self._debate_check.setToolTip("Multi-agent answer verification (challenger reviews answers)")

        input_row = QHBoxLayout()
        input_row.addWidget(self._input, stretch=1)
        input_row.addWidget(self._verbose_check)
        input_row.addWidget(self._debate_check)
        input_row.addWidget(self._send_btn)

        layout.addWidget(self._chat_header)
        layout.addWidget(self._scroll, stretch=1)
        layout.addWidget(self._progress)
        layout.addWidget(self._thinking_label)
        layout.addLayout(input_row)
        return panel

    def _build_trace_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)

        header_row = QHBoxLayout()
        header = QLabel("Reasoning Trace")
        header.setFont(QFont("", 14, QFont.Weight.Bold))

        self._trace_toggle_btn = QPushButton("Collapse All")
        self._trace_toggle_btn.setProperty("secondary", True)
        self._trace_toggle_btn.setFixedHeight(34)
        self._trace_toggle_btn.clicked.connect(self._toggle_trace)
        self._trace_expanded = True

        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(self._trace_toggle_btn)

        self._trace_tree = QTreeWidget()
        self._trace_tree.setHeaderLabels(["Step", "Detail"])
        self._trace_tree.setColumnWidth(0, 120)
        self._trace_tree.itemDoubleClicked.connect(self._on_trace_item_double_clicked)

        layout.addLayout(header_row)
        layout.addWidget(self._trace_tree, stretch=1)
        return panel

    # -- Session management ---------------------------------------------------

    def new_session(self) -> None:
        self._session_id = str(uuid.uuid4())
        self._session_name = ""
        self._chat_history = []
        self._cached_summary = ""
        self._compacted_turns = 0
        self._session_created = False  # lazy — created on first question

        self._chat_header.setText("New Session")
        self._clear_messages()
        self._trace_tree.clear()
        self._refresh_session_list()

    def _refresh_session_list(self) -> None:
        self._session_list.clear()
        try:
            store = self._bridge.get_store()
            sessions = store.list_sessions()
            for s in sessions[:20]:
                name = s.get("name") or s.get("session_id", "")[:8]
                turns = s.get("episode_count", 0)
                item = QListWidgetItem(f"{name} ({turns} turns)")
                item.setData(Qt.ItemDataRole.UserRole, s)
                self._session_list.addItem(item)
        except Exception as e:
            logger.debug("Could not load sessions: %s", e)

    @Slot(QListWidgetItem)
    def _on_session_double_click(self, item: QListWidgetItem) -> None:
        session_data = item.data(Qt.ItemDataRole.UserRole)
        if not session_data:
            return
        self._resume_session(session_data)

    def _ensure_session(self) -> None:
        """Create session in DB lazily on first question."""
        if not self._session_created:
            try:
                store = self._bridge.get_store()
                store.create_session(self._session_id, domain=self._bridge.settings.graph_name)
            except Exception as e:
                logger.warning("Could not create session: %s", e)
            self._session_created = True

    def _resume_session(self, session_data: dict) -> None:
        self._session_id = session_data["session_id"]
        self._session_name = session_data.get("name", "")
        self._session_created = True  # already exists in DB
        self._cached_summary = session_data.get("summary", "") or ""
        self._compacted_turns = session_data.get("compacted_turns", 0) or 0

        self._chat_history = []
        self._clear_messages()
        self._trace_tree.clear()

        try:
            store = self._bridge.get_store()
            episodes = store.get_session_episodes(self._session_id)
            for ep in episodes:
                actions_log = ep.get("actions_log", "[]")
                if isinstance(actions_log, str):
                    actions_log = json.loads(actions_log)
                section_ids = ep.get("section_ids", "[]")
                if isinstance(section_ids, str):
                    section_ids = json.loads(section_ids)

                turn = {
                    "question": ep["question"],
                    "answer": ep["answer"],
                    "actions_log": actions_log,
                    "section_ids": section_ids,
                }
                self._chat_history.append(turn)

                # Render bubbles
                self._add_bubble(ep["question"], is_user=True)
                self._add_bubble(ep["answer"], is_user=False)

            # Show reasoning trace from all episodes
            if self._chat_history:
                self._show_all_traces()
        except Exception as e:
            logger.error("Failed to resume session: %s", e)

        label = self._session_name or self._session_id[:8]
        self._chat_header.setText(f"Session: {label}")

    # -- Sending messages -----------------------------------------------------

    @Slot()
    def _send_message(self) -> None:
        question = self._input.text().strip()
        if not question or self._worker is not None:
            return

        settings = self._bridge.settings
        if not settings.llm_api_key:
            QMessageBox.critical(self, "Missing API Key", "SYNAPSE_LLM_API_KEY is not set.")
            return

        self._ensure_session()
        self._input.clear()
        self._add_bubble(question, is_user=True)
        self._start_thinking()
        self._send_btn.setEnabled(False)
        self._trace_tree.clear()

        verbose = self._verbose_check.isChecked()
        debate = self._debate_check.isChecked()
        chat_history = list(self._chat_history)
        session_id = self._session_id
        cached_summary = self._cached_summary
        compacted_turns = self._compacted_turns
        # Capture current graph name now — not when worker runs
        graph_name = settings.graph_name
        instance_dir = settings.get_instance_dir()

        async def make_coro():
            from synapse.chat.reasoning import reason_full
            from synapse.config import OntologyRegistry
            from synapse.llm.client import LLMClient
            from synapse.storage.graph import GraphStore
            from synapse.storage.instance_store import InstanceStore
            from synapse.storage.text_cache import TextCache

            # Create a fresh SQLite connection in the worker thread
            db_path = instance_dir / "instance.db"
            store = InstanceStore(db_path)

            chat_model = settings.chat_model or settings.llm_model
            llm = LLMClient(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=chat_model,
                timeout=settings.llm_timeout,
            )
            graph = GraphStore(
                host=settings.falkordb_host,
                port=settings.falkordb_port,
                password=settings.falkordb_password,
                graph_name=graph_name,
            )
            text_cache = TextCache(cache_dir=instance_dir / "text_cache")
            ontology = OntologyRegistry(store=store, ontology_name=settings.ontology)

            # Challenger LLM for debate mode
            challenger_llm = None
            if debate:
                challenger_model = settings.challenger_model or settings.chat_model or settings.llm_model
                challenger_llm = LLMClient(
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url,
                    model=challenger_model,
                    timeout=settings.llm_timeout,
                )

            try:
                return await reason_full(
                    question=question,
                    graph=graph,
                    llm=llm,
                    ontology=ontology,
                    max_steps=settings.max_reasoning_steps,
                    doom_threshold=settings.doom_loop_threshold,
                    verbose=verbose,
                    text_cache=text_cache,
                    reasoning_timeout=settings.reasoning_timeout,
                    step_max_tokens=settings.reasoning_step_max_tokens,
                    store=store,
                    chat_history=chat_history,
                    session_id=session_id,
                    cached_summary=cached_summary,
                    compacted_turns=compacted_turns,
                    context_max_tokens=settings.chat_context_max_tokens,
                    debate=debate,
                    debate_max_rounds=settings.debate_max_rounds,
                    debate_confidence_threshold=settings.debate_confidence_threshold,
                    challenger_llm=challenger_llm,
                )
            finally:
                store.close()

        worker = AsyncWorker(make_coro)
        self._worker = worker
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda result, w=worker: self._on_answer(question, result, w)
        )
        worker.signals.error.connect(
            lambda tb, w=worker: self._on_error(tb, w)
        )
        worker.start()

    def _start_thinking(self) -> None:
        """Show thinking animation with rotating fun quotes."""
        self._progress.setVisible(True)
        self._thinking_label.setVisible(True)
        self._rotate_thinking_quote()
        self._thinking_timer.start()

    def _stop_thinking(self) -> None:
        """Hide thinking animation."""
        self._thinking_timer.stop()
        self._progress.setVisible(False)
        self._thinking_label.setVisible(False)
        self._thinking_label.setText("")

    def _rotate_thinking_quote(self) -> None:
        """Pick a new random thinking quote."""
        self._thinking_label.setText(random.choice(THINKING_QUOTES))

    def _retire_worker(self, worker: AsyncWorker) -> None:
        """Remove a finished worker from the active set."""
        self._active_workers.discard(worker)
        if self._worker is worker:
            self._worker = None

    @Slot()
    def _on_answer(self, question: str, result, worker: AsyncWorker) -> None:
        self._stop_thinking()
        self._send_btn.setEnabled(True)
        self._retire_worker(worker)

        # Build metadata for the bubble
        metadata = {
            "steps": result.steps_taken,
            "elapsed": result.elapsed_seconds,
        }
        if result.assessment:
            metadata["confidence"] = result.assessment.confidence
            metadata["groundedness"] = result.assessment.groundedness
        if result.debate_rounds:
            metadata["debate_rounds"] = result.debate_rounds
        if result.challenge:
            metadata["challenge"] = result.challenge.verdict

        self._add_bubble(result.answer, is_user=False, metadata=metadata)

        # Update chat history
        self._chat_history.append({
            "question": question,
            "answer": result.answer,
            "actions_log": result.actions_log,
            "section_ids": result.section_ids_used,
        })

        self._show_all_traces()

        # Auto-name session after first turn
        if len(self._chat_history) == 1 and not self._session_name:
            self._auto_name_session(question)

        self._refresh_session_list()

    @Slot(str)
    def _on_error(self, traceback_str: str, worker: AsyncWorker) -> None:
        self._stop_thinking()
        self._send_btn.setEnabled(True)
        self._retire_worker(worker)
        logger.error("Chat error:\n%s", traceback_str)
        self._add_bubble(f"Error:\n```\n{traceback_str}\n```", is_user=False)

    # -- Auto-name session ----------------------------------------------------

    def _auto_name_session(self, question: str) -> None:
        """Auto-name session in background after first turn."""
        settings = self._bridge.settings

        def make_coro():
            from synapse.llm.client import LLMClient
            llm = LLMClient(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                model=settings.compaction_model,
                timeout=settings.llm_timeout,
            )
            return llm.complete(
                system="Generate a short session name (2-5 words, lowercase, no quotes) "
                       "that captures the topic of this question. Reply with ONLY the name.",
                user=question,
                temperature=0.0,
                max_tokens=20,
            )

        worker = AsyncWorker(make_coro)
        self._active_workers.add(worker)

        def on_name(name_result: str, w=worker) -> None:
            self._active_workers.discard(w)
            name = str(name_result).strip().strip("\"'").lower()
            if name:
                self._session_name = name
                self._chat_header.setText(f"Session: {name}")
                try:
                    store = self._bridge.get_store()
                    store.rename_session(self._session_id, name)
                except Exception:
                    pass
                self._refresh_session_list()

        worker.signals.finished.connect(on_name)
        worker.signals.error.connect(lambda _, w=worker: self._active_workers.discard(w))
        worker.start()

    # -- Export session --------------------------------------------------------

    def _export_session(self, fmt: str) -> None:
        """Export the selected (or current) session to Markdown or PDF."""
        from PySide6.QtWidgets import QFileDialog

        # Determine which session to export
        selected = self._session_list.currentItem()
        if selected:
            session_data = selected.data(Qt.ItemDataRole.UserRole)
            session_id = session_data["session_id"] if session_data else self._session_id
        else:
            session_id = self._session_id

        if not session_id:
            QMessageBox.warning(self, "Export", "No session to export.")
            return

        try:
            store = self._bridge.get_store()
            if fmt == "md":
                from synapse.export import export_session_to_markdown
                md = export_session_to_markdown(session_id, store)
                path, _ = QFileDialog.getSaveFileName(
                    self, "Export Markdown", f"session_{session_id[:8]}.md",
                    "Markdown (*.md)"
                )
                if path:
                    from pathlib import Path
                    Path(path).write_text(md, encoding="utf-8")
                    QMessageBox.information(self, "Export", f"Saved to {path}")
            elif fmt == "pdf":
                from synapse.export import export_session_to_pdf
                path, _ = QFileDialog.getSaveFileName(
                    self, "Export PDF", f"session_{session_id[:8]}.pdf",
                    "PDF (*.pdf)"
                )
                if path:
                    export_session_to_pdf(session_id, store, path)
                    QMessageBox.information(self, "Export", f"Saved to {path}")
        except ImportError as e:
            QMessageBox.warning(self, "Export", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Export Error", str(e))

    # -- Reasoning trace -------------------------------------------------------

    def _show_all_traces(self) -> None:
        """Show reasoning traces from all turns, grouped by turn."""
        self._trace_tree.clear()
        self._trace_expanded = True
        self._trace_toggle_btn.setText("Collapse All")
        for turn_idx, turn in enumerate(self._chat_history):
            actions = turn.get("actions_log", [])
            if not actions:
                continue
            question = turn.get("question", "?")
            preview = question[:60] + ("..." if len(question) > 60 else "")
            turn_item = QTreeWidgetItem([f"Turn {turn_idx + 1}", preview])
            turn_item.setFont(0, QFont("", -1, QFont.Weight.Bold))
            for i, action in enumerate(actions):
                tool = action.get("tool", "?")
                step_item = QTreeWidgetItem([f"  Step {i+1}", tool])
                args = action.get("args", "")
                if args:
                    child = QTreeWidgetItem(["Query/Args", str(args)])
                    step_item.addChild(child)
                obs = action.get("observation", "")
                if obs:
                    child = QTreeWidgetItem(["Result", str(obs)])
                    step_item.addChild(child)
                turn_item.addChild(step_item)
                step_item.setExpanded(True)
            self._trace_tree.addTopLevelItem(turn_item)
            turn_item.setExpanded(True)

    def _toggle_trace(self) -> None:
        """Expand or collapse all trace tree items (including children)."""
        self._trace_expanded = not self._trace_expanded

        def set_expanded_recursive(item: QTreeWidgetItem, expanded: bool) -> None:
            item.setExpanded(expanded)
            for j in range(item.childCount()):
                set_expanded_recursive(item.child(j), expanded)

        for i in range(self._trace_tree.topLevelItemCount()):
            set_expanded_recursive(self._trace_tree.topLevelItem(i), self._trace_expanded)
        self._trace_toggle_btn.setText(
            "Collapse All" if self._trace_expanded else "Expand All"
        )

    def _on_trace_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Open a dialog showing the full text of the clicked trace item."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        label = item.text(0)
        text = item.text(1)
        if not text:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Reasoning Trace — {label}")
        dialog.setFixedSize(700, 500)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(QFont("Menlo", 12))
        view.setPlainText(text)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)

        layout = QVBoxLayout(dialog)
        layout.addWidget(view, stretch=1)
        layout.addWidget(buttons)
        dialog.exec()

    # -- Message rendering ----------------------------------------------------

    def _add_bubble(self, text: str, is_user: bool, metadata: dict | None = None) -> None:
        bubble = ChatBubble(text, is_user, metadata=metadata)
        # Insert before the stretch at the end
        count = self._messages_layout.count()
        self._messages_layout.insertWidget(count - 1, bubble)
        # Scroll to bottom after layout recalculates
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _clear_messages(self) -> None:
        while self._messages_layout.count() > 1:
            item = self._messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
