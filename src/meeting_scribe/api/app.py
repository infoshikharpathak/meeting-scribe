from __future__ import annotations

"""
meeting-scribe FastAPI backend.

Run:
    uvicorn meeting_scribe.api.app:app --reload --port 8001

Endpoints:
    GET  /health
    POST /summarize/stream   — transcript → MOM + agent activity (SSE)
    POST /email              — send MOM to attendees
    POST /jira/sync          — create / update Jira tickets from action items
"""

import json
import logging

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from meeting_scribe.config.settings import settings
from meeting_scribe.ingestion.transcript import parse as parse_transcript
from meeting_scribe.integrations.email import send_mom
from meeting_scribe.integrations.jira import JiraClient, JiraTicket

log = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="meeting-scribe",
    version="0.1.0",
    description=(
        "AI-powered meeting summarizer built on agent-forge. "
        "Upload a transcript, get a complete MOM, email attendees, sync Jira."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Schemas ───────────────────────────────────────────────────────────────────

class Attendee(BaseModel):
    name: str
    email: str = ""


class ActionItem(BaseModel):
    task: str
    owner: str = ""
    deadline: str = ""
    priority: str = "medium"


class MOM(BaseModel):
    title: str
    date: str
    attendees: list[Attendee]
    summary: str
    decisions: list[str]
    action_items: list[ActionItem]
    next_steps: list[str]
    raw: str  # full synthesis markdown, always preserved


class SummarizeRequest(BaseModel):
    title: str
    transcript: str
    filename: str = ""
    attendees: list[Attendee] = []
    date: str = ""
    max_rounds: int = 3
    provider: str = "openai"


class EmailRequest(BaseModel):
    mom: MOM


class JiraSyncRequest(BaseModel):
    mom: MOM


# ── Goal builder ──────────────────────────────────────────────────────────────

def _build_goal(req: SummarizeRequest, clean_transcript: str) -> str:
    attendees_str = (
        ", ".join(f"{a.name}" + (f" ({a.email})" if a.email else "") for a in req.attendees)
        or "see transcript"
    )
    return f"""\
Analyze the following meeting transcript and produce a complete, professional \
Minutes of Meeting (MOM) document.

Meeting title: {req.title}
Date: {req.date or "see transcript"}
Attendees: {attendees_str}

Your MOM MUST include all of the following sections:

1. **Executive Summary** — 2-3 sentence overview of what was discussed and decided.
2. **Key Decisions** — bulleted list of concrete decisions made during the meeting.
3. **Action Items** — table or bulleted list. For each item include:
   - Task description
   - Owner (person responsible — infer from transcript if not explicit)
   - Target deadline (if mentioned, otherwise "TBD")
   - Priority (High / Medium / Low)
4. **Next Steps** — upcoming activities or follow-ups beyond the action items.

Be specific and factual — only include what was actually discussed in the transcript. \
Do not invent information.

Transcript:
---
{clean_transcript}
---"""


# ── MOM extractor ─────────────────────────────────────────────────────────────

async def _extract_mom(raw: str, req: SummarizeRequest) -> MOM:
    """
    Post-process the agent-forge synthesis into a structured MOM.

    Uses a lightweight OpenAI call to extract structured fields from the
    free-text synthesis. Falls back to an empty-field MOM if extraction fails.
    """
    if not settings.openai_api_key:
        return MOM(
            title=req.title,
            date=req.date,
            attendees=req.attendees,
            summary="",
            decisions=[],
            action_items=[],
            next_steps=[],
            raw=raw,
        )

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    system = (
        "You are a structured data extractor. Given a Minutes of Meeting document, "
        "return ONLY a JSON object with these exact keys:\n"
        "- summary: string\n"
        "- decisions: list of strings\n"
        "- action_items: list of {task, owner, deadline, priority}\n"
        "- next_steps: list of strings\n"
        "- attendees: list of names found in the document\n"
        "Return valid JSON only. No markdown, no explanation."
    )
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": raw},
            ],
        )
        data = json.loads(response.choices[0].message.content)

        # Merge extracted attendees with any provided by the user
        extracted_names: list[str] = data.get("attendees", [])
        attendee_map = {a.name: a for a in req.attendees}
        for name in extracted_names:
            if name not in attendee_map:
                attendee_map[name] = Attendee(name=name)
        attendees = list(attendee_map.values())

        return MOM(
            title=req.title,
            date=req.date,
            attendees=attendees,
            summary=data.get("summary", ""),
            decisions=data.get("decisions", []),
            action_items=[ActionItem(**ai) for ai in data.get("action_items", [])],
            next_steps=data.get("next_steps", []),
            raw=raw,
        )
    except Exception as exc:
        log.warning("[extract_mom] extraction failed: %s — using raw text only", exc)
        return MOM(
            title=req.title,
            date=req.date,
            attendees=req.attendees,
            summary="",
            decisions=[],
            action_items=[],
            next_steps=[],
            raw=raw,
        )


