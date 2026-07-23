from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.db import close_old_connections, connection
from django.db.utils import OperationalError
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate, login, logout

from .models import (
    BusinessListing, ScrapeJob, JobLog, EmailCampaign, EmailSend,
    SmtpProfile, ContactAttempt, AutoConfig
)
from .scraper import scrape_google_maps, StopScrape, create_shared_drivers
from .search_scraper import scrape_search_engine, StopScrape as SearchStopScrape
from .bing_maps_scraper import scrape_bing_maps, StopScrape as BingStopScrape
from .email_sender import launch_campaign
from .ai_engine import generate_email_templates, generate_smart_tips, score_lead, score_lead_label, detect_industry
from .domains import DOMAINS
import threading
import queue
import time
import io
import pandas as pd

MAX_RESULTS_CAP = 50000
ACTIVE_JOBS = set()
ACTIVE_JOBS_LOCK = threading.Lock()
ACTIVE_CAMPAIGNS = set()
ACTIVE_CAMPAIGNS_LOCK = threading.Lock()

# ─── Scheduler thread (for scheduled campaigns) ───────────────────────────────
_scheduler_started = False
_scheduler_lock = threading.Lock()


def login_view(request):
    if request.user.is_authenticated:
        return redirect(request.GET.get("next") or "home")
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        user = authenticate(request, username=username, password=request.POST.get("password", ""))
        if user is not None:
            login(request, user)
            next_url = request.POST.get("next") or request.GET.get("next") or reverse("home")
            return redirect(next_url)
    return render(request, "login.html", {"next": request.GET.get("next", "")})


def logout_view(request):
    logout(request)
    return redirect("login")


def _mark_contacted_for_followup(listing, interval_days=1):
    """Move a contacted lead into the pipeline and schedule its next touch."""
    from datetime import timedelta
    if listing.lead_status in ("converted", "stopped"):
        return
    listing.lead_status = "following_up"
    listing.follow_up_date = timezone.localdate() + timedelta(days=interval_days)
    listing.save(update_fields=["lead_status", "follow_up_date"])


def _start_scheduler():
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, daemon=True)
    t.start()


def _scheduler_loop():
    """Background thread: checks every 60s for:
    - Scheduled email campaigns ready to send
    - Auto-scrape jobs due to run
    """
    time.sleep(10)
    while True:
        try:
            close_old_connections()
            now = timezone.now()

            # ── Scheduled campaigns ──────────────────────────────────────────
            ready = EmailCampaign.objects.filter(
                status="scheduled",
                scheduled_at__lte=now,
            ).exclude(id__in=list(ACTIVE_CAMPAIGNS))
            for campaign in ready:
                campaign.status = "draft"
                campaign.save(update_fields=["status"])
                launch_campaign(campaign.id)

            # ── Auto-scrape ──────────────────────────────────────────────────
            try:
                cfg = AutoConfig.objects.get(pk=1)
                if (
                    cfg.auto_scrape_enabled
                    and cfg.auto_scrape_phrase.strip()
                    and cfg.auto_scrape_locations.strip()
                    and cfg.auto_scrape_next_run
                    and cfg.auto_scrape_next_run <= now
                ):
                    from datetime import timedelta
                    locations = [l.strip() for l in cfg.auto_scrape_locations.split(",") if l.strip()]
                    source = cfg.auto_scrape_source or "maps"

                    # Create the job
                    job = ScrapeJob.objects.create(
                        status="queued",
                        source=source,
                        search_phrase=cfg.auto_scrape_phrase,
                        locations=cfg.auto_scrape_locations,
                        max_results=cfg.auto_scrape_max_results,
                        total_locations=len(locations),
                        speed="normal",
                    )

                    auto_campaign = cfg.auto_campaign_enabled
                    if source == "bing_maps":
                        threading.Thread(
                            target=run_bing_maps_scrape,
                            args=(job.id, cfg.auto_scrape_phrase, locations, cfg.auto_scrape_max_results, auto_campaign),
                            daemon=True,
                        ).start()
                    elif source in {"google", "bing", "yahoo", "duckduckgo", "yandex", "ecosia", "ask"}:
                        threading.Thread(target=run_search_scrape, args=(job.id, True), daemon=True).start()
                    else:
                        threading.Thread(
                            target=run_scrape,
                            args=(job.id, cfg.auto_scrape_phrase, locations, "com", cfg.auto_scrape_max_results, auto_campaign),
                            daemon=True,
                        ).start()

                    # Update schedule
                    cfg.auto_scrape_last_run = now
                    cfg.auto_scrape_next_run = now + timedelta(hours=cfg.auto_scrape_interval_hours)
                    cfg.save(update_fields=["auto_scrape_last_run", "auto_scrape_next_run"])
            except AutoConfig.DoesNotExist:
                pass
            except Exception:
                pass

        except Exception:
            pass
        time.sleep(60)


_start_scheduler()


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _log(job, message, level="INFO"):
    _db_retry(JobLog.objects.create, job=job, level=level, message=message)


def _db_retry(fn, *args, **kwargs):
    delay = 0.1
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.5)
    return None


def _set_job_active(job_id, active):
    with ACTIVE_JOBS_LOCK:
        if active:
            ACTIVE_JOBS.add(job_id)
        else:
            ACTIVE_JOBS.discard(job_id)


def _is_job_active(job_id):
    with ACTIVE_JOBS_LOCK:
        return job_id in ACTIVE_JOBS


def _redirect_back(request, fallback_url):
    next_url = request.META.get("HTTP_REFERER")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect(fallback_url)


def _global_stats():
    from datetime import date
    today = date.today()
    total = BusinessListing.objects.count()
    return {
        "total_jobs": ScrapeJob.objects.count(),
        "total_leads": total,
        "leads_with_email": BusinessListing.objects.exclude(email="").count(),
        "leads_with_phone": BusinessListing.objects.exclude(phone="").count(),
        "leads_with_website": BusinessListing.objects.exclude(website="").count(),
        "active_jobs": ScrapeJob.objects.filter(status__in=["queued", "running"]).count(),
        "campaigns": EmailCampaign.objects.count(),
        "smtp_profiles": SmtpProfile.objects.count(),
        # Pipeline / lifecycle
        "leads_converted": BusinessListing.objects.filter(lead_status="converted").count(),
        "leads_stopped": BusinessListing.objects.filter(lead_status="stopped").count(),
        "leads_following_up": BusinessListing.objects.filter(lead_status="following_up").count(),
        "leads_starred": BusinessListing.objects.filter(is_starred=True).count(),
        "follow_up_today": BusinessListing.objects.filter(
            lead_status="following_up", follow_up_date__lte=today
        ).count(),
        # Gap counts
        "no_email": BusinessListing.objects.filter(email="").count(),
        "no_phone": BusinessListing.objects.filter(phone="").count(),
        "no_website": BusinessListing.objects.filter(website="").count(),
    }


def _ai_tips(stats):
    tips = generate_smart_tips(stats)
    scheduled = EmailCampaign.objects.filter(status="scheduled").count()
    if scheduled:
        tips.append({
            "icon": "⏰",
            "type": "info",
            "text": f"{scheduled} campaign{'s' if scheduled > 1 else ''} scheduled and will send automatically.",
        })
    return tips[:3]


def _get_notifications():
    """Return follow-up reminders for the sidebar."""
    from django.utils import timezone
    from datetime import timedelta, date
    now = timezone.now()
    today = date.today()
    one_day_ago = now - timedelta(days=1)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)
    notifs = []

    # Overdue follow-ups based on follow_up_date field
    overdue = BusinessListing.objects.filter(
        lead_status="following_up",
        follow_up_date__lt=today,
    ).count()
    if overdue:
        notifs.append({
            "type": "danger",
            "icon": "⚠️",
            "message": f"{overdue} overdue follow-up{'s' if overdue > 1 else ''} — action needed!",
            "url": "/leads/?lead_status=following_up",
            "key": f"overdue-{today}-{overdue}",
        })

    # Follow-ups due today
    due_today = BusinessListing.objects.filter(
        lead_status="following_up",
        follow_up_date=today,
    ).count()
    if due_today:
        notifs.append({
            "type": "warning",
            "icon": "🔔",
            "message": f"{due_today} follow-up{'s' if due_today > 1 else ''} due today!",
            "url": "/leads/?lead_status=following_up",
            "key": f"due-today-{today}-{due_today}",
        })

    # Leads contacted exactly 1 day ago — suggest follow-up
    contacted_yesterday = ContactAttempt.objects.filter(
        contacted_at__date=one_day_ago.date()
    ).values("listing_id").distinct().count()
    if contacted_yesterday:
        notifs.append({
            "type": "warning",
            "icon": "📬",
            "message": f"{contacted_yesterday} lead{'s' if contacted_yesterday > 1 else ''} contacted yesterday — schedule follow-up!",
            "url": "/leads/?contacted=1d",
            "key": f"contacted-1d-{one_day_ago.date()}-{contacted_yesterday}",
        })

    # Leads contacted 7 days ago — weekly re-engage
    contacted_week = ContactAttempt.objects.filter(
        contacted_at__date=seven_days_ago.date()
    ).values("listing_id").distinct().count()
    if contacted_week:
        notifs.append({
            "type": "info",
            "icon": "📅",
            "message": f"{contacted_week} lead{'s' if contacted_week > 1 else ''} contacted 7 days ago — time to re-engage!",
            "url": "/leads/?contacted=7d",
            "key": f"contacted-7d-{seven_days_ago.date()}-{contacted_week}",
        })

    # Leads contacted 14 days ago — final re-engagement reminder
    contacted_fortnight = ContactAttempt.objects.filter(
        contacted_at__date=fourteen_days_ago.date()
    ).values("listing_id").distinct().count()
    if contacted_fortnight:
        notifs.append({
            "type": "info",
            "icon": "📣",
            "message": f"{contacted_fortnight} lead{'s' if contacted_fortnight > 1 else ''} contacted 14 days ago — final follow-up window!",
            "url": "/leads/?contacted=14d",
            "key": f"contacted-14d-{fourteen_days_ago.date()}-{contacted_fortnight}",
        })

    return notifs


# ─── AI Dashboard helpers ─────────────────────────────────────────────────────

