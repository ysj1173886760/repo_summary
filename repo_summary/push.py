"""Push generated reports to per-repository Feishu groups.

Webhooks are configured with the ``FEISHU_WEBHOOKS`` environment variable, a
JSON object mapping ``owner/repo`` to either a webhook URL or an object with
``url`` and an optional signing ``secret``::

    {
      "pytorch/pytorch": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
      "nvidia/megatron-lm": {"url": "https://...", "secret": "..."}
    }

Pushed reports are recorded in ``<reports-dir>/.feishu_push_state.json`` so each
report is sent only once. The first push to a repo is a cold start: only the
latest ``--cold-start`` reports are sent (oldest first) and older ones are
marked as skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .feishu import SEND_INTERVAL_SECONDS, FeishuClient, FeishuError

STATE_FILENAME = ".feishu_push_state.json"
_REPORT_RE = re.compile(r"^report-\d{8}\.md$")
_TITLE_RE = re.compile(r"^#\s*Pull Request Digest for (\S+) \((\d{8})-(\d{8})\)\s*$")


def load_webhooks(raw: Optional[str] = None) -> Dict[str, Tuple[str, Optional[str]]]:
    raw = raw if raw is not None else os.getenv("FEISHU_WEBHOOKS", "")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"FEISHU_WEBHOOKS is not valid JSON: {exc}")
    webhooks = {}
    for repo, value in data.items():
        if isinstance(value, str):
            url, secret = value, None
        else:
            url, secret = value.get("url"), value.get("secret")
        if url:
            webhooks[repo.lower()] = (url, secret or None)
    return webhooks


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def discover_reports(reports_dir: str) -> Dict[str, Tuple[str, List[str]]]:
    """Return ``{owner/repo: (dir_name, [report files sorted oldest first])}``."""
    found = {}
    if not os.path.isdir(reports_dir):
        return found
    for dir_name in sorted(os.listdir(reports_dir)):
        path = os.path.join(reports_dir, dir_name)
        if not os.path.isdir(path) or "_" not in dir_name:
            continue
        # GitHub owners cannot contain underscores, so the first one separates owner/repo.
        owner, repo = dir_name.split("_", 1)
        files = sorted(f for f in os.listdir(path) if _REPORT_RE.match(f))
        if files:
            found[f"{owner}/{repo}".lower()] = (dir_name, files)
    return found


def _parse_report(path: str, repo: str) -> Tuple[str, str, str]:
    """Return (title, subtitle, body) for a report file."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    first_line, _, rest = text.partition("\n")
    match = _TITLE_RE.match(first_line)
    if not match:
        return f"{repo} PR 周报", os.path.basename(path), text
    start, end = match.group(2), match.group(3)
    subtitle = f"{start[:4]}-{start[4:6]}-{start[6:]} ~ {end[:4]}-{end[4:6]}-{end[6:]}"
    return f"{match.group(1)} PR 周报", subtitle, rest.strip()


def _report_url_base() -> Optional[str]:
    base = os.getenv("REPORT_BASE_URL")
    if base:
        return base.rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY")
    if repository:
        server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
        ref = os.getenv("GITHUB_REF_NAME", "main")
        return f"{server}/{repository}/blob/{ref}"
    return None


def plan_pushes(files: List[str], repo_state: Optional[dict], cold_start: int) -> Tuple[List[str], List[str]]:
    """Return (to_push, to_skip) for one repo."""
    if repo_state is None:
        if cold_start < 0 or cold_start >= len(files):
            return files, []
        split = len(files) - cold_start
        return files[split:], files[:split]
    done = set(repo_state.get("pushed", [])) | set(repo_state.get("skipped", []))
    return [f for f in files if f not in done], []


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="repo-summary-push",
        description="Push generated reports to per-repository Feishu groups.",
    )
    parser.add_argument("--reports-dir", default="reports", help="Reports directory (default: reports)")
    parser.add_argument("--repos", default=None, help="Comma-separated owner/repo filter (default: all with webhooks)")
    parser.add_argument(
        "--cold-start",
        type=int,
        default=4,
        help="On the first push to a repo, send only the latest N historical reports "
        "(default: 4; -1 = all, 0 = none, just mark history as skipped).",
    )
    parser.add_argument("--max-parts", type=int, default=8, help="Max cards per report before truncating (default: 8)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be pushed without sending.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    webhooks = load_webhooks()
    if not webhooks:
        print("FEISHU_WEBHOOKS is not configured; nothing to push.")
        return 0

    state_path = os.path.join(args.reports_dir, STATE_FILENAME)
    state = load_state(state_path)
    reports = discover_reports(args.reports_dir)
    url_base = _report_url_base()

    selected = None
    if args.repos:
        selected = {r.strip().lower() for r in args.repos.split(",") if r.strip()}

    all_ok = True
    for repo, (url, secret) in webhooks.items():
        if selected is not None and repo not in selected:
            continue
        if repo not in reports:
            print(f"[{repo}] no reports found, skipping.")
            continue

        dir_name, files = reports[repo]
        repo_state = state.get(repo)
        to_push, to_skip = plan_pushes(files, repo_state, args.cold_start)
        mode = "cold start" if repo_state is None else "incremental"
        print(f"[{repo}] {mode}: {len(to_push)} to push, {len(to_skip)} skipped as history.")

        if args.dry_run:
            for name in to_push:
                print(f"  would push {name}")
            continue

        repo_state = repo_state or {"pushed": [], "skipped": []}
        repo_state["skipped"] = sorted(set(repo_state.get("skipped", [])) | set(to_skip))
        state[repo] = repo_state
        save_state(state_path, state)

        client = FeishuClient(url, secret)
        for name in to_push:
            path = os.path.join(args.reports_dir, dir_name, name)
            title, subtitle, body = _parse_report(path, repo)
            if not body.strip():
                print(f"  skipping {name}: report body is empty (regenerate it to push)", file=sys.stderr)
                continue
            button_url = f"{url_base}/{args.reports_dir}/{dir_name}/{name}" if url_base else None
            try:
                cards = client.send_report(title, body, subtitle=subtitle, button_url=button_url,
                                           max_parts=args.max_parts)
            except FeishuError as exc:
                print(f"  failed to push {name}: {exc}", file=sys.stderr)
                all_ok = False
                break
            print(f"  pushed {name} ({cards} card(s))")
            repo_state["pushed"] = sorted(set(repo_state["pushed"]) | {name})
            repo_state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_state(state_path, state)
            time.sleep(SEND_INTERVAL_SECONDS)

    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
