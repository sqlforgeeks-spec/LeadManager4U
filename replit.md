# LeadManager4U.ai

A Django lead-generation platform that scrapes Google Maps, Bing Maps, and major search engines, then manages email outreach campaigns with AI-powered lead scoring.

## Run Command

```bash
python manage.py runserver 0.0.0.0:5000
```

## Default Login

- **Username:** `SA`
- **Password:** *(set via Django admin — create via `python manage.py createsuperuser` if not set)*

## Stack

- **Backend:** Django 6.x, SQLite (WAL mode)
- **Scrapers:** Selenium (Google/Bing Maps), requests + BeautifulSoup (search engines)
- **Email:** smtplib with TLS/SSL, SMTP rotation
- **AI:** Rule-based engine — no external API required
- **Frontend:** Django templates, vanilla JS, custom CSS

## Key URLs

| Path | Description |
|------|-------------|
| `/` | AI Dashboard |
| `/google-maps/` | Google Maps scraper |
| `/bing-maps/` | Bing Maps scraper |
| `/search/` | Search engine scraper |
| `/leads/` | Lead management |
| `/campaigns/` | Email campaigns |
| `/smtp/` | SMTP profiles |
| `/admin/` | Django admin |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SESSION_SECRET` | Django SECRET_KEY | insecure fallback |
| `SMTP_HOST` | Default SMTP host | smtp.gmail.com |
| `SMTP_PORT` | Default SMTP port | 587 |
| `SMTP_USER` | Default SMTP username | (empty) |
| `SMTP_PASS` | Default SMTP password | (empty) |

## Migrations

```bash
python manage.py migrate
```

## Notes

- Selenium scrapers (Google/Bing Maps) require Chrome/ChromeDriver. On Replit, the search engine scraper (requests-based) is more reliable.
- The background scheduler thread for auto-scrape/auto-campaign runs inside the Django process.
- SQLite is in WAL mode for concurrent write safety.

## User Preferences

- Keep existing project structure and stack.