def _get_connect_today(limit=8):
    """Return top leads to contact today, ranked by urgency + AI score."""
    from datetime import date
    from .ai_engine import score_lead
    today = date.today()

    STATUS_LABELS = {
        "fresh": "Fresh",
        "following_up": "Following Up",
        "converted": "Converted",
        "stopped": "Stopped",
    }

    candidates = []

    # 1. Starred leads not yet converted/stopped
    starred = BusinessListing.objects.filter(
        is_starred=True
    ).exclude(lead_status__in=["converted", "stopped"]).order_by("-scraped_at")[:30]

    # 2. Follow-up due today or overdue
    due = BusinessListing.objects.filter(
        lead_status="following_up",
        follow_up_date__lte=today,
    ).exclude(lead_status__in=["converted", "stopped"]).order_by("follow_up_date")[:30]

    # 3. Fresh leads with email (highest conversion potential)
    fresh = BusinessListing.objects.filter(
        lead_status="fresh",
    ).exclude(email="").order_by("-scraped_at")[:30]

    seen_ids = set()
    for qs in [starred, due, fresh]:
        for lead in qs:
            if lead.id in seen_ids:
                continue
            seen_ids.add(lead.id)
            score = score_lead({
                "name": lead.name, "email": lead.email,
                "phone": lead.phone, "website": lead.website,
                "address": lead.address,
            })

            # Build reason string
            reasons = []
            if lead.is_starred:
                reasons.append("starred priority lead")
            if lead.follow_up_date and lead.follow_up_date <= today:
                overdue = (today - lead.follow_up_date).days
                reasons.append(f"follow-up {'overdue ' + str(overdue) + 'd' if overdue > 0 else 'due today'}")
            if lead.lead_status == "fresh":
                reasons.append("new — first contact")
            contact_count = lead.contact_attempts.count()
            if contact_count:
                reasons.append(f"{contact_count} prior contact{'s' if contact_count > 1 else ''}")
            if lead.email:
                reasons.append("✉ has email")
            if lead.phone:
                reasons.append("📞 has phone")

            priority = "high" if score >= 70 else ("mid" if score >= 40 else "low")
            candidates.append({
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "lead_status": lead.lead_status,
                "lead_status_label": STATUS_LABELS.get(lead.lead_status, lead.lead_status),
                "is_starred": lead.is_starred,
                "score": score,
                "priority": priority,
                "reason": " · ".join(reasons) if reasons else "Fresh lead",
                "follow_up_date": lead.follow_up_date,
            })

    # Sort: starred first, then by score desc
    candidates.sort(key=lambda x: (-int(x["is_starred"]), -x["score"]))
    return candidates[:limit]


def _get_top_leads(limit=6):
    """Return highest AI-scored leads for the dashboard."""
    from .ai_engine import score_lead
    STATUS_LABELS = {"fresh": "Fresh", "following_up": "Following Up", "converted": "Converted", "stopped": "Stopped"}
    leads = list(BusinessListing.objects.exclude(lead_status__in=["stopped"]).order_by("-is_starred", "-scraped_at")[:100])
    scored = []
    for lead in leads:
        score = score_lead({
            "name": lead.name, "email": lead.email,
            "phone": lead.phone, "website": lead.website, "address": lead.address,
        })
        priority = "high" if score >= 70 else ("mid" if score >= 40 else "low")
        scored.append({
            "id": lead.id, "name": lead.name, "email": lead.email, "phone": lead.phone,
            "is_starred": lead.is_starred, "lead_status": lead.lead_status,
            "lead_status_label": STATUS_LABELS.get(lead.lead_status, lead.lead_status),
            "score": score, "priority": priority,
        })
    scored.sort(key=lambda x: (-int(x["is_starred"]), -x["score"]))
    return scored[:limit]


def _get_campaign_suggestions(stats):
    """Return AI-generated campaign action suggestions."""
    suggestions = []

    # Unsent leads with email
    unsent = BusinessListing.objects.filter(lead_status="fresh").exclude(email="").count()
    if unsent > 0:
        suggestions.append({
            "icon": "✉️",
            "title": f"{unsent} fresh leads ready to email",
            "desc": "These leads haven't been contacted yet. Start a campaign now.",
            "action_label": "Create Campaign",
            "action_url": "/campaigns/new/",
        })

    # Draft campaigns
    drafts = EmailCampaign.objects.filter(status="draft").count()
    if drafts:
        suggestions.append({
            "icon": "📝",
            "title": f"{drafts} campaign{'s' if drafts > 1 else ''} still in draft",
            "desc": "Review and send your drafted campaigns.",
            "action_label": "View Campaigns",
            "action_url": "/campaigns/",
        })

    # Follow-ups due — suggest re-engage campaign
    from datetime import date
    overdue = BusinessListing.objects.filter(
        lead_status="following_up",
        follow_up_date__lte=date.today(),
    ).count()
    if overdue:
        suggestions.append({
            "icon": "🔔",
            "title": f"{overdue} follow-up{'s' if overdue > 1 else ''} overdue — send a re-engage email",
            "desc": "Reach out to leads you marked for follow-up.",
            "action_label": "View Follow-Ups",
            "action_url": "/leads/?lead_status=following_up",
        })

    # Leads with no email — suggest search-scrape to find emails
    no_email = stats.get("no_email", 0)
    if no_email > 20:
        suggestions.append({
            "icon": "🔍",
            "title": f"{no_email} leads are missing email addresses",
            "desc": "Use Search Engine Scraper to find contact emails.",
            "action_label": "Search Scraper",
            "action_url": "/search/",
        })

    return suggestions[:4]


# ─── Dashboard ───────────────────────────────────────────────────────────────

def _get_pipeline_insights():
    """AI-powered pipeline health, velocity, and action signals for the dashboard."""
    from datetime import date, timedelta
    from django.db.models import Count
    today = date.today()
    week_ago   = today - timedelta(days=7)
    week_ago2  = today - timedelta(days=14)

    total = BusinessListing.objects.count() or 1
    converted  = BusinessListing.objects.filter(lead_status="converted").count()
    following  = BusinessListing.objects.filter(lead_status="following_up").count()
    stopped    = BusinessListing.objects.filter(lead_status="stopped").count()
    fresh      = BusinessListing.objects.filter(lead_status="fresh").count()
    overdue    = BusinessListing.objects.filter(lead_status="following_up", follow_up_date__lt=today).count()
    starred    = BusinessListing.objects.filter(is_starred=True).count()

    # Contact velocity: contacts this week vs last week
    contacts_this_week = ContactAttempt.objects.filter(contacted_at__date__gte=week_ago).count()
    contacts_last_week = ContactAttempt.objects.filter(
        contacted_at__date__gte=week_ago2, contacted_at__date__lt=week_ago
    ).count()
    velocity_up = contacts_this_week >= contacts_last_week
    velocity_pct = (
        round((contacts_this_week - contacts_last_week) / max(contacts_last_week, 1) * 100)
        if contacts_last_week else (100 if contacts_this_week else 0)
    )

    # Pipeline health score 0–100
    conversion_rate = converted / total * 100
    data_pct = BusinessListing.objects.exclude(email="").count() / total * 100
    followup_pct = min(following / max(fresh + following, 1) * 100, 100)
    health = round(
        conversion_rate * 0.4 +
        data_pct * 0.3 +
        followup_pct * 0.2 +
        min(contacts_this_week / max(total * 0.05, 1), 1) * 10
    )
    health = min(100, max(0, health))

    # Best contact day (last 30 days)
    from django.db.models.functions import ExtractWeekDay
    day_counts = (
        ContactAttempt.objects
        .filter(contacted_at__date__gte=today - timedelta(days=30))
        .annotate(dow=ExtractWeekDay("contacted_at"))
        .values("dow")
        .annotate(c=Count("id"))
        .order_by("-c")
    )
    DOW = {1:"Sunday", 2:"Monday", 3:"Tuesday", 4:"Wednesday", 5:"Thursday", 6:"Friday", 7:"Saturday"}
    best_day = DOW.get(day_counts[0]["dow"], "Any day") if day_counts else None

    # AI-suggested actions
    actions = []
    if overdue:
        actions.append({"icon": "⚠️", "color": "#dc2626", "bg": "#fef2f2",
                        "text": f"{overdue} follow-up{'s' if overdue > 1 else ''} overdue — contact now",
                        "url": "/leads/?lead_status=following_up"})
    if starred and starred > 0:
        actions.append({"icon": "⭐", "color": "#d97706", "bg": "#fffbeb",
                        "text": f"{starred} starred lead{'s' if starred > 1 else ''} — prioritise these",
                        "url": "/leads/?starred=1"})
    no_email = BusinessListing.objects.filter(email="").count()
    if no_email > 10:
        actions.append({"icon": "✉️", "color": "#7c3aed", "bg": "#f5f3ff",
                        "text": f"{no_email} leads missing email — use search scraper to find them",
                        "url": "/search/"})
    if fresh > 50:
        actions.append({"icon": "🚀", "color": "#2563eb", "bg": "#eff6ff",
                        "text": f"{fresh} fresh leads ready — launch an email campaign",
                        "url": "/campaigns/new/"})
    if contacts_last_week and not contacts_this_week:
        actions.append({"icon": "📉", "color": "#64748b", "bg": "#f8fafc",
                        "text": "No contacts logged this week — keep the momentum going",
                        "url": "/leads/"})

    return {
        "health": health,
        "health_label": "Excellent" if health >= 80 else "Good" if health >= 60 else "Fair" if health >= 40 else "Needs Work",
        "health_color": "#10b981" if health >= 80 else "#f59e0b" if health >= 60 else "#ef4444",
        "conversion_rate": round(conversion_rate, 1),
        "contacts_this_week": contacts_this_week,
        "contacts_last_week": contacts_last_week,
        "velocity_up": velocity_up,
        "velocity_pct": abs(velocity_pct),
        "best_day": best_day,
        "actions": actions[:4],
        "fresh": fresh,
        "following": following,
        "converted": converted,
        "stopped": stopped,
        "total": total,
    }


