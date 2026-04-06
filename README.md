# meeting-scribe

**AI-powered meeting summarizer built on [agent-forge](../agent-forge).**

Upload a Zoom transcript, get a complete Minutes of Meeting — action items with owners, key decisions, and next steps — auto-emailed to attendees and synced to Jira.

All AI processing is delegated to agent-forge. meeting-scribe handles ingestion, email, and Jira.

---

## How it works

```
Transcript (.vtt / .txt)
   │
   ▼
meeting-scribe backend
   │  parse + clean transcript
   │  build structured MOM goal
   │
   ▼
agent-forge /run/stream     ← all AI work happens here
   │  orchestrator plans agents
   │  agents analyze transcript, draft MOM
   │  synthesize into final report
   │
   ▼
meeting-scribe backend
   │  extract structured fields (summary, decisions, action items, next steps)
   │  emit mom_ready SSE event
   │
   ├── /email     → SMTP → attendees
   └── /jira/sync → Jira REST → create / update tickets
```

---

## Architecture

```
src/meeting_scribe/
├── api/
│   └── app.py              # FastAPI — /summarize/stream, /email, /jira/sync
├── ingestion/
│   ├── transcript.py       # Parse Zoom .vtt and plain .txt transcripts
│   └── zoom_api.py         # Placeholder — future Zoom OAuth + webhook support
├── integrations/
│   ├── email.py            # SMTP email sender
│   └── jira.py             # Jira REST client (stub mode until configured)
└── config/
    └── settings.py         # Env var config (Pydantic Settings)

app.py                      # Streamlit UI — 2 tabs: Meeting + Agent Activity
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# set AGENT_FORGE_URL, OPENAI_API_KEY, and optionally SMTP_* / JIRA_* in .env
```

agent-forge must be running before starting meeting-scribe.

---

## Running

**agent-forge backend** (port 8000):
```bash
cd ../agent-forge
uvicorn agent_forge.api.app:app --reload --port 8000
```

**meeting-scribe backend** (port 8001):
```bash
uvicorn meeting_scribe.api.app:app --reload --port 8001
```

**Frontend**:
```bash
streamlit run app.py
```

---

## API

### `GET /health`

Returns config status — confirms which integrations are active.

```json
{
  "status": "ok",
  "agent_forge_url": "http://localhost:8000",
  "email_configured": false,
  "jira_configured": false
}
```

### `POST /summarize/stream`

SSE stream. Send a transcript, receive agent activity + structured MOM.

```json
// Request
{
  "title": "Q2 Planning Sync",
  "transcript": "...",
  "filename": "meeting.vtt",
  "attendees": [{"name": "Alice", "email": "alice@example.com"}],
  "date": "2026-04-06",
  "max_rounds": 3,
  "provider": "openai"
}
```

SSE events emitted:

| Event | When |
|---|---|
| `orchestrator_tool_call` | Orchestrator researching |
| `plan_chunk` | Plan streaming |
| `plan_ready` | Agents/nodes defined |
| `agent_message` | Agent output |
| `stop_signal` | Conversation ended |
| `synthesis_chunk` | MOM streaming |
| `mom_ready` | Structured MOM extracted |
| `done` | Pipeline complete |
| `error` | On failure |

### `POST /email`

Send the MOM to all attendees with a known email address.

```json
{"mom": { ...mom_ready payload... }}
```

Requires `SMTP_*` env vars. Returns `{"sent": false, "reason": "..."}` if not configured.

### `POST /jira/sync`

Create or update Jira tickets for every action item.

Operates in **stub mode** when `JIRA_*` env vars are not set — logs what it would do and returns mock ticket keys so the rest of the pipeline runs end-to-end.

---

## Email

Uses SMTP with STARTTLS. Works with Gmail (create an App Password), Outlook, or any SMTP relay.

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=you@gmail.com
```

---

## Jira

Full Jira REST API v3 client is implemented. Operates in stub mode until credentials are set.

```env
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-token
JIRA_PROJECT_KEY=PROJ
```

When configured:
- Searches for existing tickets matching each action item
- Updates matching tickets with meeting context
- Creates new tickets for unmatched action items
- All tickets get a `meeting-scribe` label

---

## Transcript formats

| Format | Support |
|---|---|
| Zoom `.vtt` (WebVTT) | Parsed — timestamps and cue IDs stripped, speaker labels preserved |
| Plain `.txt` | Used as-is |
| Zoom API (live recording) | Placeholder — `ingestion/zoom_api.py` |

---

## Planned

- Zoom OAuth + webhook integration (auto-trigger on recording completion)
- Slack notification with MOM link
- Meeting history / search
