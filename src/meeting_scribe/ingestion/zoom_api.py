from __future__ import annotations

"""
Zoom API integration — placeholder for future OAuth + webhook support.

When implemented this module will:
  - Handle Zoom OAuth 2.0 flow to obtain access tokens
  - Register / receive webhooks for recording.completed events
  - Download VTT transcripts from Zoom Cloud Recordings API
  - Extract meeting metadata (title, date, participants) from Zoom API

Zoom API docs: https://developers.zoom.us/docs/api/
"""


class ZoomClient:
    """Placeholder Zoom API client. Not yet implemented."""

    def __init__(self, client_id: str, client_secret: str) -> None:
        raise NotImplementedError(
            "Zoom API integration is not yet implemented. "
            "Upload a .vtt transcript file manually for now."
        )

    async def get_transcript(self, meeting_id: str) -> str:
        """Download the VTT transcript for a completed cloud recording."""
        raise NotImplementedError

    async def get_participants(self, meeting_id: str) -> list[dict]:
        """Return participant list [{name, email}] for a meeting."""
        raise NotImplementedError