def home(request):
    from datetime import date as _date, timedelta as _td
    stats = _global_stats()
    recent_jobs = list(ScrapeJob.objects.order_by("-created_at")[:8])
    refresh_home = any(j.status in {"queued", "running", "paused"} for j in recent_jobs)
    auto_config = AutoConfig.get()
    smtp_profiles = list(SmtpProfile.objects.all())
    today = _date.today()
    next_week = today + _td(days=7)

    today_followups = list(
        BusinessListing.objects.filter(follow_up_date=today)
        .order_by("-is_starred", "name")[:12]
    )
    nextweek_followups = list(
        BusinessListing.objects.filter(
            follow_up_date__gt=today,
            follow_up_date__lte=next_week,
        ).order_by("follow_up_date", "-is_starred")[:12]
    )

    return render(request, "home.html", {
        "recent_jobs": recent_jobs,
        "refresh_home": refresh_home,
        "global_stats": stats,
        "ai_tips": _ai_tips(stats),
        "notifications": _get_notifications(),
        "active_page": "dashboard",
        "connect_today": _get_connect_today(),
        "top_leads": _get_top_leads(),
        "campaign_suggestions": _get_campaign_suggestions(stats),
        "pipeline_insights": _get_pipeline_insights(),
        "auto_config": auto_config,
        "auto_scrape_on": auto_config.auto_scrape_enabled,
        "auto_campaign_on": auto_config.auto_campaign_enabled,
        "smtp_profiles": smtp_profiles,
        "scrape_interval_options": [1, 2, 4, 6, 8, 12, 24, 48, 72, 168],
        "today_followups": today_followups,
        "nextweek_followups": nextweek_followups,
        "today_str": today.strftime("%b %d"),
        "nextweek_str": next_week.strftime("%b %d"),
    })


# ─── Google Maps Home (separate page) ────────────────────────────────────────

def google_maps_home(request):
    if request.method == "POST":
        return _start_maps_job(request)

    recent_jobs = list(ScrapeJob.objects.filter(source="maps").order_by("-created_at")[:12])
    refresh_home = any(j.status in {"queued", "running", "paused"} for j in recent_jobs)
    return render(request, "google_maps_home.html", {
        "domains": DOMAINS,
        "recent_jobs": recent_jobs,
        "refresh_home": refresh_home,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "maps",
    })


# ─── Auto-Config save ─────────────────────────────────────────────────────────

@require_POST
def save_auto_config(request):
    from django.utils import timezone as tz
    from datetime import timedelta
    cfg = AutoConfig.get()

    cfg.auto_scrape_enabled = "auto_scrape_enabled" in request.POST
    cfg.auto_scrape_phrase = request.POST.get("auto_scrape_phrase", "").strip()
    cfg.auto_scrape_locations = request.POST.get("auto_scrape_locations", "").strip()
    try:
        cfg.auto_scrape_max_results = max(10, min(5000, int(request.POST.get("auto_scrape_max_results", 200))))
    except ValueError:
        pass
    try:
        cfg.auto_scrape_interval_hours = max(1, int(request.POST.get("auto_scrape_interval_hours", 24)))
    except ValueError:
        pass
    cfg.auto_scrape_source = request.POST.get("auto_scrape_source", "maps").strip()

    # Calculate next run if enabling
    if cfg.auto_scrape_enabled and not cfg.auto_scrape_next_run:
        cfg.auto_scrape_next_run = tz.now() + timedelta(hours=cfg.auto_scrape_interval_hours)

    cfg.auto_campaign_enabled = "auto_campaign_enabled" in request.POST
    smtp_id = request.POST.get("auto_campaign_smtp_profile", "").strip()
    if smtp_id:
        try:
            cfg.auto_campaign_smtp_profile = SmtpProfile.objects.get(id=int(smtp_id))
        except (SmtpProfile.DoesNotExist, ValueError):
            cfg.auto_campaign_smtp_profile = None
    else:
        cfg.auto_campaign_smtp_profile = None

    cfg.auto_campaign_from_name = request.POST.get("auto_campaign_from_name", "").strip()
    cfg.auto_campaign_from_email = request.POST.get("auto_campaign_from_email", "").strip()
    cfg.auto_campaign_subject = request.POST.get("auto_campaign_subject", "").strip()
    cfg.auto_campaign_body = request.POST.get("auto_campaign_body", "").strip()
    try:
        cfg.auto_campaign_delay_minutes = max(0, int(request.POST.get("auto_campaign_delay_minutes", 30)))
    except ValueError:
        pass

    cfg.save()
    return redirect("home")


def _start_maps_job(request):
    search_phrase = request.POST.get("search_phrase", "").strip()
    locations_raw = request.POST.get("locations", "")
    domain = request.POST.get("domain", "com").strip()
    speed = request.POST.get("speed", "normal").strip()
    source = request.POST.get("source", "maps").strip()
    auto_campaign = request.POST.get("auto_campaign", "") == "1"
    try:
        max_results = min(MAX_RESULTS_CAP, max(1, int(request.POST.get("max_results", "1000"))))
    except ValueError:
        max_results = 1000
    if speed not in {"slow", "normal", "fast"}:
        speed = "normal"
    if source not in {"maps", "bing_maps"}:
        source = "maps"

    locations = [loc.strip() for loc in locations_raw.split(",") if loc.strip()]
    job = ScrapeJob.objects.create(
        status="queued",
        source=source,
        search_phrase=search_phrase,
        domain=domain,
        locations=locations_raw,
        max_results=max_results,
        total_locations=len(locations),
        speed=speed,
    )
    if source == "bing_maps":
        threading.Thread(
            target=run_bing_maps_scrape,
            args=(job.id, search_phrase, locations, max_results, auto_campaign),
            daemon=True,
        ).start()
        return redirect("job_detail", job_id=job.id)
    threading.Thread(
        target=run_scrape,
        args=(job.id, search_phrase, locations, domain, max_results, auto_campaign),
        daemon=True,
    ).start()
    return redirect("job_detail", job_id=job.id)


# ─── Maps Scraping ───────────────────────────────────────────────────────────

def run_bing_maps_scrape(job_id, search_phrase, locations, max_results, auto_campaign=True):
    _set_job_active(job_id, True)
    close_old_connections()
    try:
        job = ScrapeJob.objects.get(id=job_id)
    except ScrapeJob.DoesNotExist:
        _set_job_active(job_id, False)
        return

    try:
        job.status = "running"
        job.total_locations = max(job.total_locations, len(locations))
        job.save(update_fields=["status", "total_locations", "updated_at"])
        _log(job, f"[BingMaps] Job started: '{search_phrase}' ({job.total_locations} locations, target {max_results}).")

        if not locations:
            job.status = "failed"
            job.last_error = "No locations provided."
            job.save(update_fields=["status", "last_error", "updated_at"])
            _log(job, "No locations provided. Job failed.", level="ERROR")
            return

        total_results = 0
        had_errors = False
        seen_keys = set()
        email_cache = {}
        email_cache_lock = threading.Lock()

        last_pause_check = {"time": 0, "paused": False}
        last_stop_check = {"time": 0, "stop": False}

        def should_pause():
            now = time.time()
            if now - last_pause_check["time"] > 2:
                status = ScrapeJob.objects.filter(id=job_id).values_list("status", flat=True).first()
                last_pause_check["paused"] = status == "paused"
                last_pause_check["time"] = now
            return last_pause_check["paused"]

        def should_stop():
            now = time.time()
            if now - last_stop_check["time"] > 2:
                row = ScrapeJob.objects.filter(id=job_id).values_list("status", "last_error").first()
                if row:
                    last_stop_check["stop"] = row[0] == "failed" and row[1] == "Stopped by user."
                else:
                    last_stop_check["stop"] = True
                last_stop_check["time"] = now
            return last_stop_check["stop"]

        total_locations = len(locations)

        for idx, loc in enumerate(locations, start=1):
            if total_results >= max_results:
                break
            remaining = max_results - total_results
            _log(job, f"[BingMaps] Scraping location {idx}/{total_locations}: {loc} (target {remaining}).")

            try:
                results = scrape_bing_maps(
                    search_phrase, loc,
                    max_results=remaining,
                    log_fn=lambda message: _log(job, message),
                    should_pause_fn=should_pause,
                    should_stop_fn=should_stop,
                    speed=job.speed,
                    email_cache=email_cache,
                    email_cache_lock=email_cache_lock,
                )

                for res in results:
                    key = res.get("website", "") or f"{res.get('name','').lower()}|{loc.lower()}"
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    try:
                        _db_retry(
                            BusinessListing.objects.create,
                            job=job, source="bing_maps",
                            name=res.get("name", ""), phone=res.get("phone", ""),
                            email=res.get("email", ""), website=res.get("website", ""),
                            maps_url=res.get("maps_url", ""),
                            address=res.get("address", ""),
                            search_query=search_phrase, location=loc,
                        )
                        total_results += 1
                    except Exception as exc:
                        had_errors = True
                        _log(job, f"DB write error: {exc}", level="ERROR")

                job.collected_listings = total_results
                job.processed_listings = total_results
                job.emails_found = BusinessListing.objects.filter(job=job).exclude(email="").count()
                job.processed_locations = idx
                job.save(update_fields=["collected_listings", "processed_listings", "emails_found", "processed_locations", "updated_at"])
                _log(job, f"[BingMaps] Location {loc} done. {len(results)} listings added.")

            except (BingStopScrape, StopScrape):
                had_errors = True
                job.last_error = "Stopped by user."
                job.save(update_fields=["last_error", "updated_at"])
                _log(job, "Stop requested. Ending job.", level="ERROR")
                break
            except Exception as exc:
                had_errors = True
                job.last_error = str(exc)
                job.processed_locations = idx
                job.save(update_fields=["last_error", "processed_locations", "updated_at"])
                _log(job, f"[BingMaps] Error scraping {loc}: {exc}", level="ERROR")

        job.status = "failed" if (total_results == 0 and had_errors) else ("completed_with_errors" if had_errors else "completed")
        job.save(update_fields=["status", "updated_at"])
        _log(job, f"[BingMaps] Job {job.status.replace('_', ' ')}.")

        if job.status in {"completed", "completed_with_errors"} and auto_campaign:
            _auto_create_campaign(job)

    except Exception as exc:
        job.status = "failed"
        job.last_error = str(exc)
        job.save(update_fields=["status", "last_error", "updated_at"])
        _log(job, f"[BingMaps] Job crashed: {exc}", level="ERROR")
    finally:
        close_old_connections()
        _set_job_active(job_id, False)


