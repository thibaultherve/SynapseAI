# NeuroAI

Research paper assistant for the a neuroscience research institute. Powered by [Claude Code](https://claude.ai/claude-code) skills and Python scripts.

NeuroAI monitors a Notion database of scientific papers, automatically downloads and extracts content, generates expert-level summaries, cross-references findings across the literature, and answers researcher questions — all autonomously.

## How it works

The pipeline is built as a set of Claude Code skills that orchestrate Python scripts:

```
Notion DB (new paper added)
    |
    v
neuroai-poll        detect new papers, download PDFs/web content
    |
    v
neuroai-process     read paper, generate summaries & key findings, publish to Notion
    |
    v
neuroai-crossref    cross-reference against existing papers, update synthesis & gap analysis
    |
    v
neuroai-comments    monitor @NeuroAI mentions, answer research questions
    |
    v
neuroai-verify      daily maintenance, sync local state with Notion
```

Each skill can run on a schedule or be triggered manually (e.g. `neuroai poll`, `neuroai process`).

## Setup

### Prerequisites

- Python 3.12+
- [Claude Code](https://claude.ai/claude-code)
- A Notion integration with access to your papers database

### Install dependencies

```bash
pip install requests python-dotenv pdfplumber trafilatura
```

### Configure environment

```bash
cp .env.example .env
```

Fill in your Notion credentials in `.env`:

| Variable | Description |
|---|---|
| `NOTION_API_KEY` | Notion integration token |
| `NOTION_DATABASE_ID` | Papers database ID |
| `NOTION_LOGS_DB_ID` | Logs database ID |
| `NOTION_BOT_USER_ID` | Bot's Notion user ID |
| `NOTION_SYNTHESIS_PAGE_ID` | Literature synthesis page ID |
| `NOTION_GAP_ANALYSIS_PAGE_ID` | Gap analysis page ID |
| `NOTION_CONCORDANCE_PAGE_ID` | Concordance tracker page ID |

## Project structure

```
.claude/skills/          Claude Code skill definitions
    neuroai-poll/        Detect & download new papers
    neuroai-process/     Summarize & publish to Notion
    neuroai-crossref/    Cross-reference & literature synthesis
    neuroai-comments/    Answer @NeuroAI questions
    neuroai-verify/      Daily maintenance & sync

scripts/                 Python scripts called by skills
    notion_helper.py     Notion API wrapper (auth, blocks, comments)
    notion_pages.py      CLI for Notion page operations
    poll_papers.py       Query Notion for new papers
    poll_comments.py     Poll for new comments
    acquire_paper.py     Download PDF/web content & extract text
    process_paper.py     Init/publish pipeline for paper processing

data/                    Local state (gitignored except .gitkeep)
    papers/              Per-paper directories (metadata, summaries, PDFs)
    synthesis/           Literature synthesis markdown files
    index.json           Master paper index
    state.json           Pipeline queues & comment tracking
    analysis.json        Gap analysis & concordances
    logs.json            Run history
```

## Scripts

All scripts load credentials from `.env` via `notion_helper.py`. No manual secret handling needed.

```bash
# Poll Notion for new papers
python scripts/poll_papers.py

# Download a paper
python scripts/acquire_paper.py <page_id> <url>

# Initialize / publish paper processing
python scripts/process_paper.py init
python scripts/process_paper.py publish <page_id>

# Poll for new comments
python scripts/poll_comments.py

# Notion page operations (used by skills)
python scripts/notion_pages.py env                          # print all page IDs
python scripts/notion_pages.py comment <page_id> <text>     # post a comment
python scripts/notion_pages.py append <page_id> <md_file>   # append markdown as blocks
python scripts/notion_pages.py blocks <page_id>             # read page blocks
```
