"""Convenience entry point: `python main.py [owner] [repo] [options]`."""

from repo_summary.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