def run_scrape(job_id, search_phrase, locations, domain, max_results, auto_campaign=True):
    _set_job_active(job_id, True)
    close_old_connections()
    try:
        job = ScrapeJob.objects.get(id=job_id)
    except ScrapeJob.DoesNotExist:
        _set_job_active(job_id, False)
        return

    try:
        job.status = "running"
        job.total_locations = max(job.total_locations, len(locations))
        job.save(update_fields=["status", "total_locations", "updated_at"])
        _log(job, f"Job started: '{search_phrase}' ({job.total_locations} locations, target {max_results}).")

        if not locations:
            job.status = "failed"
            job.last_error = "No locations provided."
            job.save(update_fields=["status", "last_error", "updated_at"])
            _log(job, "No locations provided. Job failed.", level="ERROR")
            return

        total_results = job.total_results
        had_errors = False
        seen_keys = set()

        existing = BusinessListing.objects.filter(job=job).values_list("maps_url", "name", "location")
        for maps_url, name, location in existing:
            if maps_url:
                seen_keys.add(maps_url.split("?", 1)[0].rstrip("/"))
            else:
                seen_keys.add(f"{(name or '').lower()}|{(location or '').lower()}")

        db_queue = queue.Queue()
        db_stop = threading.Event()
        stats = {
            "total_results": total_results,
            "collected_listings": job.collected_listings,
            "processed_listings": job.processed_listings,
            "emails_found": job.emails_found,
        }
        email_cache = {}
        shared_drivers = None
        try:
            shared_drivers = create_shared_drivers(job.speed)
        except Exception as exc:
            _log(job, f"Driver reuse disabled: {exc}", level="WARN")

        def db_writer():
            nonlocal had_errors
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("PRAGMA journal_mode=WAL;")
                    cursor.execute("PRAGMA synchronous=NORMAL;")
            except Exception:
                pass
            last_save = time.time()
            while not db_stop.is_set() or not db_queue.empty():
                try:
                    item = db_queue.get(timeout=0.5)
                except queue.Empty:
                    item = None
                if item is None:
                    if time.time() - last_save > 2:
                        job.total_results = stats["total_results"]
                        job.collected_listings = stats["collected_listings"]
                        job.processed_listings = stats["processed_listings"]
                        job.emails_found = stats["emails_found"]
                        job.save(update_fields=["total_results", "collected_listings", "processed_listings", "emails_found", "updated_at"])
                        last_save = time.time()
                    continue
                try:
                    if item["type"] == "result":
                        res = item["result"]
                        loc = item["location"]
                        maps_url = res.get("maps_url", "")
                        key = maps_url or f"{(res.get('name') or '').lower()}|{loc.lower()}"
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                        _db_retry(
                            BusinessListing.objects.create,
                            job=job, source="maps",
                            name=res.get("name", ""), phone=res.get("phone", ""),
                            email=res.get("email", ""), website=res.get("website", ""),
                            maps_url=maps_url, search_query=search_phrase, location=loc,
                        )
                        stats["total_results"] += 1
                        stats["collected_listings"] = max(stats["collected_listings"], stats["total_results"])
                        stats["processed_listings"] = max(stats["processed_listings"], stats["total_results"])
                    elif item["type"] == "collected":
                        stats["collected_listings"] = max(stats["collected_listings"], item["count"])
                    elif item["type"] == "processed":
                        stats["processed_listings"] = max(stats["processed_listings"], item["count"])
                    elif item["type"] == "email_update":
                        upd = item["domain"]
                        email = item["email"]
                        updated = _db_retry(
                            BusinessListing.objects.filter(job=job, email="", website__icontains=upd).update,
                            email=email,
                        ) or 0
                        stats["emails_found"] += updated
                    if time.time() - last_save > 2:
                        job.total_results = stats["total_results"]
                        job.collected_listings = stats["collected_listings"]
                        job.processed_listings = stats["processed_listings"]
                        job.emails_found = stats["emails_found"]
                        _db_retry(job.save, update_fields=["total_results", "collected_listings", "processed_listings", "emails_found", "updated_at"])
                        last_save = time.time()
                except Exception as exc:
                    had_errors = True
                    try:
                        _log(job, f"DB writer error: {exc}", level="ERROR")
                    except Exception:
                        pass
                finally:
                    db_queue.task_done()

            job.total_results = stats["total_results"]
            job.collected_listings = stats["collected_listings"]
            job.processed_listings = stats["processed_listings"]
            job.emails_found = stats["emails_found"]
            _db_retry(job.save, update_fields=["total_results", "collected_listings", "processed_listings", "emails_found", "updated_at"])
            close_old_connections()

        writer_thread = threading.Thread(target=db_writer, daemon=True)
        writer_thread.start()

        last_pause_check = {"time": 0, "paused": False}
        last_stop_check = {"time": 0, "stop": False}

        def should_pause():
            now = time.time()
            if now - last_pause_check["time"] > 2:
                status = ScrapeJob.objects.filter(id=job_id).values_list("status", flat=True).first()
                last_pause_check["paused"] = status == "paused"
                last_pause_check["time"] = now
            return last_pause_check["paused"]

        def should_stop():
            now = time.time()
            if now - last_stop_check["time"] > 2:
                row = ScrapeJob.objects.filter(id=job_id).values_list("status", "last_error").first()
                if row:
                    status, last_error = row
                    last_stop_check["stop"] = status == "failed" and last_error == "Stopped by user."
                else:
                    last_stop_check["stop"] = True
                last_stop_check["time"] = now
            return last_stop_check["stop"]

        start_index = min(job.processed_locations, len(locations))
        total_locations = job.total_locations or len(locations)

        for idx, loc in enumerate(locations[start_index:], start=start_index + 1):
            if total_results >= max_results:
                break
            remaining = max_results - total_results
            _log(job, f"Scraping location {idx}/{total_locations}: {loc} (target {remaining}).")
            try:
                results = scrape_google_maps(
                    search_phrase, loc, domain, max_results=remaining,
                    log_fn=lambda message: _log(job, message),
                    should_pause_fn=should_pause, should_stop_fn=should_stop,
                    on_listings_update=lambda count, new: db_queue.put({"type": "collected", "count": count}),
                    on_detail_progress=lambda processed, total: db_queue.put({"type": "processed", "count": processed}),
                    on_result=lambda res, location=loc: db_queue.put({"type": "result", "result": res, "location": location}),
                    on_email_update=lambda domain, email: db_queue.put({"type": "email_update", "domain": domain, "email": email}),
                    speed=job.speed, seed_seen=set(seen_keys), email_cache=email_cache,
                    listing_driver=(shared_drivers or {}).get("listing_driver"),
                    detail_pool=(shared_drivers or {}).get("detail_pool"),
                    driver_path=(shared_drivers or {}).get("driver_path"),
                    detail_strategy=(shared_drivers or {}).get("detail_strategy"),
                )
                total_results = stats["total_results"]
                job.processed_locations = idx
                job.save(update_fields=["processed_locations", "updated_at"])
                _log(job, f"Location complete: {loc}. Added {len(results)} listings.")
            except StopScrape:
                had_errors = True
                job.last_error = "Stopped by user."
                job.save(update_fields=["last_error", "updated_at"])
                _log(job, "Stop requested. Ending job.", level="ERROR")
                break
            except Exception as exc:
                had_errors = True
                job.last_error = str(exc)
                job.processed_locations = idx
                job.save(update_fields=["last_error", "processed_locations", "updated_at"])
                _log(job, f"Error scraping {loc}: {exc}", level="ERROR")

        db_queue.join()
        db_stop.set()
        writer_thread.join(timeout=5)

        job.status = "failed" if (total_results == 0 and had_errors) else ("completed_with_errors" if had_errors else "completed")
        job.save(update_fields=["status", "updated_at"])
        _log(job, f"Job {job.status.replace('_', ' ')}.")

        if job.status in {"completed", "completed_with_errors"} and auto_campaign:
            _auto_create_campaign(job)

    except Exception as exc:
        job.status = "failed"
        job.last_error = str(exc)
        job.save(update_fields=["status", "last_error", "updated_at"])
        _log(job, f"Job crashed: {exc}", level="ERROR")
    finally:
        if shared_drivers:
            try:
                if shared_drivers.get("listing_driver"):
                    shared_drivers["listing_driver"].quit()
            except Exception:
                pass
            try:
                if shared_drivers.get("detail_pool"):
                    shared_drivers["detail_pool"].close()
            except Exception:
                pass
        close_old_connections()
        _set_job_active(job_id, False)


def _auto_create_campaign(job):
    try:
        close_old_connections()
        leads_with_email = BusinessListing.objects.filter(job=job).exclude(email="").count()
        if leads_with_email == 0:
            return
        if EmailCampaign.objects.filter(job_filter=job).exists():
            return

        campaign = EmailCampaign.objects.create(
            name=f"Campaign — {job.search_phrase} (auto)",
            subject=f"Hi {{name}}, reaching out about your business",
            body=(
                "Hi {name},\n\n"
                "I came across your business and wanted to reach out.\n\n"
                "Would you be open to a quick chat?\n\n"
                "Best regards"
            ),
            from_name="",
            from_email="your@email.com",
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_user="",
            smtp_password="",
            use_tls=True,
            status="draft",
            job_filter=job,
        )
        for listing in BusinessListing.objects.filter(job=job).exclude(email=""):
            EmailSend.objects.get_or_create(campaign=campaign, listing=listing)

        job.auto_campaign_created = True
        job.save(update_fields=["auto_campaign_created"])
        _log(job, f"Auto-created email campaign draft (ID {campaign.id}) for {leads_with_email} leads.")
    except Exception as exc:
        try:
            _log(job, f"Auto-campaign creation failed: {exc}", level="WARN")
        except Exception:
            pass


# ─── Search Engine Scraping ───────────────────────────────────────────────────

def bing_maps_home(request):
    if request.method == "POST":
        return _start_maps_job(request)

    recent_jobs = list(ScrapeJob.objects.filter(source="bing_maps").order_by("-created_at")[:12])
    refresh_home = any(j.status in {"queued", "running", "paused"} for j in recent_jobs)
    stats = _global_stats()
    return render(request, "bing_maps_home.html", {
        "recent_jobs": recent_jobs,
        "refresh_home": refresh_home,
        "global_stats": stats,
        "ai_tips": _ai_tips(stats),
        "notifications": _get_notifications(),
        "active_page": "bing_maps",
    })


def search_home(request):
    if request.method == "POST":
        return _start_search_job(request)

    recent_jobs = list(ScrapeJob.objects.exclude(source__in=["maps", "bing_maps"]).order_by("-created_at")[:12])
    return render(request, "search_home.html", {
        "recent_jobs": recent_jobs,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "search",
    })


def _start_search_job(request):
    search_phrase = request.POST.get("search_phrase", "").strip()
    location = request.POST.get("location", "").strip()
    engine = request.POST.get("engine", "google").strip()
    speed = request.POST.get("speed", "normal").strip()
    country = request.POST.get("country", "").strip().lower()
    search_type = request.POST.get("search_type", "web").strip()
    visit_pages = request.POST.get("visit_pages", "1") == "1"

    if engine not in {"google", "bing", "yahoo", "duckduckgo", "yandex", "ecosia", "ask"}:
        engine = "google"
    if speed not in {"slow", "normal", "fast"}:
        speed = "normal"
    if search_type not in {"web", "images", "videos", "news"}:
        search_type = "web"
    try:
        max_results = min(MAX_RESULTS_CAP, max(1, int(request.POST.get("max_results", "100"))))
    except ValueError:
        max_results = 100

    job = ScrapeJob.objects.create(
        status="queued",
        source=engine,
        search_phrase=search_phrase,
        locations=location,
        country=country,
        search_type=search_type,
        max_results=max_results,
        total_locations=1 if location else 0,
        speed=speed,
    )
    threading.Thread(target=run_search_scrape, args=(job.id, visit_pages), daemon=True).start()
    return redirect("search_job_detail", job_id=job.id)


