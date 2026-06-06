"""Helpers for turning pull requests into prompt-friendly text."""

from __future__ import annotations

import re
from typing import List

from .github_client import PullRequest

# Sections that mark the "boilerplate tail" of a PR description; everything from
# the first match onwards is dropped to keep the input concise.
_TAIL_MARKERS = ("## Accuracy Tests", "## Checklist", "## Test")


def clean_up_body(body: str, max_len: int = 500) -> str:
    """Strip HTML comments, collapse whitespace, drop boilerplate tails and cap length."""
    if not isinstance(body, str):
        return ""
    cleaned = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    cleaned = re.sub(r"\n+", "\n", cleaned)
    cleaned = re.sub(r"(\r\n)+", "\r\n", cleaned)
    for marker in _TAIL_MARKERS:
        idx = cleaned.find(marker)
        if idx > 0:
            cleaned = cleaned[:idx]
    return cleaned[:max_len].strip()


def pulls_to_prompt_input(pulls: List[PullRequest], body_max_len: int = 500) -> str:
    """Render a list of pull requests as a compact text block for the model."""
    lines = [f"\nThere are {len(pulls)} pull requests recently:"]
    for i, pr in enumerate(pulls, 1):
        labels = ", ".join(pr.labels) if pr.labels else "None"
        lines.append(f"{i}. PR #{pr.number}: {pr.title}")
        lines.append(f"   URL: {pr.url}")
        lines.append(f"   Author: {pr.user}")
        lines.append(f"   State: {pr.state}")
        lines.append(f"   Created at: {pr.created_at}")
        lines.append(f"   Labels: {labels}")
        lines.append(f"   Description: {clean_up_body(pr.body, body_max_len)}")
        lines.append("\n")
    return "\n".join(lines)
