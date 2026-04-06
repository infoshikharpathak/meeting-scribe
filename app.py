from __future__ import annotations

"""
meeting-scribe Streamlit frontend.

Requires the backend to be running:
    uvicorn meeting_scribe.api.app:app --reload --port 8001

Run:
    streamlit run app.py
"""

import json

import httpx
import streamlit as st

API_URL = "http://localhost:8001"

# ── API helpers ───────────────────────────────────────────────────────────────

def stream_summarize(title: str, transcript: str, filename: str, attendees: list[dict], date: str, max_rounds: int):
    """Consume SSE events from POST /summarize/stream."""
    with httpx.Client(timeout=300) as client:
        with client.stream(
            "POST",
            f"{API_URL}/summarize/stream",
            headers={"Accept": "text/event-stream"},
            json={
                "title": title,
                "transcript": transcript,
                "filename": filename,
                "attendees": attendees,
                "date": date,
                "max_rounds": max_rounds,
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])


def send_email(mom: dict) -> dict:
    return httpx.post(f"{API_URL}/email", json={"mom": mom}, timeout=30).json()


def jira_sync(mom: dict) -> dict:
    return httpx.post(f"{API_URL}/jira/sync", json={"mom": mom}, timeout=60).json()


# ── Helpers ───────────────────────────────────────────────────────────────────

_AGENT_COLORS = ["#4F8EF7", "#F7874F", "#4FD18C", "#F7CF4F", "#C44FF7", "#F74F6E"]

def _agent_color(name: str, agent_names: list[str]) -> str:
    idx = agent_names.index(name) if name in agent_names else 0
    return _AGENT_COLORS[idx % len(_AGENT_COLORS)]


def render_agent_activity(messages: list[dict], agent_names: list[str], stop: dict | None) -> str:
    """Render agent messages as pure HTML to avoid Markdown parser dropping middle sections."""
    if not messages:
        return ""
    parts = []
    current_round = 0
    for msg in messages:
        rnd = msg.get("round", 1)
        if rnd != current_round:
            current_round = rnd
            parts.append(f'<p><strong>── Round {current_round} ──</strong></p>')
        color = _agent_color(msg["agent"], agent_names)
        content_html = msg["content"].replace("\n", "<br>")
        parts.append(
            f'<div style="margin-bottom:1em">'
            f'<span style="color:{color}; font-weight:600">{msg["agent"]}</span><br>'
            f'<span>{content_html}</span>'
            f'</div>'
            f'<hr style="border:none;border-top:1px solid #333;margin:0.5em 0"/>'
        )
    if stop:
        icon = "✅" if stop.get("stopped_by") == "orchestrator" else "⏹️"
        parts.append(f'<p>{icon} <em>{stop["reason"]}</em></p>')
    return "".join(parts)


def render_mom(mom: dict) -> str:
    """Render the structured MOM as Markdown."""
    lines = [f"# {mom['title']}", f"**Date:** {mom['date']}", ""]

    if mom.get("attendees"):
        lines.append("## Attendees")
        for a in mom["attendees"]:
            entry = a["name"]
            if a.get("email"):
                entry += f" ({a['email']})"
            lines.append(f"- {entry}")
        lines.append("")

    if mom.get("summary"):
        lines += ["## Executive Summary", mom["summary"], ""]

    if mom.get("decisions"):
        lines.append("## Key Decisions")
        for d in mom["decisions"]:
            lines.append(f"- {d}")
        lines.append("")

    if mom.get("action_items"):
        lines.append("## Action Items")
        lines.append("| Task | Owner | Deadline | Priority |")
        lines.append("|------|-------|----------|----------|")
        for ai in mom["action_items"]:
            lines.append(
                f"| {ai['task']} | {ai.get('owner','TBD')} | {ai.get('deadline','TBD')} | {ai.get('priority','Medium')} |"
            )
        lines.append("")

    if mom.get("next_steps"):
        lines.append("## Next Steps")
        for ns in mom["next_steps"]:
            lines.append(f"- {ns}")
        lines.append("")

    return "\n".join(lines)


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(page_title="meeting-scribe", page_icon="📝", layout="wide")
st.title("📝 meeting-scribe")

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_meeting, tab_agents = st.tabs(["📋 Meeting", "🤖 Agent Activity"])

# ── Tab 1: Meeting ────────────────────────────────────────────────────────────