def run_search_scrape(job_id, visit_pages=True):
    _set_job_active(job_id, True)
    close_old_connections()
    try:
        job = ScrapeJob.objects.get(id=job_id)
    except ScrapeJob.DoesNotExist:
        _set_job_active(job_id, False)
        return

    email_cache = {}
    email_cache_lock = threading.Lock()
    seen_lock = threading.Lock()

    try:
        job.status = "running"
        job.save(update_fields=["status", "updated_at"])
        _log(job, f"Search job started: '{job.search_phrase}' on {job.source} (target {job.max_results}, type={job.search_type}, country={job.country or 'any'})")

        last_stop_check = {"time": 0, "stop": False}
        last_pause_check = {"time": 0, "paused": False}

        def should_stop():
            now = time.time()
            if now - last_stop_check["time"] > 2:
                row = ScrapeJob.objects.filter(id=job_id).values_list("status", "last_error").first()
                if row:
                    last_stop_check["stop"] = row[0] == "failed" and row[1] == "Stopped by user."
                else:
                    last_stop_check["stop"] = True
                last_stop_check["time"] = now
            return last_stop_check["stop"]

        def should_pause():
            now = time.time()
            if now - last_pause_check["time"] > 2:
                status = ScrapeJob.objects.filter(id=job_id).values_list("status", flat=True).first()
                last_pause_check["paused"] = status == "paused"
                last_pause_check["time"] = now
            return last_pause_check["paused"]

        seen = set()

        def on_result(record):
            close_old_connections()
            key = record.get("website", "") or record.get("name", "")
            with seen_lock:
                if key in seen:
                    return
                seen.add(key)
            _db_retry(
                BusinessListing.objects.create,
                job=job,
                source=job.source,
                name=record.get("name", ""),
                phone=record.get("phone", ""),
                email=record.get("email", ""),
                website=record.get("website", ""),
                maps_url="",
                address=record.get("address", ""),
                search_query=record.get("search_query", job.search_phrase),
                location=record.get("location", job.locations),
            )
            # Update job stats safely (read-modify-write in main thread is fine here)
            with seen_lock:
                count = len(seen)
            try:
                ScrapeJob.objects.filter(id=job_id).update(
                    collected_listings=count,
                    updated_at=timezone.now(),
                )
            except Exception:
                pass

        speed_to_workers = {"slow": 3, "normal": 6, "fast": 10}
        max_email_workers = speed_to_workers.get(job.speed, 6)

        results = scrape_search_engine(
            search_phrase=job.search_phrase,
            location=job.locations,
            engine=job.source,
            max_results=job.max_results,
            country=job.country,
            search_type=job.search_type,
            visit_pages=visit_pages,
            log_fn=lambda msg: _log(job, msg),
            should_pause_fn=should_pause,
            should_stop_fn=should_stop,
            on_result=on_result,
            email_cache=email_cache,
            email_cache_lock=email_cache_lock,
            max_email_workers=max_email_workers,
        )

        final_count = BusinessListing.objects.filter(job=job).count()
        emails_count = BusinessListing.objects.filter(job=job).exclude(email="").count()
        job.collected_listings = final_count
        job.processed_listings = final_count
        job.emails_found = emails_count
        job.status = "completed"
        job.save(update_fields=["collected_listings", "processed_listings", "emails_found", "status", "updated_at"])
        _log(job, f"Search job completed. {final_count} leads, {emails_count} emails.")
    except (SearchStopScrape, StopScrape):
        job.status = "completed_with_errors"
        job.last_error = "Stopped by user."
        job.save(update_fields=["status", "last_error", "updated_at"])
        _log(job, "Search job stopped by user.", level="ERROR")
    except Exception as exc:
        job.status = "failed"
        job.last_error = str(exc)
        job.save(update_fields=["status", "last_error", "updated_at"])
        _log(job, f"Search job crashed: {exc}", level="ERROR")
    finally:
        close_old_connections()
        _set_job_active(job_id, False)


