"""Helpers for turning pull requests into prompt-friendly text."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, List, Tuple

from .github_client import PullRequest

# Sections that mark the "boilerplate tail" of a PR description; everything from
# the first match onwards is dropped to keep the input concise.
_TAIL_MARKERS = ("## Accuracy Tests", "## Checklist", "## Test", "cc @", "Pull Request resolved:")

# Total character budget for PR descriptions; per-PR length shrinks for busy repos.
BODY_BUDGET_CHARS = 150_000
MIN_BODY_CHARS = 120

_STATUS_LABELS = {"merged": "已合并", "open": "进行中", "draft": "草稿", "closed": "已关闭"}


def clean_up_body(body: str, max_len: int = 500) -> str:
    """Strip comments, images, URLs and boilerplate, collapse whitespace and cap length."""
    if not isinstance(body, str):
        return ""
    cleaned = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    for marker in _TAIL_MARKERS:
        idx = cleaned.find(marker)
        if idx > 0:
            cleaned = cleaned[:idx]
    cleaned = re.sub(r"^\s*[-*]\s*\[[ xX]\].*$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*#{1,6}\s*(What does this PR do\??|Summary|Description|Motivation)?\s*$",
                     "", cleaned, flags=re.MULTILINE | re.IGNORECASE)
    cleaned = re.sub(r"^\s*#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip() + "…"
    return cleaned


def split_bot_pulls(pulls: Iterable[PullRequest]) -> Tuple[List[PullRequest], List[PullRequest]]:
    humans, bots = [], []
    for pr in pulls:
        (bots if pr.is_bot else humans).append(pr)
    return humans, bots


def stats_line(pulls: List[PullRequest], bot_count: int = 0) -> str:
    counts = Counter(pr.status for pr in pulls)
    parts = [f"{_STATUS_LABELS[s]} {counts[s]}" for s in ("merged", "open", "draft", "closed") if counts[s]]
    line = f"**PR 统计**：共 {len(pulls)} 个（{' · '.join(parts)}）"
    if bot_count:
        line += f"，另有 {bot_count} 个机器人 PR 未计入"
    return line


def body_length_for(count: int, max_len: int = 500) -> int:
    if count <= 0:
        return max_len
    return max(MIN_BODY_CHARS, min(max_len, BODY_BUDGET_CHARS // count))


def pulls_to_prompt_input(pulls: List[PullRequest], body_max_len: int = 500) -> str:
    """Render pull requests as one compact block per PR, oldest first."""
    body_len = body_length_for(len(pulls), body_max_len)
    lines = []
    for pr in sorted(pulls, key=lambda p: p.number):
        labels = f" [labels: {', '.join(pr.labels)}]" if pr.labels else ""
        lines.append(f"#{pr.number} ({pr.status}) {pr.title} — @{pr.user}{labels}")
        body = clean_up_body(pr.body, body_len)
        if body:
            lines.append(f"  {body}")
    return "\n".join(lines)
