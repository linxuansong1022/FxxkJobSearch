# FxxkJobSearch 🔍

An automated job search pipeline that scrapes, filters, analyzes, and pushes relevant **internship / student / part-time** positions in Denmark directly to your Telegram.

## How It Works

```
Scrape → Filter → JD Backfill → Analyze → Notify
```

1. **Scrape** — Collects job listings from multiple sources:
   - [Tavily Search API](https://tavily.com/) (LinkedIn, Indeed, Glassdoor, Jobindex)
   - [Jobindex.dk](https://www.jobindex.dk/) direct scraping
   - [TheHub.io](https://thehub.io/) Danish startup API
   - 65 company career pages via Playwright + Gemini Flash

2. **Filter** — Two-layer filtering:
   - Rule-based: excludes non-tech titles (HR, Sales, Marketing…) and senior roles
   - LLM (Gemini Flash): classifies by job type (intern/student/full-time) and location (Denmark only)

3. **JD Backfill** — Fetches full job descriptions for listings that only have titles:
   - Direct HTTP for most sites
   - Playwright fallback for JS-heavy platforms (LinkedIn, Indeed, Glassdoor)

4. **Analyze** — Gemini Pro scores each JD (0–1) based on:
   - Job type: intern/student → normal score; full-time 2+ yrs → capped at 0.50
   - Location: Denmark → normal; non-Denmark → capped at 0.20
   - Skill match against candidate profile (`profile.yaml`)

5. **Notify** — Sends a daily Telegram report with top matching jobs, including match reason, key skills, and direct apply links.

## Quick Start

```bash
# Setup
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Configure
cp .env.example .env
# Edit .env with your API keys (Tavily, Google Cloud, Telegram)
# Edit profile.yaml with your skills and preferences

# Run full pipeline (scrape → filter → analyze → notify)
python main.py run

# Or run steps individually
python main.py scrape    # Collect jobs
python main.py filter    # Filter irrelevant ones
python main.py analyze   # Score with Gemini Pro
python main.py report    # Send Telegram notification
python main.py status    # Check database stats

# Import your resume to generate profile.yaml
python main.py import-resume path/to/resume.pdf
```

## Server Deployment

### Option 1: Docker (recommended for cloud / OpenClaw)

```bash
git clone https://github.com/linxuansong1022/FxxkJobSearch.git
cd FxxkJobSearch

# Configure
cp .env.example .env   # Fill in API keys
vim profile.yaml       # Your skills and preferences

# Build & Run (one-shot full pipeline)
docker compose up --build

# Or run individual steps
docker compose run fxxkjobsearch scrape
docker compose run fxxkjobsearch filter
docker compose run fxxkjobsearch analyze
docker compose run fxxkjobsearch report
docker compose run fxxkjobsearch status
```

The SQLite database persists in `./data/jobs.db` via Docker volume.

For daily scheduled runs (cron on host):
```bash
crontab -e
# Add: 0 9 * * * cd /path/to/FxxkJobSearch && docker compose run --rm fxxkjobsearch run >> data/daily.log 2>&1
```

### Option 2: Direct (Ubuntu/Debian)

```bash
# On your server
git clone https://github.com/linxuansong1022/FxxkJobSearch.git
cd FxxkJobSearch
chmod +x deploy.sh && ./deploy.sh

# Edit .env and profile.yaml, then set up daily cron:
crontab -e
# Add: 0 9 * * * cd /path/to/FxxkJobSearch && source venv/bin/activate && python main.py run >> logs/daily.log 2>&1
```

Minimum requirements: 1 CPU, 1GB RAM, 1GB disk.

### Low-Memory Server (\u2264 512MB RAM)

Set `LIGHTWEIGHT_MODE=true` in `.env` to skip Playwright/Chromium:

```bash
# In .env
LIGHTWEIGHT_MODE=true
```

This disables company career page scraping and Playwright JD backfill, reducing memory to ~100-200MB. Tavily + Jobindex + TheHub still cover most job listings.

## Candidate Profile (`profile.yaml`)

The pipeline uses `profile.yaml` to personalize job scoring. You can either:

### Option A: Import from resume PDF (recommended)
```bash
python main.py import-resume path/to/your-resume.pdf
```
Gemini will parse your resume and generate a structured `profile.yaml` automatically. You can then manually edit it to fix or add details.

### Option B: Write manually
Create `profile.yaml` with this structure:

```yaml
personal:
  name: "Your Name"
  email: "you@example.com"
  linkedin: "https://linkedin.com/in/you"

education:
  - school: "Your University"
    degree: "MSc in Computer Science"
    dates: "2024 -- 2026"
    bullets: []

experiences:
  - company: "Company A"
    role: "Software Intern"
    dates: "Jun. -- Aug. 2024"
    bullets:
      - "Built X using Y, improved Z by N%"

skills:
  languages: "Python, Java, SQL"
  frameworks: "FastAPI, PyTorch"
  tools: "Git, Docker"
```

- **Scoring**: `analyzer.py` reads your profile to match skills against JD requirements
- **Resume generation**: `builder.py` selects the most relevant bullets via embedding similarity and renders a tailored LaTeX resume

## Configuration

| Env Variable | Purpose |
|---|---|
| `TAVILY_API_KEY` | Tavily Search API |
| `GCP_PROJECT_ID` | Google Cloud project for Gemini |
| `GOOGLE_CLOUD_API_KEY` | Gemini API key (or use ADC) |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

## Project Structure

```
main.py              # CLI entry point
config.py            # All configuration
profile.yaml         # Candidate profile (skills, preferences)
src/
  scraper_tavily.py  # Tavily search with URL/title quality gates
  scraper_jobindex.py# Jobindex.dk scraper
  scraper_careers.py # Company career page scraper (Playwright + LLM)
  filter.py          # Rule-based + LLM filtering
  jd_fetcher.py      # Async JD backfill with Playwright fallback
  analyzer.py        # Gemini Pro JD analysis & scoring
  notifier.py        # Telegram daily report
  database.py        # SQLite storage
  company_list.py    # 92 Danish tech companies
tests/               # 164 tests (pytest)
```

## Tech Stack

- **Python 3.13** + asyncio
- **Google Gemini** (Pro for analysis, Flash for filtering)
- **Playwright** for JS-rendered career pages
- **SQLite** for job storage
- **Telegram Bot API** for notifications

## AI Agent Integration

See [SKILL.md](SKILL.md) for a complete operator skill file that AI agents (OpenClaw, Claude, etc.) can use to automatically deploy and run this pipeline.