def search_job_detail(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    listings = BusinessListing.objects.filter(job=job).order_by("-scraped_at")[:200]
    logs = job.logs.order_by("-created_at")[:200]
    progress = 0
    if job.max_results:
        progress = min(100, int(job.collected_listings * 100 / job.max_results))
    return render(request, "search_job.html", {
        "job": job,
        "listings": listings,
        "logs": logs,
        "progress": progress,
        "is_running": job.status in {"queued", "running"},
        "can_pause": job.status == "running",
        "can_resume": job.status in {"paused", "failed"},
        "is_paused": job.status == "paused",
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "search",
    })


# ─── Leads ───────────────────────────────────────────────────────────────────

def _build_leads_qs(request_get):
    """Build the leads queryset from GET filter params. Returns (qs, filters_dict)."""
    qs = BusinessListing.objects.all()

    source_filter = request_get.get("source", "")
    has_email = request_get.get("has_email", "")
    has_phone = request_get.get("has_phone", "")
    has_website = request_get.get("has_website", "")
    query = request_get.get("q", "").strip()
    job_id = request_get.get("job_id", "")
    location_filter = request_get.get("location", "").strip()
    contacted_filter = request_get.get("contacted", "")
    lead_status_filter = request_get.get("lead_status", "")
    starred_filter = request_get.get("starred", "")

    if source_filter:
        qs = qs.filter(source=source_filter)
    if has_email == "1":
        qs = qs.exclude(email="")
    elif has_email == "0":
        qs = qs.filter(email="")
    if has_phone == "1":
        qs = qs.exclude(phone="")
    elif has_phone == "0":
        qs = qs.filter(phone="")
    if has_website == "1":
        qs = qs.exclude(website="")
    elif has_website == "0":
        qs = qs.filter(website="")
    if job_id:
        qs = qs.filter(job_id=job_id)
    if location_filter:
        qs = qs.filter(location__icontains=location_filter)
    if query:
        qs = qs.filter(
            Q(name__icontains=query) |
            Q(email__icontains=query) |
            Q(phone__icontains=query) |
            Q(website__icontains=query) |
            Q(location__icontains=query) |
            Q(address__icontains=query) |
            Q(search_query__icontains=query)
        )

    # Contacted filter
    if contacted_filter == "1d":
        from datetime import timedelta
        one_day_ago = timezone.now() - timedelta(days=1)
        contacted_ids = ContactAttempt.objects.filter(
            contacted_at__date=one_day_ago.date()
        ).values_list("listing_id", flat=True)
        qs = qs.filter(id__in=contacted_ids)
    elif contacted_filter == "7d":
        from datetime import timedelta
        seven_days_ago = timezone.now() - timedelta(days=7)
        contacted_ids = ContactAttempt.objects.filter(
            contacted_at__date=seven_days_ago.date()
        ).values_list("listing_id", flat=True)
        qs = qs.filter(id__in=contacted_ids)
    elif contacted_filter == "14d":
        from datetime import timedelta
        fourteen_days_ago = timezone.now() - timedelta(days=14)
        contacted_ids = ContactAttempt.objects.filter(
            contacted_at__date=fourteen_days_ago.date()
        ).values_list("listing_id", flat=True)
        qs = qs.filter(id__in=contacted_ids)
    elif contacted_filter == "never":
        contacted_ids = ContactAttempt.objects.values_list("listing_id", flat=True).distinct()
        qs = qs.exclude(id__in=contacted_ids)
    elif contacted_filter == "any":
        contacted_ids = ContactAttempt.objects.values_list("listing_id", flat=True).distinct()
        qs = qs.filter(id__in=contacted_ids)

    # Lead status filter
    if lead_status_filter in ("fresh", "following_up", "converted", "stopped"):
        qs = qs.filter(lead_status=lead_status_filter)

    # Starred filter
    if starred_filter == "1":
        qs = qs.filter(is_starred=True)

    filters = {
        "source": source_filter,
        "has_email": has_email,
        "has_phone": has_phone,
        "has_website": has_website,
        "q": query,
        "job_id": job_id,
        "location": location_filter,
        "contacted": contacted_filter,
        "lead_status": lead_status_filter,
        "starred": starred_filter,
    }
    return qs, filters


def leads(request):
    qs, filters = _build_leads_qs(request.GET)

    # Starred leads always float to the top
    listings = list(qs.order_by("-is_starred", "-scraped_at")[:2000].prefetch_related("contact_attempts"))

    # Attach last contact info (no underscore prefix — Django templates reject those)
    for listing in listings:
        attempts = list(listing.contact_attempts.all()[:3])
        listing.recent_attempts = attempts
        listing.last_contact_info = attempts[0] if attempts else None
        listing.contact_total = len(attempts)

    from datetime import date
    today = date.today()
    stats = {
        "total": BusinessListing.objects.count(),
        "with_phone": BusinessListing.objects.exclude(phone="").count(),
        "with_email": BusinessListing.objects.exclude(email="").count(),
        "with_website": BusinessListing.objects.exclude(website="").count(),
        "converted": BusinessListing.objects.filter(lead_status="converted").count(),
        "stopped": BusinessListing.objects.filter(lead_status="stopped").count(),
        "following_up": BusinessListing.objects.filter(lead_status="following_up").count(),
        "starred": BusinessListing.objects.filter(is_starred=True).count(),
        "follow_up_today": BusinessListing.objects.filter(
            lead_status="following_up", follow_up_date__lte=today
        ).count(),
    }
    jobs_for_filter = ScrapeJob.objects.order_by("-created_at")[:50]
    return render(request, "leads.html", {
        "listings": listings,
        "stats": stats,
        "jobs_for_filter": jobs_for_filter,
        "filters": filters,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "leads",
    })


# ─── Lead contact logging ─────────────────────────────────────────────────────

@require_POST
def log_contact(request, listing_id):
    listing = get_object_or_404(BusinessListing, id=listing_id)
    channel = request.POST.get("channel", "email")
    notes = request.POST.get("notes", "").strip()
    if channel not in {"email", "gmail", "whatsapp", "telegram", "call"}:
        channel = "email"
    ContactAttempt.objects.create(listing=listing, channel=channel, notes=notes)
    _mark_contacted_for_followup(listing)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({
            "ok": True,
            "channel": channel,
            "lead_status": listing.lead_status,
            "follow_up_date": listing.follow_up_date.isoformat() if listing.follow_up_date else None,
        })
    return _redirect_back(request, reverse("leads"))


def api_lead_contacts(request, listing_id):
    listing = get_object_or_404(BusinessListing, id=listing_id)
    attempts = ContactAttempt.objects.filter(listing=listing).order_by("-contacted_at")[:50]
    data = [
        {
            "id": a.id,
            "channel": a.channel,
            "contacted_at": timezone.localtime(a.contacted_at).strftime("%Y-%m-%d %H:%M"),
            "notes": a.notes,
        }
        for a in attempts
    ]
    return JsonResponse({"contacts": data, "total": len(data)})


def api_notifications(request):
    return JsonResponse({"notifications": _get_notifications()})


@require_POST
def api_update_lead(request, listing_id):
    """AJAX: update lead_status, is_starred, follow_up_date, follow_up_note."""
    import json
    from datetime import date as date_cls
    listing = get_object_or_404(BusinessListing, id=listing_id)
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    valid_statuses = {c[0] for c in BusinessListing.LEAD_STATUS_CHOICES}

    if "lead_status" in data:
        new_status = data["lead_status"]
        if new_status in valid_statuses:
            listing.lead_status = new_status
            # Auto-schedule next follow-up on first transition to following_up
            if new_status == "following_up" and not listing.follow_up_date:
                from datetime import timedelta
                listing.follow_up_date = date_cls.today() + timedelta(days=1)

    if "is_starred" in data:
        listing.is_starred = bool(data["is_starred"])

    if "follow_up_date" in data:
        val = data["follow_up_date"]
        if val:
            try:
                listing.follow_up_date = date_cls.fromisoformat(str(val))
            except ValueError:
                pass
        else:
            listing.follow_up_date = None

    if "follow_up_note" in data:
        listing.follow_up_note = str(data.get("follow_up_note", ""))[:1000]

    listing.save()
    return JsonResponse({
        "ok": True,
        "lead_status": listing.lead_status,
        "is_starred": listing.is_starred,
        "follow_up_date": listing.follow_up_date.isoformat() if listing.follow_up_date else None,
        "follow_up_note": listing.follow_up_note,
    })


# ─── Deduplicate leads ────────────────────────────────────────────────────────

@require_POST
def dedupe_leads(request):
    """Remove duplicate BusinessListing records, keeping the oldest per group."""
    from django.db.models import Min, Count
    mode = request.POST.get("mode", "email")
    removed = 0

    if mode == "email":
        dupes = (
            BusinessListing.objects
            .filter(email__isnull=False)
            .exclude(email="")
            .values("email")
            .annotate(cnt=Count("id"), min_id=Min("id"))
            .filter(cnt__gt=1)
        )
        for d in dupes:
            deleted_count, _ = (
                BusinessListing.objects
                .filter(email=d["email"])
                .exclude(id=d["min_id"])
                .delete()
            )
            removed += deleted_count

    elif mode == "name":
        dupes = (
            BusinessListing.objects
            .values("name")
            .annotate(cnt=Count("id"), min_id=Min("id"))
            .filter(cnt__gt=1)
        )
        for d in dupes:
            deleted_count, _ = (
                BusinessListing.objects
                .filter(name=d["name"])
                .exclude(id=d["min_id"])
                .delete()
            )
            removed += deleted_count

    return JsonResponse({"ok": True, "removed": removed})


# ─── Upload leads ─────────────────────────────────────────────────────────────

def upload_leads(request):
    if request.method == "POST":
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return render(request, "upload_leads.html", {
                "error": "No file selected.",
                "global_stats": _global_stats(),
                "notifications": _get_notifications(),
                "active_page": "leads",
            })

        name = uploaded_file.name.lower()
        try:
            content = uploaded_file.read()
            if name.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(content), dtype=str)
            elif name.endswith((".xlsx", ".xls")):
                df = pd.read_excel(io.BytesIO(content), dtype=str)
            else:
                return render(request, "upload_leads.html", {
                    "error": "Unsupported file format. Please upload CSV, XLSX, or XLS.",
                    "global_stats": _global_stats(),
                    "notifications": _get_notifications(),
                    "active_page": "leads",
                })
        except Exception as exc:
            return render(request, "upload_leads.html", {
                "error": f"Could not read file: {exc}",
                "global_stats": _global_stats(),
                "notifications": _get_notifications(),
                "active_page": "leads",
            })

        # Normalize column names
        df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

        FIELD_ALIASES = {
            "name": ["name", "business_name", "company", "company_name", "business", "title"],
            "email": ["email", "email_address", "e_mail", "mail"],
            "phone": ["phone", "phone_number", "mobile", "telephone", "tel", "contact"],
            "website": ["website", "url", "web", "site", "domain"],
            "address": ["address", "location_address", "full_address", "street"],
            "location": ["location", "city", "city_state", "area", "region", "country"],
            "notes": ["notes", "note", "comments", "remark", "remarks"],
        }

        def find_col(field):
            for alias in FIELD_ALIASES[field]:
                if alias in df.columns:
                    return alias
            return None

        col_map = {field: find_col(field) for field in FIELD_ALIASES}

        def get_val(row, field, default=""):
            col = col_map.get(field)
            if col and col in row and pd.notna(row[col]):
                return str(row[col]).strip()
            return default

        created = 0
        skipped = 0
        source_label = request.POST.get("source_label", "uploaded").strip() or "uploaded"

        for _, row in df.iterrows():
            name_val = get_val(row, "name") or get_val(row, "website") or "Unknown"
            email_val = get_val(row, "email")
            phone_val = get_val(row, "phone")
            website_val = get_val(row, "website")

            # Skip completely empty rows
            if not any([name_val, email_val, phone_val, website_val]):
                skipped += 1
                continue

            # Basic email validation
            if email_val and "@" not in email_val:
                email_val = ""

            try:
                BusinessListing.objects.create(
                    name=name_val[:255],
                    email=email_val[:254] if email_val else "",
                    phone=phone_val[:50] if phone_val else "",
                    website=website_val[:500] if website_val else "",
                    address=get_val(row, "address")[:500],
                    location=get_val(row, "location")[:255] or source_label,
                    search_query=f"uploaded:{source_label}",
                    source="uploaded",
                    notes=get_val(row, "notes"),
                    job=None,
                )
                created += 1
            except Exception:
                skipped += 1

        column_guide = {
            "name": ["name", "business_name", "company", "company_name", "business", "title"],
            "email": ["email", "email_address", "e_mail", "mail"],
            "phone": ["phone", "phone_number", "mobile", "telephone", "tel", "contact"],
            "website": ["website", "url", "web", "site", "domain"],
            "address": ["address", "location_address", "full_address", "street"],
            "location": ["location", "city", "city_state", "area", "region", "country"],
            "notes": ["notes", "note", "comments", "remark", "remarks"],
        }
        return render(request, "upload_leads.html", {
            "success": True,
            "created": created,
            "skipped": skipped,
            "global_stats": _global_stats(),
            "notifications": _get_notifications(),
            "active_page": "leads",
            "detected_columns": list(df.columns),
            "mapped_columns": {k: v for k, v in col_map.items() if v},
            "column_guide": column_guide,
        })

    column_guide = {
        "name": ["name", "business_name", "company", "company_name", "business", "title"],
        "email": ["email", "email_address", "e_mail", "mail"],
        "phone": ["phone", "phone_number", "mobile", "telephone", "tel", "contact"],
        "website": ["website", "url", "web", "site", "domain"],
        "address": ["address", "location_address", "full_address", "street"],
        "location": ["location", "city", "city_state", "area", "region", "country"],
        "notes": ["notes", "note", "comments", "remark", "remarks"],
    }
    return render(request, "upload_leads.html", {
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "leads",
        "column_guide": column_guide,
    })


# ─── Job detail (Maps) ───────────────────────────────────────────────────────

