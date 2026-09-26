"""AI summarization via any OpenAI-compatible chat-completions endpoint."""

from __future__ import annotations

import os
import re
from typing import Optional

from openai import OpenAI

_PROMPT_TEMPLATE = """\
Act as a Senior AI Software Architect. Analyze the following list of Pull \
Requests from {owner}/{repo} and provide a high-signal technical digest for a \
team of framework developers. Be professional, technical and objective. Keep \
it concise. Skip the "Here is your report" intro.

Start the report with key takeaways: a one-sentence summary of the most \
impactful trend this week, with the total number of PRs and the top active \
areas (with inline numbering for these areas). Then summarize these PRs by \
their categories (features, components, hardware backends, etc.).

**Formatting Instructions:**
- Write the entire report in {language}.
- Use numbering for the first level, bullet points for other levels.
- Keep code symbols, identifiers and PR titles in their original form; do NOT \
translate `code symbols` or proper nouns.
- Use backticks for code symbols (e.g., `DTensor`).
- Give the number and link for mentioned PRs (e.g., [PR#1234](URL)).

**Input Data:**
{pr_summary}
"""

# Maps short language codes to a human-readable name used in the prompt.
_LANGUAGE_NAMES = {
    "zh": "Simplified Chinese (简体中文)",
    "en": "English",
}


def _language_name(language: str) -> str:
    return _LANGUAGE_NAMES.get(language.lower(), language)


_LINK_URL_RE = re.compile(r"\]\((https?://[^)\s]+)\)")
# Models sometimes emit typographic dashes/spaces inside URLs, which breaks links.
_URL_CHAR_FIXES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u00a0": ""})


def fix_link_urls(markdown: str) -> str:
    return _LINK_URL_RE.sub(lambda m: f"]({m.group(1).translate(_URL_CHAR_FIXES)})", markdown)


def build_prompt(pr_summary: str, owner: str, repo: str, language: str = "zh") -> str:
    return _PROMPT_TEMPLATE.format(
        owner=owner, repo=repo, pr_summary=pr_summary, language=_language_name(language)
    )


def summarize(
    pr_summary: str,
    owner: str,
    repo: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 0.3,
    language: str = "zh",
) -> str:
    """Send the PR summary to an OpenAI-compatible model and return the digest.

    Any OpenAI-compatible endpoint works by setting ``base_url`` (and the
    matching ``api_key``/``model``), e.g. OpenAI, Azure OpenAI, DeepSeek,
    OpenRouter, a local vLLM/Ollama server, or Gemini's OpenAI-compatible API.
    """
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    base_url = base_url or os.getenv("OPENAI_BASE_URL") or None
    model = model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

    if not api_key:
        raise RuntimeError(
            "No API key found. Set OPENAI_API_KEY (and optionally OPENAI_BASE_URL)."
        )

    client = OpenAI(api_key=api_key, base_url=base_url)
    prompt = build_prompt(pr_summary, owner, repo, language=language)

    print(f"Sending PR summary to model '{model}'"
          + (f" via {base_url}" if base_url else "") + " ...")

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior AI software architect writing concise technical digests. "
                    f"Always write your response in {_language_name(language)}."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("Model returned an empty response.")
    return fix_link_urls(content)
