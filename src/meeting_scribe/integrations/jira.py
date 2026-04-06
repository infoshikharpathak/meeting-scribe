from __future__ import annotations

"""
Jira integration — creates and updates tickets from MOM action items.

Status: STUB — all methods are implemented with the correct interface but
do not make real API calls until JIRA_BASE_URL / JIRA_API_TOKEN are configured.

When you have a Jira project:
  1. Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY in .env
  2. The methods below will hit the real Jira REST API automatically.

Jira REST API docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
"""

import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("uvicorn.error")


@dataclass
class JiraTicket:
    """Represents a Jira ticket to create or update."""
    summary: str
    description: str
    assignee: str = ""
    due_date: str = ""          # ISO format: YYYY-MM-DD
    priority: str = "Medium"    # Highest / High / Medium / Low / Lowest
    labels: list[str] = field(default_factory=list)
    issue_type: str = "Task"


@dataclass
class JiraResult:
    """Result of a create or update operation."""
    key: str            # e.g. PROJ-42
    url: str            # e.g. https://org.atlassian.net/browse/PROJ-42
    action: str         # "created" or "updated"
    summary: str


class JiraClient:
    """
    Thin Jira REST API client.

    Operates in stub mode when Jira credentials are not configured —
    logs what it would do and returns mock results so the rest of the
    pipeline can run end-to-end without a real Jira project.
    """

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        project_key: str,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._project_key = project_key
        self._stub = not (base_url and email and api_token)
        self._auth = (email, api_token) if not self._stub else ("", "")

        if self._stub:
            log.warning(
                "[jira] Running in STUB mode — no real API calls will be made. "
                "Set JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env to enable."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(self, keywords: list[str]) -> list[dict]:
        """
        Search for existing tickets matching any of the keywords.

        Returns a list of dicts with keys: key, summary, status, url.
        """
        if self._stub:
            log.info("[jira:stub] search keywords=%s → []", keywords)
            return []

        jql = " OR ".join(
            f'summary ~ "{kw}" OR description ~ "{kw}"' for kw in keywords
        )
        jql += f' AND project = "{self._project_key}"'
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/rest/api/3/search",
                auth=self._auth,
                params={"jql": jql, "maxResults": 10, "fields": "summary,status"},
            )
            resp.raise_for_status()
            issues = resp.json().get("issues", [])
            return [
                {
                    "key": i["key"],
                    "summary": i["fields"]["summary"],
                    "status": i["fields"]["status"]["name"],
                    "url": f"{self._base_url}/browse/{i['key']}",
                }
                for i in issues
            ]

    async def create(self, ticket: JiraTicket) -> JiraResult:
        """Create a new Jira ticket and return its key and URL."""
        if self._stub:
            key = f"{self._project_key}-???"
            log.info("[jira:stub] would create: %s | %s", key, ticket.summary)
            return JiraResult(
                key=key,
                url=f"https://stub.atlassian.net/browse/{key}",
                action="created",
                summary=ticket.summary,
            )

        payload = {
            "fields": {
                "project": {"key": self._project_key},
                "summary": ticket.summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": ticket.description}],
                        }
                    ],
                },
                "issuetype": {"name": ticket.issue_type},
                "priority": {"name": ticket.priority.capitalize()},
                "labels": ticket.labels,
            }
        }
        if ticket.due_date:
            payload["fields"]["duedate"] = ticket.due_date
        if ticket.assignee:
            payload["fields"]["assignee"] = {"name": ticket.assignee}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/rest/api/3/issue",
                auth=self._auth,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            key = data["key"]
            log.info("[jira] created %s: %s", key, ticket.summary)
            return JiraResult(
                key=key,
                url=f"{self._base_url}/browse/{key}",
                action="created",
                summary=ticket.summary,
            )

    async def update(self, issue_key: str, ticket: JiraTicket) -> JiraResult:
        """Append meeting context to an existing ticket's description."""
        if self._stub:
            log.info("[jira:stub] would update: %s | %s", issue_key, ticket.summary)
            return JiraResult(
                key=issue_key,
                url=f"https://stub.atlassian.net/browse/{issue_key}",
                action="updated",
                summary=ticket.summary,
            )

        payload = {
            "fields": {
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": ticket.description}],
                        }
                    ],
                }
            }
        }
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self._base_url}/rest/api/3/issue/{issue_key}",
                auth=self._auth,
                json=payload,
            )
            resp.raise_for_status()
            log.info("[jira] updated %s: %s", issue_key, ticket.summary)
            return JiraResult(
                key=issue_key,
                url=f"{self._base_url}/browse/{issue_key}",
                action="updated",
                summary=ticket.summary,
            )
