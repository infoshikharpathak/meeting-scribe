from __future__ import annotations

"""
Transcript ingestion — parses Zoom .vtt and plain .txt transcripts
into a clean speaker-labelled string ready for agent-forge.
"""

import re


_VTT_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}")
_VTT_CUE_ID = re.compile(r"^\d+$")


def parse(text: str, filename: str = "") -> str:
    """
    Parse a transcript into a clean, speaker-labelled string.

    Supports:
    - Zoom .vtt (WebVTT) — strips timestamps and cue IDs, preserves speaker labels
    - Plain .txt — returned as-is (stripped)

    Args:
        text:     Raw transcript file content.
        filename: Original filename — used to detect format (.vtt vs .txt).

    Returns:
        Clean transcript text ready for the AI pipeline.
    """
    if filename.lower().endswith(".vtt") or text.lstrip().startswith("WEBVTT"):
        return _parse_vtt(text)
    return text.strip()


def _parse_vtt(text: str) -> str:
    """Strip VTT headers, timestamps, and cue IDs. Keep speaker labels + dialogue."""
    lines = text.splitlines()
    output: list[str] = []
    skip_next_blank = False

    for line in lines:
        line = line.strip()

        # Skip VTT header
        if line == "WEBVTT" or line.startswith("WEBVTT"):
            continue

        # Skip NOTE blocks
        if line.startswith("NOTE"):
            skip_next_blank = True
            continue

        if skip_next_blank:
            if line == "":
                skip_next_blank = False
            continue

        # Skip timestamps
        if _VTT_TIMESTAMP.match(line):
            continue

        # Skip cue IDs (bare integers)
        if _VTT_CUE_ID.match(line):
            continue

        # Skip blank lines (collapse them)
        if not line:
            if output and output[-1] != "":
                output.append("")
            continue

        # Remove VTT inline tags like <00:00:01.000><c>text</c>
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line:
            output.append(line)

    # Collapse multiple blank lines
    result = "\n".join(output)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()
