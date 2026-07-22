# LeadManager4U

A premium Django lead-generation platform that scrapes business data from Google Maps, Bing Maps, and major search engines, then manages email outreach campaigns with AI-powered lead intelligence.

## Features

### AI Dashboard (/)
- **Who to Connect Today** — AI-ranked list of leads to contact right now, scored by urgency + data completeness
- **Campaign Intelligence** — smart suggestions: send campaigns to fresh leads, re-engage follow-ups, etc.
- **Top Scored Leads** — highest AI-scored leads shown for quick action
- **Follow-Up Pipeline** — visual bars showing pipeline health (due today, following up, converted, stopped)
- **Auto-Mode** — configure auto-scrape (scheduled, recurring) and auto-campaign (send emails automatically after scrape)
- Stats: total leads, with email/phone, active jobs, campaigns, due today, following up, converted, starred, stopped, no email/phone/website

### Scraping
- **Google Maps** (`/google-maps/`) — Selenium-based, extracts name, phone, website, address, email
- **Bing Maps** (`/bing-maps/`) — Selenium-based, same data with different coverage
- **Search Engines** (`/search/`) — requests + BeautifulSoup for Google, Bing, Yahoo, DuckDuckGo, Yandex, Ecosia, Ask
- Up to **50,000 results** per job; pause/resume/stop controls on all jobs

### Auto-Mode (configured on Dashboard)
- **Auto-Scrape** — runs on a schedule (1h–168h interval), finds new leads automatically
- **Auto-Campaign** — auto-sends emails to new leads after scrape completes (configurable delay)
- Both are powered by the background scheduler thread in `scraper/views.py`

### Leads Management (`/leads/`)
- Filter by source, email/phone/website presence, job, lead status, starred, contacted recency
- Lead status pipeline: Fresh → Following Up → Converted / Stopped
- Star/prioritize individual leads (shown above all others)
- Follow-up date + note per lead (calendar picker + notes)
- Converted/stopped leads are automatically skipped in email campaigns
- Quick reach-out icons per row: Email, Gmail, WhatsApp, Telegram, Call
- Bulk selection → Email Campaign / Gmail All / WhatsApp All / Telegram All
- Export filtered: All CSV, Emails CSV, Phones CSV, Websites CSV
- Import leads via CSV/Excel

### Email Campaigns (`/campaigns/`)
- Personalized body with `{name}`, `{email}`, `{phone}`, `{website}`, `{location}` placeholders
- SMTP profile management (save and reuse credentials, supports Gmail)
- Send now, schedule for later, stop, resend failed
- Campaign progress tracking with real-time updates
- Auto-skip converted/stopped leads

### AI Features
- **AI Email Template Generator** — detects industry, generates 3 subject + body templates
- **Smart Tips** — contextual advice on dashboard based on lead stats
- **Lead Scoring** — scores 0–100 based on data completeness + recency
- Industry detection for 20+ categories (dental, legal, medical, restaurant, etc.)

### Authentication & Outreach Lifecycle
- The application requires Django authentication; unauthenticated visitors are sent to `/login/`.
- The initial administrator account is `SA` with the name Shahid Ahmed. Use Django admin to manage accounts.
- Successful campaign emails and manual Gmail, WhatsApp, Telegram, and call contacts move a lead to Following Up.
- Email follow-ups are scheduled automatically for the next day, then 7 days, then 14 days based on prior campaign email contacts.
- The dashboard notification bell includes overdue, due-today, 1-day, 7-day, and 14-day outreach reminders.
- The brand is displayed as `LeadManager4U.ai`.

## Architecture

- **Backend**: Django 6.x, SQLite (WAL mode)
- **Scrapers**: `scraper/scraper.py` (Google Maps), `scraper/bing_maps_scraper.py`, `scraper/search_scraper.py`
- **AI**: `scraper/ai_engine.py` — rule-based, no external API required
- **Email**: `scraper/email_sender.py` — smtplib with TLS/SSL
- **Models**: `scraper/models.py` — BusinessListing (with lead_status, is_starred, follow_up_date, follow_up_note), ScrapeJob, EmailCampaign, SmtpProfile, ContactAttempt, AutoConfig
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

## Key URLs

| Path | Description |
|------|-------------|
| `/` | AI Dashboard (intelligence hub) |
| `/google-maps/` | Google Maps scraper |
| `/bing-maps/` | Bing Maps scraper |
| `/search/` | Search engine scraper |
| `/leads/` | Lead management |
| `/campaigns/` | Email campaigns |
| `/smtp/` | SMTP profiles |
| `/auto-config/save/` | Save auto-mode settings (POST) |
| `/api/ai/templates/?q=dentist` | AI template generator |
| `/api/ai/scores/` | Lead scoring API |

## User Preferences

- Keep existing structure and stack (Django + SQLite)
- Dashboard = AI intelligence hub only (no scraping form on dashboard)
- Google Maps scraper on its own page `/google-maps/`
- Max results up to 50,000 (no arbitrary caps)
- Auto-mode: auto-scrape + auto-campaign configured from dashboard
