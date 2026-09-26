"""Feishu (Lark) custom-bot webhook client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import List, Optional

import requests

# Custom-bot webhook requests are capped at 20 KB; leave headroom for the card
# envelope and JSON escaping.
MAX_CONTENT_BYTES = 15_000

# Custom bots are rate limited to 5 req/s and 100 req/min.
SEND_INTERVAL_SECONDS = 1.0


class FeishuError(RuntimeError):
    pass


def _gen_sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def split_markdown(text: str, max_bytes: int = MAX_CONTENT_BYTES) -> List[str]:
    """Split markdown on line boundaries into chunks of at most ``max_bytes`` UTF-8 bytes."""
    chunks: List[str] = []
    current: List[str] = []
    size = 0
    in_code_block = False

    def flush():
        nonlocal current, size
        if current:
            chunk = "\n".join(current).strip("\n")
            if in_code_block:
                chunk += "\n```"
            if chunk:
                chunks.append(chunk)
        current, size = [], 0

    for line in text.split("\n"):
        line_bytes = len(line.encode("utf-8")) + 1
        if line_bytes > max_bytes:
            flush()
            encoded = line.encode("utf-8")
            for start in range(0, len(encoded), max_bytes):
                chunks.append(encoded[start:start + max_bytes].decode("utf-8", errors="ignore"))
            continue
        if size + line_bytes > max_bytes:
            reopen = in_code_block
            flush()
            if reopen:
                current, size = ["```"], 4
        current.append(line)
        size += line_bytes
        if line.lstrip().startswith("```"):
            in_code_block = not in_code_block
    flush()
    return chunks


class FeishuClient:
    def __init__(self, webhook_url: str, secret: Optional[str] = None, timeout: int = 15):
        if not webhook_url:
            raise ValueError("Feishu webhook URL cannot be empty")
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout = timeout

    def _post(self, payload: dict, retries: int = 3) -> dict:
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            body = dict(payload)
            if self.secret:
                timestamp = int(time.time())
                body["timestamp"] = str(timestamp)
                body["sign"] = _gen_sign(timestamp, self.secret)
            try:
                response = requests.post(
                    self.webhook_url,
                    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                result = response.json()
                if result.get("code", 0) != 0:
                    raise FeishuError(f"Feishu error {result.get('code')}: {result.get('msg')}")
                return result
            except (requests.RequestException, ValueError, FeishuError) as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise FeishuError(f"Failed to send Feishu message: {last_error}")

    def send_markdown_card(
        self,
        title: str,
        content: str,
        subtitle: str = "",
        button_url: Optional[str] = None,
        template: str = "blue",
    ) -> dict:
        elements = [{"tag": "markdown", "content": content}]
        if button_url:
            elements.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "查看完整报告"},
                    "type": "primary",
                    "behaviors": [{"type": "open_url", "default_url": button_url}],
                }
            )
        card = {
            "schema": "2.0",
            "config": {"update_multi": True, "summary": {"content": title}},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "subtitle": {"tag": "plain_text", "content": subtitle},
                "template": template,
            },
            "body": {"elements": elements},
        }
        return self._post({"msg_type": "interactive", "card": card})

    def send_report(
        self,
        title: str,
        markdown: str,
        subtitle: str = "",
        button_url: Optional[str] = None,
        max_parts: int = 8,
    ) -> int:
        """Send a (possibly long) markdown report as one or more cards. Returns the card count."""
        parts = split_markdown(markdown)
        if len(parts) > max_parts:
            parts = parts[:max_parts]
            hint = "请点击下方按钮查看完整报告" if button_url else "完整报告请在仓库 reports 目录中查看"
            parts[-1] += f"\n\n---\n\n*内容过长已截断，{hint}。*"
        total = len(parts)
        for i, part in enumerate(parts, 1):
            part_title = title if total == 1 else f"{title} ({i}/{total})"
            self.send_markdown_card(
                part_title,
                part,
                subtitle=subtitle,
                button_url=button_url if i == total else None,
            )
            if i < total:
                time.sleep(SEND_INTERVAL_SECONDS)
        return total
