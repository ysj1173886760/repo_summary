"""AI summarization via any OpenAI-compatible chat-completions endpoint."""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Iterable, Optional

from openai import OpenAI

_PROMPT_TEMPLATE = """\
You are a senior AI infrastructure architect writing the weekly pull-request \
digest of {owner}/{repo} for framework engineers who want to know what changed \
and why it matters.

The input lists {count} pull requests created during the week. Each entry is:
#<number> (<status>) <title> — @<author> [labels]
  <trimmed description>
Status is one of: merged, open, draft, closed. "closed" only means no merge \
was recorded; do not describe closed PRs as abandoned or rejected.

## Output format (Markdown, follow exactly)

## {h_overview}
2-3 sentences on the most important themes of the week. Do not state PR \
counts; they are shown separately.

**{h_areas}**：1. <area>；2. <area>；3. <area> (3-5 areas, ordered by significance)

## {h_highlights}
3-5 bullets on the most impactful changes, each as \
`- **<short headline>**：<what changed and why it matters> #1234 #1235`

## {h_details}
One `### <area>` section per active area (4-8 sections), each with 2-8 bullets \
as `- **<topic>**：<one sentence> #1234 #1235`. Do not repeat changes already \
covered in "{h_highlights}"; cover the remaining notable work instead.

## {h_watch}
Bullets for BC-breaking changes, deprecations, reverts, notable open RFCs or \
risks. Omit this whole section if there is nothing notable.

## Rules
- Write in {language}. Keep API, module, class, function and model names and \
other technical terms in English; wrap code identifiers in backticks.
- Reference PRs only as `#1234`. Never write URLs or markdown links; links are \
added automatically. Only cite PR numbers that appear in the input.
- Group related PRs (a ghstack series, the same feature) into one bullet \
instead of listing them one by one; cite at most 6 PR numbers per bullet. \
Put each PR in at most one "{h_details}" section.
- Prioritize merged PRs. Whenever a bullet is mainly about open or draft PRs, \
end its headline with "{wip}"; use no other status markers.
- Skip trivial PRs (typos, lint, version bumps, disabling flaky tests) unless \
they form a notable pattern.
- Be factual: describe only what titles and descriptions support. If the \
meaning of a term is unclear, keep the original English wording instead of \
guessing a translation.
- Keep the whole digest under about {max_chars} characters and never repeat \
an item. No preamble or closing remarks.

## Input
{pr_input}
"""

_HEADINGS = {
    "zh": {
        "h_overview": "本周概览",
        "h_areas": "最活跃领域",
        "h_highlights": "重点进展",
        "h_details": "分领域动态",
        "h_watch": "值得关注",
        "wip": "（进行中）",
    },
    "en": {
        "h_overview": "Overview",
        "h_areas": "Most active areas",
        "h_highlights": "Highlights",
        "h_details": "By area",
        "h_watch": "Worth watching",
        "wip": "(in progress)",
    },
}

# Maps short language codes to a human-readable name used in the prompt.
_LANGUAGE_NAMES = {
    "zh": "Simplified Chinese (简体中文)",
    "en": "English",
}

_LINK_URL_RE = re.compile(r"\]\((https?://[^)\s]+)\)")
# Models sometimes emit typographic dashes/spaces inside URLs, which breaks links.
_URL_CHAR_FIXES = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u00a0": ""})

# Existing markdown links and inline code are left untouched when linkifying.
_PROTECTED_RE = re.compile(r"\[[^\]\n]*\]\([^)\s]*\)|`[^`\n]*`")
_PR_REF_RE = re.compile(r"\[?(?<![\w/&#])(?:PR\s*)?#(\d{2,7})\b\]?(?!\()")


def _language_name(language: str) -> str:
    return _LANGUAGE_NAMES.get(language.lower(), language)


def fix_link_urls(markdown: str) -> str:
    return _LINK_URL_RE.sub(lambda m: f"]({m.group(1).translate(_URL_CHAR_FIXES)})", markdown)


def linkify_prs(markdown: str, owner: str, repo: str, known: Optional[Iterable[int]] = None) -> str:
    """Turn bare ``#1234`` / ``PR#1234`` / ``[PR#1234]`` references into PR links."""
    known_set = set(known) if known is not None else None

    def repl(m: re.Match) -> str:
        number = int(m.group(1))
        if known_set is not None and number not in known_set:
            return m.group(0)
        return f"[#{number}](https://github.com/{owner}/{repo}/pull/{number})"

    out, last = [], 0
    for protected in _PROTECTED_RE.finditer(markdown):
        out.append(_PR_REF_RE.sub(repl, markdown[last:protected.start()]))
        out.append(protected.group(0))
        last = protected.end()
    out.append(_PR_REF_RE.sub(repl, markdown[last:]))
    return "".join(out)


def _segments(text: str):
    for line in text.split("\n"):
        for seg in re.split(r"[；;。]", line):
            seg = seg.strip(" -*")
            if len(seg) >= 15:
                yield seg


def looks_degenerate(text: str, max_repeats: int = 3) -> bool:
    """Detect the model getting stuck repeating the same items."""
    counts = Counter(_segments(text))
    return bool(counts) and counts.most_common(1)[0][1] > max_repeats


def drop_repeats(text: str) -> str:
    """Last-resort cleanup: drop repeated lines and repeated clauses within a line."""
    seen_lines, seen_segs, out = set(), set(), []
    for line in text.split("\n"):
        if line.strip() and line in seen_lines:
            continue
        seen_lines.add(line)
        parts = re.split(r"(?<=[；;])", line)
        kept = []
        for part in parts:
            key = part.strip(" -*；;")
            if len(key) >= 15 and key in seen_segs:
                continue
            seen_segs.add(key)
            kept.append(part)
        out.append("".join(kept))
    return "\n".join(out)


def build_prompt(pr_summary: str, owner: str, repo: str, language: str = "zh", count: int = 0) -> str:
    headings = _HEADINGS.get(language.lower(), _HEADINGS["en"])
    return _PROMPT_TEMPLATE.format(
        owner=owner,
        repo=repo,
        pr_input=pr_summary,
        count=count,
        language=_language_name(language),
        max_chars=2500 if count < 200 else 4000,
        **headings,
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
    pr_numbers: Optional[Iterable[int]] = None,
    max_tokens: int = 32000,
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

    numbers = list(pr_numbers) if pr_numbers is not None else None
    client = OpenAI(api_key=api_key, base_url=base_url)
    prompt = build_prompt(pr_summary, owner, repo, language=language, count=len(numbers or []))
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior AI software architect writing concise technical digests. "
                f"Always write your response in {_language_name(language)}."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    print(f"Sending PR summary to model '{model}'"
          + (f" via {base_url}" if base_url else "") + " ...")

    content = ""
    for attempt in range(2):
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            frequency_penalty=0.3 * attempt,
            messages=messages,
        )
        choice = response.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            raise RuntimeError("Model returned an empty response.")
        if choice.finish_reason != "length" and not looks_degenerate(content):
            break
        print(f"Model output looks truncated or repetitive (attempt {attempt + 1}), retrying...")
    else:
        content = drop_repeats(content)

    return linkify_prs(fix_link_urls(content), owner, repo, numbers)
