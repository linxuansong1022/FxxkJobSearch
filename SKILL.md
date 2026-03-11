# FxxkJobSearch — Operator Skill

> This skill describes how to deploy and operate the FxxkJobSearch automated job search pipeline.

## Overview

FxxkJobSearch is an automated pipeline that scrapes job listings from multiple Danish job platforms, filters for internship/student/part-time positions, analyzes them with Google Gemini, and sends daily reports via Telegram.

## Prerequisites

- Python 3.10+
- API Keys: Google Gemini, Tavily Search, Telegram Bot
- ~1GB RAM (for Playwright headless browser)

## Setup

### Docker (recommended for cloud agents)
```bash
git clone https://github.com/linxuansong1022/FxxkJobSearch.git
cd FxxkJobSearch
cp .env.example .env
# Fill in API keys in .env, then:
docker compose up --build
```

### Manual
```bash
# 1. Clone
git clone https://github.com/linxuansong1022/FxxkJobSearch.git
cd FxxkJobSearch

# 2. Install
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium

# 3. Configure .env
cp .env.example .env
# Fill in:
#   GOOGLE_CLOUD_API_KEY  — from https://aistudio.google.com/app/apikey
#   TAVILY_API_KEY        — from https://tavily.com/
#   TELEGRAM_BOT_TOKEN    — from @BotFather on Telegram
#   TELEGRAM_CHAT_ID      — your chat ID (use @userinfobot)
```

## Running

### Docker
```bash
docker compose run fxxkjobsearch run       # Full pipeline
docker compose run fxxkjobsearch scrape    # Scrape only
docker compose run fxxkjobsearch status    # Check stats
```

### Direct
### One-Shot (full pipeline)
```bash
source venv/bin/activate
python main.py run
```
This runs: scrape → filter → analyze → report (Telegram notification).

### Individual Steps
```bash
python main.py scrape    # Collect jobs from all sources
python main.py filter    # Filter irrelevant jobs (LLM-based)
python main.py analyze   # Score jobs with Gemini Pro
python main.py report    # Send Telegram daily report
python main.py status    # Show database statistics
python main.py list      # List all relevant jobs
```

### Automated Daily Run (cron)
```bash
crontab -e
# Add this line (runs at 9:00 AM daily):
0 9 * * * cd /path/to/FxxkJobSearch && source venv/bin/activate && python main.py run >> logs/daily.log 2>&1
```

## Pipeline Details

| Step | Duration | What it does |
|------|----------|-------------|
| `scrape` | ~30 min | Tavily (16 queries), Jobindex (11 queries), TheHub API, 65 company career sites via Playwright |
| `filter` | ~30 sec | Rule-based + Gemini Flash to keep only intern/student/part-time in Denmark |
| `analyze` | ~3 min | Ranks top 15 candidates, deep-analyzes with Gemini Pro, scores 0–1 |
| `report` | ~2 sec | Pushes matching jobs to Telegram with scores and apply links |

## Scoring Rules

- **Intern/Student/Part-time in Denmark** → normal scoring (0.0–1.0)
- **Full-time requiring 2+ years** → capped at 0.50
- **Full-time requiring 5+ years** → capped at 0.30
- **Non-Denmark (other countries)** → capped at 0.20

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `playwright` not found | `pip install playwright && playwright install --with-deps chromium` |
| Gemini API error | Check `GOOGLE_CLOUD_API_KEY` in `.env` |
| No Telegram notification | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` |
| 0 jobs scraped | Check `TAVILY_API_KEY`, ensure internet connectivity |
| All jobs filtered out | Check `profile.yaml` matches your target role |

## File Reference

| File | Purpose |
|------|---------|
| `main.py` | CLI entry point with `run` command |
| `config.py` | All configuration and env vars |
| `profile.yaml` | Your skills, education, experience |
| `.env` | API keys (not in git) |
| `src/scraper_tavily.py` | Tavily search with URL quality gates |
| `src/scraper_jobindex.py` | Jobindex.dk scraper |
| `src/scraper_careers.py` | Company career pages (Playwright + Gemini Flash) |
| `src/filter.py` | Two-layer job filtering |
| `src/jd_fetcher.py` | Async JD backfill |
| `src/analyzer.py` | Gemini Pro scoring |
| `src/notifier.py` | Telegram notifications |
| `src/database.py` | SQLite storage |
| `src/company_list.py` | 92 Danish tech companies |
| `jobs.db` | SQLite database (auto-created) |