def job_detail(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.source not in {"maps", "bing_maps"}:
        return redirect("search_job_detail", job_id=job_id)
    listings = BusinessListing.objects.filter(job=job).order_by("-scraped_at")[:200]
    logs = job.logs.order_by("-created_at")[:200]
    recent_jobs = list(ScrapeJob.objects.exclude(id=job.id).order_by("-created_at")[:6])
    progress = 0
    if job.max_results:
        progress = min(100, int(job.collected_listings * 100 / job.max_results))
    is_running = job.status in {"queued", "running"}
    auto_campaign = job.campaigns.filter(name__icontains="auto").first() if job.auto_campaign_created else None
    return render(request, "job.html", {
        "job": job,
        "listings": listings,
        "logs": logs,
        "progress": progress,
        "is_running": is_running,
        "can_pause": job.status == "running",
        "can_resume": job.status in {"paused", "failed"},
        "is_paused": job.status == "paused",
        "domains": DOMAINS,
        "recent_jobs": recent_jobs,
        "auto_campaign": auto_campaign,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "dashboard",
    })


# ─── SMTP Profiles ────────────────────────────────────────────────────────────

def smtp_profiles(request):
    from .models import AutoConfig
    global_cfg = AutoConfig.get()

    if request.method == "POST":
        # Read the visible controls directly. Previously these values were
        # copied into hidden fields by JavaScript, so a browser script error
        # could make the Save button silently save the old value.
        def parse_limit(option_name, custom_name, legacy_name, label, current_value):
            option = request.POST.get(option_name)
            if option is None:
                # Accept the old hidden-field names during a deploy where a
                # previously rendered form is still open in the browser.
                option = request.POST.get(legacy_name)
                if option is None:
                    # Do not reset a saved value if a partial/older form
                    # submission does not include this control.
                    return current_value
            option = option.strip()
            if option != "custom":
                try:
                    value = int(option)
                except (TypeError, ValueError):
                    raise ValueError(f"{label} must be a non-negative whole number.")
            else:
                raw_custom = request.POST.get(custom_name, "").strip()
                try:
                    value = int(raw_custom)
                except (TypeError, ValueError):
                    raise ValueError(f"Enter a whole number for the {label.lower()}.")
            if value < 0:
                raise ValueError(f"{label} cannot be negative.")
            return value

        from django.contrib import messages
        try:
            global_limit = parse_limit(
                "global_daily_limit_option",
                "global_daily_limit_custom",
                "global_daily_limit",
                "Global daily limit",
                global_cfg.global_daily_limit,
            )
            rotation_limit = parse_limit(
                "smtp_rotation_limit_option",
                "smtp_rotation_limit_custom",
                "smtp_rotation_limit",
                "SMTP rotation limit",
                global_cfg.smtp_rotation_limit,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("smtp_profiles")

        profile_ids = request.POST.getlist("global_smtp_profiles")
        ids = [int(profile_id) for profile_id in profile_ids if str(profile_id).isdigit()]
        global_cfg.global_daily_limit = global_limit
        global_cfg.smtp_rotation_limit = rotation_limit
        global_cfg.save(update_fields=["global_daily_limit", "smtp_rotation_limit", "updated_at"])
        global_cfg.global_smtp_profiles.set(SmtpProfile.objects.filter(id__in=ids))
        messages.success(request, "Global settings saved successfully.")
        return redirect("smtp_profiles")

    from django.utils import timezone as tz
    sent_today = EmailSend.objects.filter(
        status="sent", sent_at__date=tz.localdate()
    ).count()

    profiles = SmtpProfile.objects.all()
    return render(request, "smtp_profiles.html", {
        "profiles": profiles,
        "global_cfg": global_cfg,
        "global_smtp_selected": set(global_cfg.global_smtp_profiles.values_list("id", flat=True)),
        "global_daily_limit_is_custom": global_cfg.global_daily_limit not in {0, 100, 200, 300, 600, 900, 1200},
        "smtp_rotation_limit_is_custom": global_cfg.smtp_rotation_limit not in {0, 100, 200, 300, 500},
        "sent_today": sent_today,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "smtp",
    })


@require_POST
def create_smtp_profile(request):
    name = request.POST.get("name", "").strip()
    host = request.POST.get("host", "smtp.gmail.com").strip()
    user = request.POST.get("user", "").strip()
    password = request.POST.get("password", "").strip()
    use_tls = request.POST.get("use_tls", "on") == "on"
    try:
        port = int(request.POST.get("port", "587"))
    except ValueError:
        port = 587
    try:
        daily_limit = int(request.POST.get("daily_limit", "300"))
    except ValueError:
        daily_limit = 300
    if name and user:
        SmtpProfile.objects.create(
            name=name, host=host, port=port, user=user,
            password=password, use_tls=use_tls, daily_limit=daily_limit,
        )
    return redirect("smtp_profiles")


def test_smtp_view(request, profile_id):
    """AJAX: send a test email from a saved SMTP profile."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "msg": "POST required."}, status=405)
    from .email_sender import send_test_email
    to_email = request.POST.get("to_email", "").strip()
    if not to_email:
        return JsonResponse({"ok": False, "msg": "Recipient email is required."})
    ok, msg = send_test_email(profile_id, to_email)
    return JsonResponse({"ok": ok, "msg": msg})


def api_email_templates(request):
    """GET: list templates. POST: save new template."""
    from .models import EmailTemplate
    if request.method == "POST":
        import json
        try:
            data = json.loads(request.body)
        except Exception:
            data = {}
        name = (data.get("name") or "").strip()
        subject = (data.get("subject") or "").strip()
        body = (data.get("body") or "").strip()
        industry = (data.get("industry") or "").strip()
        if not name or not subject or not body:
            return JsonResponse({"ok": False, "msg": "name, subject and body are required."})
        tpl = EmailTemplate.objects.create(name=name, subject=subject, body=body, industry=industry)
        return JsonResponse({"ok": True, "id": tpl.id, "name": tpl.name})
    # GET
    templates = list(
        EmailTemplate.objects.values("id", "name", "subject", "body", "industry", "created_at")
    )
    for t in templates:
        t["created_at"] = t["created_at"].strftime("%Y-%m-%d") if t["created_at"] else ""
    return JsonResponse({"templates": templates})


@require_POST
def delete_email_template(request, template_id):
    from .models import EmailTemplate
    tpl = get_object_or_404(EmailTemplate, id=template_id)
    tpl.delete()
    return JsonResponse({"ok": True})


@require_POST
def delete_smtp_profile(request, profile_id):
    profile = get_object_or_404(SmtpProfile, id=profile_id)
    profile.delete()
    return redirect("smtp_profiles")


def api_smtp_profile(request, profile_id):
    profile = get_object_or_404(SmtpProfile, id=profile_id)
    return JsonResponse({
        "host": profile.host,
        "port": profile.port,
        "user": profile.user,
        "use_tls": profile.use_tls,
    })


# ─── Email Campaigns ──────────────────────────────────────────────────────────

def campaigns(request):
    from .models import AutoConfig
    from django.utils import timezone as tz
    global_cfg = AutoConfig.get()
    sent_today = EmailSend.objects.filter(
        status="sent", sent_at__date=tz.localdate()
    ).count()
    campaign_list = EmailCampaign.objects.order_by("-created_at")
    return render(request, "campaigns.html", {
        "campaigns": campaign_list,
        "global_cfg": global_cfg,
        "sent_today": sent_today,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "campaigns",
    })


@require_POST
def update_campaign_template(request, campaign_id):
    """Update subject/body on a campaign — allowed even while sending."""
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    subject = request.POST.get("subject", "").strip()
    body = request.POST.get("body", "").strip()
    if subject:
        campaign.subject = subject
    if body:
        campaign.body = body
    campaign.save(update_fields=["subject", "body", "updated_at"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.POST.get("ajax"):
        return JsonResponse({"ok": True, "msg": "Template updated. Changes apply to next batch."})
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def toggle_ai_variation(request, campaign_id):
    """Toggle AI variation on/off for a campaign."""
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    campaign.ai_variation = not campaign.ai_variation
    campaign.save(update_fields=["ai_variation", "updated_at"])
    return JsonResponse({"ok": True, "ai_variation": campaign.ai_variation})


def new_campaign(request):
    jobs = ScrapeJob.objects.filter(status__in=["completed", "completed_with_errors"]).order_by("-created_at")
    smtp_profiles_qs = SmtpProfile.objects.all()
    prefill_job_id = request.GET.get("job_id", "")
    prefill_listing_ids = request.GET.get("listing_ids", "")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        from_name = request.POST.get("from_name", "").strip()
        from_email = request.POST.get("from_email", "").strip()
        reply_to = request.POST.get("reply_to", "").strip()
        job_filter_id = request.POST.get("job_filter", "")
        listing_ids_raw = request.POST.get("listing_ids", "").strip()

        # Load SMTP credentials from selected saved profile
        profile_id = request.POST.get("smtp_profile_id", "").strip()
        smtp_host = "smtp.gmail.com"
        smtp_port = 587
        smtp_user = ""
        smtp_password = ""
        use_tls = True
        if profile_id:
            try:
                profile = SmtpProfile.objects.get(id=int(profile_id))
                smtp_host = profile.host
                smtp_port = profile.port
                smtp_user = profile.user
                smtp_password = profile.password
                use_tls = profile.use_tls
                if not from_email:
                    from_email = profile.user
            except (SmtpProfile.DoesNotExist, ValueError):
                pass

        job_filter = None
        if job_filter_id:
            try:
                job_filter = ScrapeJob.objects.get(id=int(job_filter_id))
            except (ScrapeJob.DoesNotExist, ValueError):
                pass

        campaign = EmailCampaign.objects.create(
            name=name, subject=subject, body=body,
            from_name=from_name, from_email=from_email, reply_to=reply_to,
            smtp_host=smtp_host, smtp_port=smtp_port, smtp_user=smtp_user,
            smtp_password=smtp_password, use_tls=use_tls,
            job_filter=job_filter,
            daily_limit=0,  # global limit applies
        )

        # If specific listing IDs provided, use only those
        if listing_ids_raw:
            try:
                ids = [int(x.strip()) for x in listing_ids_raw.split(",") if x.strip().isdigit()]
                qs = BusinessListing.objects.filter(id__in=ids).exclude(email="")
            except Exception:
                qs = BusinessListing.objects.none()
        else:
            qs = BusinessListing.objects.exclude(email="")
            if job_filter:
                qs = qs.filter(job=job_filter)

        for listing in qs:
            EmailSend.objects.get_or_create(campaign=campaign, listing=listing)

        # Auto-log contact attempt for all recipients
        for send in campaign.sends.select_related("listing"):
            ContactAttempt.objects.create(
                listing=send.listing,
                channel="email",
                campaign=campaign,
                notes=f"Added to campaign: {campaign.name}",
            )

        return redirect("campaign_detail", campaign_id=campaign.id)

    # Pre-load selected listing IDs if coming from leads page
    prefill_listings = []
    if prefill_listing_ids:
        try:
            ids = [int(x.strip()) for x in prefill_listing_ids.split(",") if x.strip().isdigit()]
            prefill_listings = list(BusinessListing.objects.filter(id__in=ids)[:200])
        except Exception:
            pass

    return render(request, "new_campaign.html", {
        "jobs": jobs,
        "smtp_profiles": smtp_profiles_qs,
        "prefill_job_id": prefill_job_id,
        "prefill_listing_ids": prefill_listing_ids,
        "prefill_listings": prefill_listings,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "campaigns",
    })


def campaign_detail(request, campaign_id):
    from .models import EmailTemplate, AutoConfig
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    sends = campaign.sends.select_related("listing").order_by("-listing__scraped_at")[:500]
    saved_templates = list(EmailTemplate.objects.values("id", "name", "subject", "body", "industry").order_by("-created_at"))
    global_cfg = AutoConfig.get()
    return render(request, "campaign_detail.html", {
        "campaign": campaign,
        "sends": sends,
        "saved_templates": saved_templates,
        "global_cfg": global_cfg,
        "global_stats": _global_stats(),
        "notifications": _get_notifications(),
        "active_page": "campaigns",
    })


@require_POST
def send_campaign_view(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    if campaign.status not in {"draft", "failed", "stopped"}:
        return redirect("campaign_detail", campaign_id=campaign_id)
    launch_campaign(campaign.id)
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def stop_campaign_view(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    if campaign.status == "sending":
        campaign.status = "stopped"
        campaign.save(update_fields=["status", "updated_at"])
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def resend_campaign(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    if campaign.status in {"sending"}:
        return redirect("campaign_detail", campaign_id=campaign_id)

    resend_mode = request.POST.get("resend_mode", "failed")
    if resend_mode == "all":
        campaign.sends.update(status="pending", error="")
    else:
        campaign.sends.filter(status__in=["failed", "skipped"]).update(status="pending", error="")

    campaign.status = "draft"
    campaign.total_sent = 0
    campaign.total_failed = 0
    campaign.total_skipped = 0
    campaign.save(update_fields=["status", "total_sent", "total_failed", "total_skipped", "updated_at"])
    launch_campaign(campaign.id)
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def schedule_campaign(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    from django.utils.dateparse import parse_datetime
    scheduled_str = request.POST.get("scheduled_at", "").strip()
    if scheduled_str:
        try:
            from datetime import datetime
            import pytz
            dt = datetime.strptime(scheduled_str, "%Y-%m-%dT%H:%M")
            try:
                from django.conf import settings as djsettings
                tz = pytz.timezone(djsettings.TIME_ZONE)
                dt = tz.localize(dt)
            except Exception:
                dt = timezone.make_aware(dt)
            campaign.scheduled_at = dt
            campaign.status = "scheduled"
            campaign.save(update_fields=["scheduled_at", "status", "updated_at"])
        except Exception:
            pass
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def unschedule_campaign(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    campaign.scheduled_at = None
    campaign.status = "draft"
    campaign.save(update_fields=["scheduled_at", "status", "updated_at"])
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def delete_campaign(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    campaign.delete()
    return redirect("campaigns")


def download_campaign_report(request, campaign_id):
    """Download a CSV report for a campaign: who was sent, who failed, who was skipped."""
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    sends = campaign.sends.select_related("listing").order_by("status", "listing__name")

    import io
    import csv as csv_module
    output = io.StringIO()
    writer = csv_module.writer(output)
    writer.writerow([
        "Status", "Business Name", "Email", "Phone", "Website",
        "Location", "Sent At", "Error"
    ])
    for send in sends:
        listing = send.listing
        writer.writerow([
            send.status.upper(),
            listing.name,
            listing.email,
            listing.phone or "",
            listing.website or "",
            listing.location or "",
            send.sent_at.strftime("%Y-%m-%d %H:%M:%S") if send.sent_at else "",
            send.error or "",
        ])

    filename = f"campaign_{campaign.id}_report.csv"
    response = HttpResponse(output.getvalue(), content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ─── Job deletion ─────────────────────────────────────────────────────────────

@require_POST
def delete_job(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.status in {"queued", "running"}:
        return _redirect_back(request, reverse("home"))
    source = job.source
    job.delete()
    if source in {"maps", "bing_maps"}:
        return redirect("home")
    return redirect("search_home")


@require_POST
def delete_lead(request, listing_id):
    listing = get_object_or_404(BusinessListing, id=listing_id)
    listing.delete()
    return _redirect_back(request, reverse("leads"))


# ─── API endpoints ────────────────────────────────────────────────────────────

def _job_payload(job):
    return {
        "id": job.id,
        "status": job.status,
        "status_display": job.get_status_display(),
        "source": job.source,
        "source_display": job.get_source_display(),
        "search_phrase": job.search_phrase,
        "domain": job.domain,
        "speed_display": job.get_speed_display(),
        "collected": job.collected_listings,
        "max_results": job.max_results,
        "processed": job.processed_listings,
        "emails_found": job.emails_found,
        "processed_locations": job.processed_locations,
        "total_locations": job.total_locations,
        "last_error": job.last_error,
        "updated_at": timezone.localtime(job.updated_at).strftime("%Y-%m-%d %H:%M:%S"),
    }


def api_recent_jobs(request):
    jobs = list(ScrapeJob.objects.order_by("-created_at")[:10])
    stats = _global_stats()
    payload = []
    for job in jobs:
        listing_count = BusinessListing.objects.filter(job=job).count()
        email_count = BusinessListing.objects.filter(job=job).exclude(email="").count()
        data = _job_payload(job)
        data["collected"] = listing_count
        data["processed"] = listing_count
        data["emails_found"] = email_count
        payload.append(data)
    return JsonResponse({
        "jobs": payload,
        "stats": {
            "total": stats["total_jobs"],
            "queued": ScrapeJob.objects.filter(status="queued").count(),
            "running": ScrapeJob.objects.filter(status="running").count(),
            "paused": ScrapeJob.objects.filter(status="paused").count(),
            "completed": ScrapeJob.objects.filter(status__in=["completed", "completed_with_errors"]).count(),
        },
        "timestamp": timezone.localtime(timezone.now()).strftime("%H:%M:%S"),
    })


def api_job_status(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    listing_count = BusinessListing.objects.filter(job=job).count()
    email_count = BusinessListing.objects.filter(job=job).exclude(email="").count()
    progress = 0
    if job.max_results:
        progress = min(100, int(listing_count * 100 / job.max_results))
    logs = job.logs.order_by("-created_at")[:200]
    listings = BusinessListing.objects.filter(job=job).order_by("-scraped_at")[:100]
    data = _job_payload(job)
    data["collected"] = listing_count
    data["processed"] = listing_count
    data["emails_found"] = email_count
    return JsonResponse({
        "job": data,
        "progress": progress,
        "logs": [{"level": l.level, "message": l.message, "created_at": timezone.localtime(l.created_at).strftime("%Y-%m-%d %H:%M:%S")} for l in logs],
        "listings": [{"name": l.name, "phone": l.phone, "email": l.email, "website": l.website, "location": l.location} for l in listings],
        "timestamp": timezone.localtime(timezone.now()).strftime("%H:%M:%S"),
    })


# ─── Job controls ─────────────────────────────────────────────────────────────

@require_POST
def pause_job(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.status == "running":
        job.status = "paused"
        job.save(update_fields=["status", "updated_at"])
        _log(job, "Job paused by user.")
    return _redirect_back(request, reverse("job_detail", args=[job.id]))


@require_POST
def resume_job(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.status in {"paused", "failed"}:
        resume_mode = request.POST.get("resume_mode", "continue")
        if resume_mode == "restart_all":
            job.processed_locations = 0
        job.status = "running"
        job.last_error = ""
        if resume_mode == "restart_all":
            job.save(update_fields=["status", "last_error", "processed_locations", "updated_at"])
            _log(job, "Job resumed (restart all locations).")
        else:
            job.save(update_fields=["status", "last_error", "updated_at"])
            _log(job, "Job resumed.")
        if not _is_job_active(job_id):
            if job.source == "maps":
                locations = [loc.strip() for loc in job.locations.split(",") if loc.strip()]
                threading.Thread(target=run_scrape, args=(job.id, job.search_phrase, locations, job.domain, job.max_results), daemon=True).start()
            elif job.source == "bing_maps":
                locations = [loc.strip() for loc in job.locations.split(",") if loc.strip()]
                threading.Thread(target=run_bing_maps_scrape, args=(job.id, job.search_phrase, locations, job.max_results), daemon=True).start()
            else:
                threading.Thread(target=run_search_scrape, args=(job.id,), daemon=True).start()
    return _redirect_back(request, reverse("job_detail", args=[job.id]))


@require_POST
def stop_job(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.status in {"running", "paused", "queued"}:
        job.status = "failed"
        job.last_error = "Stopped by user."
        job.save(update_fields=["status", "last_error", "updated_at"])
        _log(job, "Job stopped by user.", level="ERROR")
    if job.source == "maps":
        return _redirect_back(request, reverse("job_detail", args=[job.id]))
    return _redirect_back(request, reverse("search_job_detail", args=[job.id]))


# ─── Downloads ────────────────────────────────────────────────────────────────

def _csv_response(qs_values, filename):
    df = pd.DataFrame(qs_values)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    df.to_csv(path_or_buf=response, index=False)
    return response


def download_csv(request):
    """Download with optional filters applied."""
    qs, _ = _build_leads_qs(request.GET)
    values = list(qs.values(
        "id", "name", "email", "phone", "website", "address",
        "location", "source", "search_query", "scraped_at"
    ))
    return _csv_response(values, "leads_filtered.csv")


def download_phone_csv(request):
    qs, _ = _build_leads_qs(request.GET)
    qs = qs.exclude(phone="")
    return _csv_response([{"name": v[0], "phone": v[1]} for v in qs.values_list("name", "phone")], "leads_phones.csv")


def download_email_csv(request):
    qs, _ = _build_leads_qs(request.GET)
    qs = qs.exclude(email="")
    return _csv_response([{"name": v[0], "email": v[1]} for v in qs.values_list("name", "email")], "leads_emails.csv")


def download_website_csv(request):
    qs, _ = _build_leads_qs(request.GET)
    qs = qs.exclude(website="")
    return _csv_response([{"name": v[0], "website": v[1]} for v in qs.values_list("name", "website")], "leads_websites.csv")


def download_job_csv(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    return _csv_response(list(BusinessListing.objects.filter(job=job).values()), f"job_{job_id}_leads.csv")


def _download_job_column(request, job_id, column, label):
    job = get_object_or_404(ScrapeJob, id=job_id)
    values = list(BusinessListing.objects.filter(job=job).values_list(column, flat=True))
    return _csv_response([{column: v} for v in values], f"job_{job_id}_{label}.csv")


def download_job_phone_csv(request, job_id):
    return _download_job_column(request, job_id, "phone", "phones")


def download_job_email_csv(request, job_id):
    return _download_job_column(request, job_id, "email", "emails")


def download_job_website_csv(request, job_id):
    return _download_job_column(request, job_id, "website", "websites")


# ─── Legacy redirect ──────────────────────────────────────────────────────────

def results(request):
    return redirect("leads")


# ─── AI Feature Endpoints ─────────────────────────────────────────────────────

def api_ai_templates(request):
    search_phrase = request.GET.get("q", "").strip()
    if not search_phrase:
        return JsonResponse({"templates": [], "industry": "default"})
    from .ai_engine import generate_email_templates, detect_industry
    templates = generate_email_templates(search_phrase, count=3)
    industry = detect_industry(search_phrase)
    return JsonResponse({"templates": templates, "industry": industry})


def api_lead_scores(request):
    job_id = request.GET.get("job_id", "")
    qs = BusinessListing.objects.all()
    if job_id:
        qs = qs.filter(job_id=job_id)
    qs = qs[:500]
    from .ai_engine import score_lead, score_lead_label
    scores = []
    for lead in qs:
        s = score_lead({"name": lead.name, "email": lead.email, "phone": lead.phone,
                        "website": lead.website, "address": lead.address})
        scores.append({"id": lead.id, "score": s, "label": score_lead_label(s)})
    return JsonResponse({"scores": scores})


# ─── Create campaign from selected leads ─────────────────────────────────────

@require_POST
def create_campaign_from_selection(request):
    """Create a campaign with only the selected lead IDs."""
    ids_raw = request.POST.get("listing_ids", "").strip()
    if not ids_raw:
        return redirect("leads")
    return redirect(f"/campaigns/new/?listing_ids={ids_raw}")
