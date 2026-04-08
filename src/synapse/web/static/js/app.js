/* SynapseOS Web Demo — Alpine.js application */

// Auth helper — stores credentials
const Auth = {
    _creds: null,
    get() {
        if (!this._creds) {
            this._creds = localStorage.getItem('synapse_creds');
        }
        return this._creds;
    },
    set(user, pass) {
        this._creds = btoa(user + ':' + pass);
        localStorage.setItem('synapse_creds', this._creds);
    },
    header() {
        return { 'Authorization': 'Basic ' + this.get() };
    },
    wsToken() {
        const c = this.get();
        return c ? atob(c) : '';
    },
    clear() {
        this._creds = null;
        localStorage.removeItem('synapse_creds');
    },
    isSet() {
        return !!this.get();
    }
};

// API fetch helper with auth
async function api(path, opts = {}) {
    const res = await fetch(path, {
        ...opts,
        headers: { ...Auth.header(), ...(opts.headers || {}) },
    });
    if (res.status === 401) {
        Auth.clear();
        location.reload();
        return null;
    }
    return res.json();
}

// ---- Alpine components ----

function loginApp() {
    return {
        user: '',
        pass: '',
        error: '',
        async login() {
            this.error = '';
            Auth.set(this.user, this.pass);
            try {
                const r = await api('/api/graph/stats');
                if (r === null) {
                    this.error = 'Invalid credentials';
                    return;
                }
                // Reload to show main app
                location.reload();
            } catch (e) {
                this.error = 'Connection error';
                Auth.clear();
            }
        }
    };
}

