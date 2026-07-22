# LeadManager4U

A Django 5.1 web app that scrapes Google Maps for business listings (name, phone, email, website), manages scrape jobs, and exports results as CSV.

## Stack

- **Backend:** Django 5.1 (Python 3.12)
- **Database:** SQLite (`db.sqlite3`)
- **Scraping:** Selenium + ChromeDriver (headless Chromium), BeautifulSoup4
- **Exports:** pandas (CSV download)

## How to Run

The app runs on port 5000 via the "Start application" workflow:

```
python manage.py runserver 0.0.0.0:5000
```

## Project Structure

```
maps_scraper/       Django project config (settings, urls, wsgi)
scraper/            Main app
  models.py         BusinessListing, ScrapeJob, JobLog
  views.py          All views + background scrape runner
  scraper.py        Selenium scraping engine (Google Maps)
  domains.py        Supported Google domain list
  templates/        home.html, job.html, result.html, base.html
  static/           styles.css
manage.py
db.sqlite3          SQLite database (existing data)
```

## Key Features

- Search Google Maps by keyword + location(s)
- Pause / resume / stop scrape jobs
- Email extraction from business websites
- Download results as CSV (full, or by field: phone, email, website)
- Per-job and global results views

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django secret key | insecure dev key |
| `SCRAPER_WORKERS` | Selenium detail workers | 5 |
| `SCRAPER_EMAIL_WORKERS` | Email fetch workers | 6 |
| `SCRAPER_MAX_WORKERS` | Max Selenium workers | 8 |
| `SCRAPER_MAX_EMAIL_WORKERS` | Max email workers | 14 |

## User Preferences

- Keep existing Django/SQLite stack — do not migrate to another DB unless explicitly asked.
- The uploaded spec file (`attached_assets/Pasted--MULTI-SEARCH-ENGINE-SUPPORT-...txt`) describes a planned multi-search-engine extension; implement only when explicitly requested.
