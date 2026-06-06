# repo-summary

A small CLI tool that fetches recent **pull requests** from a GitHub repository
and turns them into a high-signal technical digest using any
**OpenAI-compatible** chat model.

## Features

- Fetches all PRs created within a configurable time window (default: last 7 days).
- Stops paginating early once it passes the window (saves API calls).
- Cleans PR descriptions (strips HTML comments, boilerplate test/checklist tails).
- Summarizes with any OpenAI-compatible endpoint via a configurable `base_url`
  (OpenAI, Azure OpenAI, DeepSeek, OpenRouter, local vLLM/Ollama, Gemini's
  OpenAI-compatible API, etc.).
- Writes a Markdown report to `reports/<owner>_<repo>/report-YYYYMMDD.md`.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable          | Purpose                                                                 |
| ----------------- | ----------------------------------------------------------------------- |
| `GH_TOKEN`        | GitHub token (optional, but avoids low rate limits).                    |
| `OPENAI_API_KEY`  | API key for your OpenAI-compatible provider.                            |
| `OPENAI_BASE_URL` | Endpoint base URL. Leave empty for official OpenAI.                     |
| `OPENAI_MODEL`    | Default model name (e.g. `gpt-4o-mini`).                                |

### Examples of OpenAI-compatible endpoints

```bash
# OpenRouter (default in .env.example)
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_MODEL=deepseek/deepseek-v4-pro

# Official OpenAI (leave OPENAI_BASE_URL empty)
OPENAI_MODEL=gpt-4o-mini

# DeepSeek (direct)
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

# Gemini (OpenAI-compatible endpoint)
OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OPENAI_MODEL=gemini-2.0-flash
```

## Usage

```bash
# Default (no args): the whole PyTorch ecosystem, last 7 days
python main.py

# A single repo
python main.py vllm-project vllm

# Multiple repos at once
python main.py --repos "pytorch/pytorch,pytorch/vision,pytorch/audio"

# Look back 14 days
python main.py pytorch pytorch --days 14

# Backfill a past window (ends on a specific date, UTC)
python main.py pytorch pytorch --end 2026-03-01 --days 7

# Override model / endpoint per run
python main.py pytorch pytorch --model gpt-4o --base-url https://api.openai.com/v1

# Skip AI and just dump the cleaned PR list
python main.py pytorch pytorch --no-ai
```

When run with **no positional args and no `--repos`**, it defaults to the
PyTorch ecosystem (`pytorch/pytorch`, `pytorch/vision`, `pytorch/audio`,
`pytorch/ao`, `pytorch/executorch`). Edit `DEFAULT_REPOS` in
`repo_summary/cli.py` to change the default set.

You can also run it as a module:

```bash
python -m repo_summary.cli pytorch pytorch
```

## CLI options

| Flag             | Default     | Description                                  |
| ---------------- | ----------- | -------------------------------------------- |
| `owner` / `repo` | `pytorch`   | Target repository.                           |
| `--days`         | `7`         | Look-back window length in days.             |
| `--end`          | now (UTC)   | End of window as `YYYY-MM-DD`.               |
| `--model`        | env / `gpt-4o-mini` | Model name.                          |
| `--base-url`     | env         | OpenAI-compatible base URL.                  |
| `--temperature`  | `0.3`       | Sampling temperature.                        |
| `--body-max-len` | `500`       | Max chars kept per PR body.                  |
| `--output-dir`   | `reports`   | Where reports are written.                   |
| `--no-ai`        | off         | Skip summarization; output the raw PR list.  |

## Deploy on GitHub Actions

A workflow at `.github/workflows/repo-summary.yml` runs the tool automatically,
then commits the generated reports back to the repo.

- **Schedule:** every Monday 01:00 UTC (~09:00 Beijing time).
- **Manual run:** Actions tab → *Repo Summary* → *Run workflow* (optionally pass
  a custom `repos` list and `days`).

### One-time setup

1. Push this repository to GitHub.
2. Add your OpenRouter key as a repository secret named **`OPENROUTER_API_KEY`**:
   - Repo → *Settings* → *Secrets and variables* → *Actions* → *New repository secret*
   - Name: `OPENROUTER_API_KEY`, Value: your `sk-or-v1-...` key.
3. Make sure Actions can push commits:
   - Repo → *Settings* → *Actions* → *General* → *Workflow permissions* →
     **Read and write permissions**.

The workflow uses the built-in `GITHUB_TOKEN` for GitHub API rate limits, and
`OPENROUTER_API_KEY` for summarization. Model/endpoint are set in the workflow
env (`deepseek/deepseek-v4-pro` via OpenRouter); edit the workflow to change
them. The default repo set is the PyTorch ecosystem.

> Note: `.env` is git-ignored and only used for local runs. Never commit your
> real API key — use the GitHub secret instead.

## Project layout

```
repo_summary/
  github_client.py   # GitHub PR fetching
  formatting.py      # body cleanup + prompt-input rendering
  summarizer.py      # OpenAI-compatible summarization
  cli.py             # argparse entry point
main.py              # convenience launcher
```