with tab_meeting:
    col_input, col_output = st.columns([1, 1], gap="large")

    with col_input:
        st.subheader("Meeting Details")
        meeting_title = st.text_input("Meeting title", placeholder="Q2 Planning Sync")
        meeting_date  = st.text_input("Date", placeholder="2026-04-06")

        st.markdown("**Attendees** (add name + email, email optional)")
        if "attendee_rows" not in st.session_state:
            st.session_state.attendee_rows = [{"name": "", "email": ""}]

        for i, row in enumerate(st.session_state.attendee_rows):
            c1, c2, c3 = st.columns([2, 2, 0.5])
            row["name"]  = c1.text_input("Name",  value=row["name"],  key=f"aname_{i}", label_visibility="collapsed", placeholder="Name")
            row["email"] = c2.text_input("Email", value=row["email"], key=f"aemail_{i}", label_visibility="collapsed", placeholder="email@example.com")
            if c3.button("✕", key=f"del_{i}") and len(st.session_state.attendee_rows) > 1:
                st.session_state.attendee_rows.pop(i)
                st.rerun()

        if st.button("+ Add attendee"):
            st.session_state.attendee_rows.append({"name": "", "email": ""})
            st.rerun()

        st.divider()
        uploaded = st.file_uploader("Upload transcript (.vtt or .txt)", type=["vtt", "txt"])
        transcript_text = st.text_area("Or paste transcript", height=200, placeholder="Speaker A: Hello everyone...")

        col_btn, col_rounds = st.columns([3, 1])
        with col_btn:
            run_btn = st.button("▶ Summarize", type="primary", disabled=not (meeting_title and (uploaded or transcript_text.strip())))
        with col_rounds:
            max_rounds = st.number_input("Max rounds", min_value=1, max_value=10, value=3)

    with col_output:
        st.subheader("Minutes of Meeting")
        status_box   = st.empty()
        mom_display  = st.empty()
        action_row   = st.empty()

# ── Tab 2: Agent Activity ─────────────────────────────────────────────────────

with tab_agents:
    agents_legend  = st.empty()
    st.divider()
    agent_activity = st.empty()

if not run_btn:
    st.stop()

# ── Run pipeline ──────────────────────────────────────────────────────────────

# Resolve transcript
if uploaded:
    raw_transcript = uploaded.read().decode("utf-8")
    fname = uploaded.name
else:
    raw_transcript = transcript_text.strip()
    fname = "transcript.txt"

attendees = [
    {"name": r["name"], "email": r["email"]}
    for r in st.session_state.attendee_rows
    if r["name"].strip()
]

status_box.info("⏳ Connecting to backend...")

# State accumulated across events
agent_names:    list[str]  = []
conv_messages:  list[dict] = []
stop_signal:    dict | None = None
synthesis_text: str = ""
mom_data:       dict | None = None

for event in stream_summarize(meeting_title, raw_transcript, fname, attendees, meeting_date, int(max_rounds)):
    etype = event.get("type")

    if etype == "orchestrator_tool_call":
        status_box.info("🔍 Orchestrator researching...")

    elif etype == "plan_ready":
        strategy = event.get("strategy", "autogen")
        if strategy == "langgraph":
            nodes = event["spec"]["nodes"]
            agent_names = [n["name"] for n in nodes]
            status_box.info(f"⏳ Running LangGraph pipeline — {len(nodes)} node(s)...")
        else:
            specs = event.get("specs", [])
            agent_names = [s["name"] for s in specs]
            status_box.info(f"⏳ Agents debating — {len(specs)} agent(s)...")

        # Agent legend in Tab 2
        with agents_legend:
            if agent_names:
                cols = st.columns(len(agent_names))
                for i, name in enumerate(agent_names):
                    color = _agent_color(name, agent_names)
                    cols[i].markdown(
                        f'<span style="color:{color}; font-weight:700">● {name}</span>',
                        unsafe_allow_html=True,
                    )

    elif etype == "agent_message":
        conv_messages.append(event)
        agent_activity.markdown(
            render_agent_activity(conv_messages, agent_names, None),
            unsafe_allow_html=True,
        )

    elif etype == "stop_signal":
        stop_signal = event
        agent_activity.markdown(
            render_agent_activity(conv_messages, agent_names, stop_signal),
            unsafe_allow_html=True,
        )
        status_box.info("⏳ Synthesizing MOM...")

    elif etype == "synthesis_chunk":
        synthesis_text += event["text"]
        mom_display.markdown(synthesis_text + "▌")

    elif etype == "mom_ready":
        mom_data = event
        mom_display.markdown(render_mom(mom_data))

    elif etype == "done":
        status_box.success("✅ Done")

    elif etype == "error":
        status_box.error(f"Error: {event['message']}")
        st.stop()

# ── Post-run actions ──────────────────────────────────────────────────────────

if mom_data:
    with action_row.container():
        st.divider()
        c1, c2 = st.columns(2)

        if c1.button("📧 Email MOM to attendees"):
            result = send_email(mom_data)
            if result.get("sent"):
                st.success(f"Sent to: {', '.join(result['recipients'])}")
            else:
                st.warning(result.get("reason", "Email not sent."))

        if c2.button("🔗 Sync to Jira"):
            result = jira_sync(mom_data)
            stub = result.get("stub", True)
            tickets = result.get("results", [])
            if stub:
                st.info(f"Jira stub mode — would have created/updated {len(tickets)} ticket(s). Configure JIRA_* in .env to enable.")
                for t in tickets:
                    st.markdown(f"- [{t['key']}] {t['summary']} *(would be {t['action']})*")
            else:
                st.success(f"{len(tickets)} ticket(s) synced.")
                for t in tickets:
                    st.markdown(f"- [{t['key']}]({t['url']}) {t['summary']} — *{t['action']}*")