function mainApp() {
    return {
        tab: 'chat',
        // Chat state
        sessions: [],
        currentSession: null,
        messages: [],
        input: '',
        sending: false,
        steps: [],
        ws: null,
        // Dashboard state
        stats: null,
        health: null,
        searchQuery: '',
        searchResults: [],
        triples: [],
        ontology: null,
        // Documents state
        documents: [],
        uploading: false,
        uploadStatus: '',
        // Review state
        reviewTab: 'entities',
        unverifiedEntities: [],
        unverifiedRels: [],
        unverifiedTriples: [],
        entityContext: null,
        reviewOntology: null,

        async init() {
            await this.loadSessions();
            this.newSession();
        },

        switchTab(t) {
            this.tab = t;
            if (t === 'dashboard') this.loadDashboard();
            if (t === 'documents') this.loadDocuments();
            if (t === 'review') this.loadReview();
        },

        // ---- Sessions ----
        async loadSessions() {
            this.sessions = await api('/api/sessions') || [];
        },

        newSession() {
            const id = crypto.randomUUID();
            this.currentSession = id;
            this.messages = [];
            this.steps = [];
            this.connectWS();
        },

        async resumeSession(s) {
            this.currentSession = s.session_id;
            this.messages = [];
            this.steps = [];
            const episodes = await api(`/api/sessions/${s.session_id}/episodes`) || [];
            for (const ep of episodes) {
                this.messages.push({ role: 'user', text: ep.question });
                this.messages.push({
                    role: 'assistant', text: ep.answer,
                    confidence: ep.confidence,
                    groundedness: ep.groundedness,
                    steps: ep.steps_taken,
                    elapsed: ep.elapsed_seconds,
                });
            }
            this.connectWS();
            this.$nextTick(() => this.scrollChat());
        },

        async deleteSession(sessionId) {
            if (!confirm('Delete this session?')) return;
            await api(`/api/sessions/${sessionId}`, { method: 'DELETE' });
            if (this.currentSession === sessionId) this.newSession();
            await this.loadSessions();
        },

        async exportSession() {
            if (!this.currentSession) return;
            const r = await api(`/api/sessions/${this.currentSession}/export`);
            if (r && r.markdown) {
                const blob = new Blob([r.markdown], { type: 'text/markdown' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `session_${this.currentSession.slice(0, 8)}.md`;
                a.click();
                URL.revokeObjectURL(url);
            }
        },

        // ---- WebSocket ----
        connectWS() {
            if (this.ws) { try { this.ws.close(); } catch(e) {} }
            const proto = location.protocol === 'https:' ? 'wss' : 'ws';
            const token = encodeURIComponent(Auth.wsToken());
            const url = `${proto}://${location.host}/api/chat/${this.currentSession}?token=${token}`;
            this.ws = new WebSocket(url);
            this.ws.onmessage = (e) => this.onWSMessage(JSON.parse(e.data));
            this.ws.onerror = () => { this.sending = false; };
            this.ws.onclose = () => { this.sending = false; };
        },

        sendMessage() {
            if (!this.input.trim() || this.sending || !this.ws) return;
            const text = this.input.trim();
            this.input = '';
            this.messages.push({ role: 'user', text });
            this.steps = [];
            this.sending = true;
            this.$nextTick(() => this.scrollChat());
            this.ws.send(JSON.stringify({ type: 'question', text }));
        },

        onWSMessage(msg) {
            if (msg.type === 'step') {
                this.steps.push(msg);
            } else if (msg.type === 'answer') {
                this.messages.push({
                    role: 'assistant',
                    text: msg.text,
                    confidence: msg.confidence,
                    groundedness: msg.groundedness,
                    completeness: msg.completeness,
                    steps: msg.steps,
                    elapsed: msg.elapsed,
                    assessment: msg.assessment || '',
                    gaps: msg.gaps || [],
                    debate_rounds: msg.debate_rounds || 0,
                    _showAssessment: false,
                });
                this.$nextTick(() => this.scrollChat());
            } else if (msg.type === 'done') {
                this.sending = false;
                this.loadSessions();
            } else if (msg.type === 'error') {
                this.messages.push({ role: 'assistant', text: `Error: ${msg.detail}` });
                this.sending = false;
            }
        },

        scrollChat() {
            const el = document.getElementById('chat-messages');
            if (el) el.scrollTop = el.scrollHeight;
        },

        renderMarkdown(text) {
            if (typeof marked !== 'undefined') {
                return marked.parse(text || '');
            }
            return (text || '').replace(/\n/g, '<br>');
        },

        // ---- Dashboard ----
        async loadDashboard() {
            this.stats = await api('/api/graph/stats');
            this.health = await api('/api/graph/health');
            this.ontology = await api('/api/graph/ontology');
            const t = await api('/api/graph/triples?limit=20');
            this.triples = t || [];
        },

        async searchEntities() {
            if (!this.searchQuery.trim()) return;
            this.searchResults = await api(`/api/graph/search?q=${encodeURIComponent(this.searchQuery)}`) || [];
        },

        // ---- Documents ----
        async loadDocuments() {
            this.documents = await api('/api/documents') || [];
        },

        async uploadFile(event) {
            const file = event.target.files[0];
            if (!file) return;
            this.uploading = true;
            this.uploadStatus = `Uploading ${file.name}...`;
            const form = new FormData();
            form.append('file', file);
            const r = await fetch('/api/documents/upload', {
                method: 'POST',
                headers: Auth.header(),
                body: form,
            }).then(r => r.json());

            if (r.task_id) {
                this.uploadStatus = 'Processing...';
                this.pollIngestion(r.task_id);
            } else {
                this.uploadStatus = r.error || 'Upload failed';
                this.uploading = false;
            }
        },

        async pollIngestion(taskId) {
            const check = async () => {
                const r = await api(`/api/ingestion/${taskId}`);
                if (!r) return;
                if (r.status === 'done') {
                    this.uploadStatus = `Done! ${r.entities} entities, ${r.relationships} relationships extracted.`;
                    this.uploading = false;
                    this.loadDocuments();
                } else if (r.status === 'error') {
                    this.uploadStatus = `Error: ${r.detail}`;
                    this.uploading = false;
                } else {
                    this.uploadStatus = r.detail || 'Processing...';
                    setTimeout(check, 3000);
                }
            };
            setTimeout(check, 2000);
        },

        // ---- Review ----
        async loadReview() {
            this.unverifiedEntities = await api('/api/review/entities') || [];
            this.unverifiedRels = await api('/api/review/relationships') || [];
            this.entityContext = null;
        },

        async loadTriples() {
            this.unverifiedTriples = await api('/api/review/triples') || [];
        },

        async loadOntology() {
            this.reviewOntology = await api('/api/review/ontology');
        },

        async showEntityContext(name) {
            this.entityContext = await api(`/api/review/entity/${encodeURIComponent(name)}/context`);
        },

        async verifyEntity(e) {
            await api('/api/review/entities/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ canonical_name: e.canonical_name, entity_type: e.entity_type }),
            });
            this.loadReview();
        },

        async rejectEntity(e) {
            await api('/api/review/entities/reject', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ canonical_name: e.canonical_name, entity_type: e.entity_type }),
            });
            this.loadReview();
        },

        async verifyRel(r) {
            await api('/api/review/relationships/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: r.subject, predicate: r.predicate, object: r.object }),
            });
            this.loadReview();
        },

        async rejectRel(r) {
            await api('/api/review/relationships/reject', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject: r.subject, predicate: r.predicate, object: r.object }),
            });
            this.loadReview();
        },

        async verifyAll() {
            await api('/api/review/verify-all', { method: 'POST' });
            this.loadReview();
        },
    };
}
