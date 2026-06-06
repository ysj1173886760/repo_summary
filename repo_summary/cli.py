"""Command-line entry point for the repo summary tool."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional at runtime
    pass

from . import __version__
from .formatting import pulls_to_prompt_input
from .github_client import fetch_recent_pulls
from .summarizer import summarize

# Default set of repositories in the PyTorch ecosystem.
DEFAULT_REPOS = [
    "pytorch/pytorch",
    "pytorch/vision",
    "pytorch/audio",
    "pytorch/ao",
    "pytorch/executorch",
]


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="repo-summary",
        description="Fetch recent GitHub pull requests and generate an AI digest.",
    )
    parser.add_argument("owner", nargs="?", default=None, help="Repository owner (default: pytorch)")
    parser.add_argument("repo", nargs="?", default=None, help="Repository name (default: pytorch)")
    parser.add_argument(
        "--repos",
        default=None,
        help="Comma-separated list of owner/repo to summarize (overrides positional args). "
        "Default when no positional args given: the PyTorch ecosystem.",
    )
    parser.add_argument("--days", type=int, default=7, help="Look back this many days (default: 7)")
    parser.add_argument(
        "--end",
        default=None,
        help="End of window as YYYY-MM-DD (default: now, UTC). Useful for backfilling.",
    )
    parser.add_argument("--model", default=None, help="Model name (overrides OPENAI_MODEL)")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible base URL (overrides OPENAI_BASE_URL)")
    parser.add_argument("--temperature", type=float, default=0.3, help="Sampling temperature (default: 0.3)")
    parser.add_argument("--body-max-len", type=int, default=500, help="Max chars kept per PR body (default: 500)")
    parser.add_argument("--output-dir", default="reports", help="Base directory for reports (default: reports)")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI summarization, just dump the raw PR list.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _resolve_window(args: argparse.Namespace):
    if args.end:
        try:
            end_time = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Invalid --end date '{args.end}', expected YYYY-MM-DD.", file=sys.stderr)
            sys.exit(2)
    else:
        end_time = datetime.now(timezone.utc)
    since_time = end_time - timedelta(days=args.days)
    return since_time, end_time


def _resolve_targets(args: argparse.Namespace):
    """Return a list of (owner, repo) tuples from CLI args."""
    if args.repos:
        targets = []
        for item in args.repos.split(","):
            item = item.strip()
            if not item:
                continue
            if "/" not in item:
                print(f"Skipping invalid repo '{item}', expected 'owner/repo'.", file=sys.stderr)
                continue
            owner, repo = item.split("/", 1)
            targets.append((owner.strip(), repo.strip()))
        return targets
    if args.owner and args.repo:
        return [(args.owner, args.repo)]
    if args.owner and not args.repo:
        print("Both owner and repo are required when using positional args.", file=sys.stderr)
        sys.exit(2)
    # No positional args and no --repos: default to the PyTorch ecosystem.
    return [tuple(r.split("/", 1)) for r in DEFAULT_REPOS]


def _process_one(owner: str, repo: str, args, since_time, end_time) -> bool:
    print("=" * 40)
    print(f"{owner}/{repo}")
    print("=" * 40)

    pulls = fetch_recent_pulls(owner, repo, since_time=since_time, end_time=end_time)
    if not pulls:
        print(f"No recent pull requests found for {owner}/{repo}.")
        return True

    print(f"Number of recent pulls: {len(pulls)}")
    prompt_input = pulls_to_prompt_input(pulls, body_max_len=args.body_max_len)

    if args.no_ai:
        report_body = prompt_input
    else:
        try:
            report_body = summarize(
                prompt_input,
                owner,
                repo,
                model=args.model,
                base_url=args.base_url,
                temperature=args.temperature,
            )
        except Exception as exc:  # noqa: BLE001 - surface a clean message to the user
            print(f"AI summarization failed for {owner}/{repo}: {exc}", file=sys.stderr)
            return False
        print("\nAI Analysis of Recent Pull Requests:\n")
        print(report_body)

    report_path = _write_report(args.output_dir, owner, repo, since_time, end_time, report_body)
    print(f"\nReport saved to {report_path}")
    return True


def main(argv=None) -> int:
    args = _parse_args(argv)
    targets = _resolve_targets(args)
    since_time, end_time = _resolve_window(args)

    if not targets:
        print("No valid repositories to summarize.", file=sys.stderr)
        return 2

    print(f"Summarizing {len(targets)} repo(s): {', '.join(f'{o}/{r}' for o, r in targets)}")

    all_ok = True
    for owner, repo in targets:
        ok = _process_one(owner, repo, args, since_time, end_time)
        all_ok = all_ok and ok

    return 0 if all_ok else 1


def _write_report(output_dir, owner, repo, since_time, end_time, body) -> str:
    report_dir = os.path.join(output_dir, f"{owner}_{repo}")
    os.makedirs(report_dir, exist_ok=True)
    date_str = end_time.strftime("%Y%m%d")
    start_str = since_time.strftime("%Y%m%d")
    report_path = os.path.join(report_dir, f"report-{date_str}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# Pull Request Digest for {owner}/{repo} ({start_str}-{date_str})\n\n")
        f.write(body)
        f.write("\n")
    return report_path


if __name__ == "__main__":
    raise SystemExit(main())