# ── SSE proxy ─────────────────────────────────────────────────────────────────

async def _summarize_stream(req: SummarizeRequest):
    """
    Core generator — proxies agent-forge SSE events, then emits mom_ready.

    Yields SSE-formatted strings:
        agent_message, stop_signal, synthesis_chunk  → proxied from agent-forge
        mom_ready                                    → structured MOM object
        done                                         → final signal
        error                                        → on any failure
    """
    clean = parse_transcript(req.transcript, req.filename)
    goal = _build_goal(req, clean)

    synthesis_chunks: list[str] = []

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST",
                f"{settings.agent_forge_url}/run/stream",
                params={"detail": "full"},
                json={"goal": goal, "max_rounds": req.max_rounds, "provider": req.provider},
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    etype = event.get("type")

                    if etype == "synthesis_chunk":
                        synthesis_chunks.append(event["text"])
                        yield f"data: {json.dumps(event)}\n\n"

                    elif etype == "done":
                        # Extract structured MOM then emit mom_ready
                        raw_mom = "".join(synthesis_chunks)
                        mom = await _extract_mom(raw_mom, req)
                        yield f"data: {json.dumps({'type': 'mom_ready', **mom.model_dump()})}\n\n"
                        yield f"data: {json.dumps({'type': 'done'})}\n\n"

                    elif etype == "error":
                        yield f"data: {json.dumps(event)}\n\n"
                        return

                    else:
                        # agent_message, stop_signal, orchestrator_tool_call, plan_* — proxy as-is
                        yield f"data: {json.dumps(event)}\n\n"

    except Exception as exc:
        log.error("[summarize] pipeline error: %s", exc)
        yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness probe."""
    return {
        "status": "ok",
        "agent_forge_url": settings.agent_forge_url,
        "email_configured": settings.email_configured,
        "jira_configured": settings.jira_configured,
    }


@app.post("/summarize/stream")
async def summarize_stream(req: SummarizeRequest):
    """
    Stream the full pipeline: agent activity + structured MOM.

    SSE events emitted:
      orchestrator_tool_call, plan_chunk, plan_ready  — planning phase
      agent_message, stop_signal                      — execution phase
      synthesis_chunk                                 — synthesis streaming
      mom_ready                                       — full structured MOM
      done                                            — pipeline complete
      error                                           — on failure
    """
    return StreamingResponse(
        _summarize_stream(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/email")
async def email_mom(req: EmailRequest):
    """Send the MOM to all attendees with a known email address."""
    if not settings.email_configured:
        return {"sent": False, "reason": "SMTP not configured. Set SMTP_* vars in .env."}

    recipients = [a.email for a in req.mom.attendees if a.email]
    if not recipients:
        return {"sent": False, "reason": "No attendee email addresses available."}

    send_mom(
        to_addresses=recipients,
        meeting_title=req.mom.title,
        meeting_date=req.mom.date,
        mom_markdown=req.mom.raw,
        smtp_host=settings.smtp_host,
        smtp_port=settings.smtp_port,
        smtp_user=settings.smtp_user,
        smtp_password=settings.smtp_password,
        from_address=settings.email_from,
    )
    return {"sent": True, "recipients": recipients}


@app.post("/jira/sync")
async def jira_sync(req: JiraSyncRequest):
    """
    Create Jira tickets for all action items.
    Updates existing tickets if keywords match.

    Operates in stub mode when JIRA_* env vars are not set.
    """
    client = JiraClient(
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        project_key=settings.jira_project_key,
    )

    results: list[dict] = []
    for ai in req.mom.action_items:
        # Search for existing tickets matching this task
        matches = await client.search([ai.task[:50]])

        priority_map = {"high": "High", "medium": "Medium", "low": "Low"}
        ticket = JiraTicket(
            summary=ai.task,
            description=(
                f"Action item from meeting: {req.mom.title} ({req.mom.date})\n\n"
                f"Owner: {ai.owner or 'TBD'}\n"
                f"Deadline: {ai.deadline or 'TBD'}\n\n"
                f"Meeting summary:\n{req.mom.summary}"
            ),
            assignee=ai.owner,
            due_date=ai.deadline,
            priority=priority_map.get(ai.priority.lower(), "Medium"),
            labels=["meeting-scribe"],
        )

        if matches:
            result = await client.update(matches[0]["key"], ticket)
        else:
            result = await client.create(ticket)

        results.append({
            "key": result.key,
            "url": result.url,
            "action": result.action,
            "summary": result.summary,
        })
        log.info("[jira] %s %s", result.action, result.key)

    return {"results": results, "stub": not settings.jira_configured}
