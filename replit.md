# LeadManager4U

A premium Django lead-generation platform that scrapes business data from Google Maps, Bing Maps, and major search engines, then manages email outreach campaigns.

## Features

### Scraping
- **Google Maps** — Selenium-based, extracts name, phone, website, address, email
- **Bing Maps** — Selenium-based (new), same data as Google Maps with different coverage
- **Search Engines** — requests + BeautifulSoup scraper for Google, Bing, Yahoo, DuckDuckGo, Yandex, Ecosia, Ask
- Up to **50,000 results** per job
- Anti-block: rotating user-agents, session resets, exponential backoff, auto-recovery from CAPTCHAs
- Parallel enrichment: visits business websites to extract emails and phones
- Pause/resume/stop controls on all jobs

### Leads Management
- Filter by source, email/phone presence, job, free-text search
- Export: full CSV, emails-only, phones-only, websites-only
- Per-job export

### Email Campaigns
- Personalized body with `{name}`, `{email}`, `{phone}`, `{website}`, `{location}` placeholders
- SMTP profile management (save and reuse credentials)
- Send now, schedule for later, stop, resend failed
- Campaign progress tracking

### AI Features
- **AI Email Template Generator** — detects industry from search phrase, generates 3 subject + body templates
- **Smart Tips** — contextual advice on dashboard based on lead stats
- **Lead Scoring** API — scores 0–100 based on data completeness
- Industry detection for: dental, legal, medical, restaurant, plumbing, construction, marketing, accounting, salon, fitness, tech, and more

## Architecture

- **Backend**: Django 6.x, SQLite (WAL mode)
- **Scrapers**: `scraper/scraper.py` (Google Maps), `scraper/bing_maps_scraper.py` (Bing Maps), `scraper/search_scraper.py` (search engines)
- **AI**: `scraper/ai_engine.py` — rule-based, no external API required
- **Email**: `scraper/email_sender.py` — smtplib with TLS/SSL
- **Frontend**: Django templates, vanilla JS, custom CSS (Inter font, dark sidebar)

## Run Command

```
python manage.py runserver 0.0.0.0:5000
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SESSION_SECRET` | Django SECRET_KEY | insecure fallback |
| `SMTP_HOST` | Default SMTP host | smtp.gmail.com |
| `SMTP_PORT` | Default SMTP port | 587 |
| `SMTP_USER` | Default SMTP user | (empty) |
| `SMTP_PASS` | Default SMTP password | (empty) |
| `SCRAPER_WORKERS` | Google Maps detail workers | 5 |
| `SCRAPER_EMAIL_WORKERS` | Email extraction workers | 6 |

## Key URLs

| Path | Description |
|------|-------------|
| `/` | Dashboard + Google Maps scraper |
| `/bing-maps/` | Bing Maps scraper |
| `/search/` | Search engine scraper |
| `/leads/` | Lead management |
| `/campaigns/` | Email campaigns |
| `/smtp/` | SMTP profiles |
| `/api/ai/templates/?q=dentist` | AI template generator |
| `/api/ai/scores/` | Lead scoring API |

## User Preferences

- Keep existing structure and stack (Django + SQLite)
- Max results up to 50,000 (no arbitrary caps)
- Anti-block must auto-recover without stopping jobs
