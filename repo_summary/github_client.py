"""GitHub pull-request fetching."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests

GITHUB_API = "https://api.github.com"


@dataclass
class PullRequest:
    """A trimmed-down representation of a GitHub pull request."""

    number: int
    title: str
    state: str
    created_at: str
    updated_at: str
    user: str
    url: str
    labels: List[str] = field(default_factory=list)
    body: str = ""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_recent_pulls(
    owner: str = "pytorch",
    repo: str = "pytorch",
    since_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    token: Optional[str] = None,
    per_page: int = 50,
    max_retries: int = 5,
    base_delay: float = 8.0,
) -> List[PullRequest]:
    """Fetch pull requests created within ``[since_time, end_time]``.

    Results are sorted by creation time (newest first). Because the GitHub API
    returns PRs in that order, we can stop paginating as soon as we hit one
    created before ``since_time``.
    """

    if token is None:
        token = os.getenv("GH_TOKEN")
    if token is None:
        print("Warning: GH_TOKEN is not set. You may hit GitHub rate limits.")

    if end_time is None:
        end_time = datetime.now(timezone.utc)
    if since_time is None:
        since_time = end_time - timedelta(weeks=1)

    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    params = {
        "state": "all",
        "sort": "created",
        "direction": "desc",
        "per_page": per_page,
        "page": 1,
    }

    collected: List[PullRequest] = []
    retries = max_retries

    while True:
        print(f"Fetching page {params['page']}...")
        try:
            response = requests.get(url, params=params, headers=headers, timeout=30)
        except requests.exceptions.RequestException as exc:
            print(f"Request error: {exc}")
            retries -= 1
            if retries <= 0:
                print("Max retries reached. Stopping.")
                break
            time.sleep(5)
            continue

        if response.status_code != 200:
            print(f"Request failed (status {response.status_code}): {response.text[:200]}")
            retries -= 1
            if retries <= 0:
                print("Max retries reached. Stopping.")
                break
            print(f"Retrying... ({retries} attempts left)")
            time.sleep(5)
            continue

        retries = max_retries
        pulls = response.json()
        if not pulls:
            print("No more data.")
            break

        print(f"Fetched {len(pulls)} pull requests on page {params['page']}")

        found_older = False
        page_hits = 0
        for pull in pulls:
            created_at = datetime.fromisoformat(pull["created_at"].replace("Z", "+00:00"))
            if created_at < since_time:
                found_older = True
                break
            if created_at > end_time:
                continue
            collected.append(
                PullRequest(
                    number=pull["number"],
                    title=pull["title"],
                    state=pull["state"],
                    created_at=pull["created_at"],
                    updated_at=pull["updated_at"],
                    user=pull["user"]["login"],
                    url=pull["html_url"],
                    labels=[label["name"] for label in pull.get("labels", [])],
                    body=pull.get("body") or "",
                )
            )
            page_hits += 1

        print(f"  -> {page_hits} pull requests within window (total {len(collected)})")

        if found_older:
            break

        params["page"] += 1
        time.sleep(base_delay + len(collected) / 100.0)

    return collected
