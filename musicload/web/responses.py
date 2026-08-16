"""Helpers for constructing web responses."""

import json


def sse_event(event: str, payload: dict) -> str:
    """Format a Server-Sent Event message."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"
