# LeadManager4U

A premium, full-featured lead generation platform that scrapes business data from Google Maps and multiple search engines, then enables email outreach campaigns.

## Stack

- **Backend:** Django 5.1 (Python 3.12)
- **Database:** SQLite (WAL mode for concurrency)
- **Scraping:** Selenium + Chrome (Google Maps), requests + BeautifulSoup (search engines)
- **Email:** SMTP (smtplib) — configurable per campaign
- **Frontend:** Vanilla JS, Inter font, dark-sidebar SaaS design

## Running

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 0.0.0.0:5000
```

## Features

### Scraping
- **Google Maps** — Selenium-based scraper with concurrent drivers, pause/resume/stop, duplicate detection, WAL-mode SQLite writes
- **Search Engines** — Modular scraper supporting Google, Bing, Yahoo, DuckDuckGo, Yandex via requests+BeautifulSoup. Visits each result's website to find emails and phone numbers.

### Lead Management (`/leads/`)
- Filter by source, email, phone, job
- Full-text search
- CSV exports (all / emails / phones / websites)

### Email Campaigns (`/campaigns/`)
- Configure SMTP per campaign (Gmail App Passwords supported)
- Body personalisation: `{name}`, `{email}`, `{phone}`, `{website}`, `{location}`
- Per-send status tracking (sent / failed / skipped)
- Filter recipients by job or use all leads

### Live Updates
- Job detail pages poll `/api/jobs/<id>/` every 6s
- Dashboard polls `/api/jobs/recent/` every 12s
- Progress bars, log console, live results table

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SESSION_SECRET` | (insecure default) | Django SECRET_KEY |
| `DEBUG` | `True` | Django debug mode |
| `SMTP_HOST` | `smtp.gmail.com` | Default SMTP host |
| `SMTP_PORT` | `587` | Default SMTP port |
| `SMTP_USER` | — | Default SMTP user |
| `SMTP_PASS` | — | Default SMTP password |

## Project Structure

```
maps_scraper/      Django project (settings, urls, wsgi)
scraper/
  models.py        ScrapeJob, BusinessListing, JobLog, EmailCampaign, EmailSend
  views.py         All request handlers
  scraper.py       Google Maps Selenium scraper
  search_scraper.py  Multi-engine search scraper (requests)
  email_sender.py  SMTP campaign sender
  domains.py       Google domain list
  templates/       All HTML templates
  static/          styles.css (Inter, dark sidebar design)
  migrations/      Django migrations
```

## User Preferences

- Email-only outreach (no WhatsApp/SMS)
- Keep existing Google Maps scraper working
- Dark sidebar SaaS dashboard design
- No destructive restructuring of original scraper logic
