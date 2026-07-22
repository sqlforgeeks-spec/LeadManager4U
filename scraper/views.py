from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.db import close_old_connections, connection
from django.db.utils import OperationalError
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import BusinessListing, ScrapeJob, JobLog, EmailCampaign, EmailSend
from .scraper import scrape_google_maps, StopScrape, create_shared_drivers
from .search_scraper import scrape_search_engine, StopScrape as SearchStopScrape
from .email_sender import launch_campaign
from .domains import DOMAINS
import threading
import queue
import time
import pandas as pd

MAX_RESULTS_CAP = 5000
ACTIVE_JOBS = set()
ACTIVE_JOBS_LOCK = threading.Lock()


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
    return {
        "total_jobs": ScrapeJob.objects.count(),
        "total_leads": BusinessListing.objects.count(),
        "leads_with_email": BusinessListing.objects.exclude(email="").count(),
        "leads_with_phone": BusinessListing.objects.exclude(phone="").count(),
        "active_jobs": ScrapeJob.objects.filter(status__in=["queued", "running"]).count(),
        "campaigns": EmailCampaign.objects.count(),
    }


# ─── Dashboard ───────────────────────────────────────────────────────────────

def home(request):
    if request.method == "POST":
        return _start_maps_job(request)

    recent_jobs = list(ScrapeJob.objects.order_by("-created_at")[:12])
    refresh_home = any(j.status in {"queued", "running", "paused"} for j in recent_jobs)
    job_stats = {
        "total": ScrapeJob.objects.count(),
        "queued": ScrapeJob.objects.filter(status="queued").count(),
        "running": ScrapeJob.objects.filter(status="running").count(),
        "paused": ScrapeJob.objects.filter(status="paused").count(),
        "completed": ScrapeJob.objects.filter(status__in=["completed", "completed_with_errors"]).count(),
    }
    return render(request, "home.html", {
        "domains": DOMAINS,
        "recent_jobs": recent_jobs,
        "refresh_home": refresh_home,
        "job_stats": job_stats,
        "global_stats": _global_stats(),
        "active_page": "dashboard",
    })


def _start_maps_job(request):
    search_phrase = request.POST.get("search_phrase", "").strip()
    locations_raw = request.POST.get("locations", "")
    domain = request.POST.get("domain", "com").strip()
    speed = request.POST.get("speed", "normal").strip()
    try:
        max_results = min(MAX_RESULTS_CAP, max(1, int(request.POST.get("max_results", "1000"))))
    except ValueError:
        max_results = 1000
    if speed not in {"slow", "normal", "fast"}:
        speed = "normal"

    locations = [loc.strip() for loc in locations_raw.split(",") if loc.strip()]
    job = ScrapeJob.objects.create(
        status="queued",
        source="maps",
        search_phrase=search_phrase,
        domain=domain,
        locations=locations_raw,
        max_results=max_results,
        total_locations=len(locations),
        speed=speed,
    )
    threading.Thread(target=run_scrape, args=(job.id, search_phrase, locations, domain, max_results), daemon=True).start()
    return redirect("job_detail", job_id=job.id)


# ─── Maps Scraping ───────────────────────────────────────────────────────────

def run_scrape(job_id, search_phrase, locations, domain, max_results):
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


# ─── Search Engine Scraping ───────────────────────────────────────────────────

def search_home(request):
    """Page for search-engine scraping jobs."""
    if request.method == "POST":
        return _start_search_job(request)

    recent_jobs = list(ScrapeJob.objects.exclude(source="maps").order_by("-created_at")[:12])
    return render(request, "search_home.html", {
        "recent_jobs": recent_jobs,
        "global_stats": _global_stats(),
        "active_page": "search",
    })


def _start_search_job(request):
    search_phrase = request.POST.get("search_phrase", "").strip()
    location = request.POST.get("location", "").strip()
    engine = request.POST.get("engine", "google").strip()
    speed = request.POST.get("speed", "normal").strip()
    if engine not in {"google", "bing", "yahoo", "duckduckgo", "yandex"}:
        engine = "google"
    if speed not in {"slow", "normal", "fast"}:
        speed = "normal"
    try:
        max_results = min(500, max(1, int(request.POST.get("max_results", "100"))))
    except ValueError:
        max_results = 100

    job = ScrapeJob.objects.create(
        status="queued",
        source=engine,
        search_phrase=search_phrase,
        locations=location,
        max_results=max_results,
        total_locations=1 if location else 0,
        speed=speed,
    )
    threading.Thread(target=run_search_scrape, args=(job.id,), daemon=True).start()
    return redirect("search_job_detail", job_id=job.id)


def run_search_scrape(job_id):
    _set_job_active(job_id, True)
    close_old_connections()
    try:
        job = ScrapeJob.objects.get(id=job_id)
    except ScrapeJob.DoesNotExist:
        _set_job_active(job_id, False)
        return

    email_cache = {}
    email_cache_lock = __import__("threading").Lock()

    try:
        job.status = "running"
        job.save(update_fields=["status", "updated_at"])
        _log(job, f"Search job started: '{job.search_phrase}' on {job.source} (target {job.max_results})")

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
            job.collected_listings = len(seen)
            if record.get("email"):
                job.emails_found = BusinessListing.objects.filter(job=job).exclude(email="").count()
            job.save(update_fields=["collected_listings", "emails_found", "updated_at"])

        speed_to_workers = {"slow": 3, "normal": 6, "fast": 10}
        max_email_workers = speed_to_workers.get(job.speed, 6)

        results = scrape_search_engine(
            search_phrase=job.search_phrase,
            location=job.locations,
            engine=job.source,
            max_results=job.max_results,
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
        "active_page": "search",
    })


# ─── Leads ───────────────────────────────────────────────────────────────────

def leads(request):
    qs = BusinessListing.objects.all()

    source_filter = request.GET.get("source", "")
    has_email = request.GET.get("has_email", "")
    has_phone = request.GET.get("has_phone", "")
    query = request.GET.get("q", "").strip()
    job_id = request.GET.get("job_id", "")

    if source_filter:
        qs = qs.filter(source=source_filter)
    if has_email == "1":
        qs = qs.exclude(email="")
    if has_email == "0":
        qs = qs.filter(email="")
    if has_phone == "1":
        qs = qs.exclude(phone="")
    if job_id:
        qs = qs.filter(job_id=job_id)
    if query:
        from django.db.models import Q
        qs = qs.filter(
            Q(name__icontains=query) |
            Q(email__icontains=query) |
            Q(phone__icontains=query) |
            Q(website__icontains=query) |
            Q(location__icontains=query)
        )

    listings = qs.order_by("-scraped_at")[:2000]
    stats = {
        "total": BusinessListing.objects.count(),
        "with_phone": BusinessListing.objects.exclude(phone="").count(),
        "with_email": BusinessListing.objects.exclude(email="").count(),
        "with_website": BusinessListing.objects.exclude(website="").count(),
    }
    jobs_for_filter = ScrapeJob.objects.order_by("-created_at")[:50]
    return render(request, "leads.html", {
        "listings": listings,
        "stats": stats,
        "jobs_for_filter": jobs_for_filter,
        "filters": {
            "source": source_filter,
            "has_email": has_email,
            "has_phone": has_phone,
            "q": query,
            "job_id": job_id,
        },
        "global_stats": _global_stats(),
        "active_page": "leads",
    })


# ─── Job detail (Maps) ───────────────────────────────────────────────────────

def job_detail(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    # Redirect search engine jobs to their own detail page
    if job.source != "maps":
        return redirect("search_job_detail", job_id=job_id)
    listings = BusinessListing.objects.filter(job=job).order_by("-scraped_at")[:200]
    logs = job.logs.order_by("-created_at")[:200]
    recent_jobs = list(ScrapeJob.objects.exclude(id=job.id).order_by("-created_at")[:6])
    progress = 0
    if job.max_results:
        progress = min(100, int(job.collected_listings * 100 / job.max_results))
    is_running = job.status in {"queued", "running"}
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
        "global_stats": _global_stats(),
        "active_page": "dashboard",
    })


# ─── Email Campaigns ──────────────────────────────────────────────────────────

def campaigns(request):
    campaign_list = EmailCampaign.objects.order_by("-created_at")
    return render(request, "campaigns.html", {
        "campaigns": campaign_list,
        "global_stats": _global_stats(),
        "active_page": "campaigns",
    })


def new_campaign(request):
    jobs = ScrapeJob.objects.filter(status__in=["completed", "completed_with_errors"]).order_by("-created_at")
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        subject = request.POST.get("subject", "").strip()
        body = request.POST.get("body", "").strip()
        from_name = request.POST.get("from_name", "").strip()
        from_email = request.POST.get("from_email", "").strip()
        reply_to = request.POST.get("reply_to", "").strip()
        smtp_host = request.POST.get("smtp_host", "smtp.gmail.com").strip()
        smtp_user = request.POST.get("smtp_user", "").strip()
        smtp_password = request.POST.get("smtp_password", "").strip()
        use_tls = request.POST.get("use_tls", "on") == "on"
        job_filter_id = request.POST.get("job_filter", "")
        try:
            smtp_port = int(request.POST.get("smtp_port", "587"))
        except ValueError:
            smtp_port = 587

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
        )

        # Pre-populate EmailSend records
        qs = BusinessListing.objects.exclude(email="")
        if job_filter:
            qs = qs.filter(job=job_filter)
        for listing in qs:
            EmailSend.objects.get_or_create(campaign=campaign, listing=listing)

        return redirect("campaign_detail", campaign_id=campaign.id)

    return render(request, "new_campaign.html", {
        "jobs": jobs,
        "global_stats": _global_stats(),
        "active_page": "campaigns",
    })


def campaign_detail(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    sends = campaign.sends.select_related("listing").order_by("-listing__scraped_at")[:500]
    return render(request, "campaign_detail.html", {
        "campaign": campaign,
        "sends": sends,
        "global_stats": _global_stats(),
        "active_page": "campaigns",
    })


@require_POST
def send_campaign_view(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    if campaign.status not in {"draft", "failed"}:
        return redirect("campaign_detail", campaign_id=campaign_id)

    def log_fn(msg, level="INFO"):
        pass  # Campaigns don't have JobLog; could extend later

    launch_campaign(campaign.id, log_fn=log_fn)
    return redirect("campaign_detail", campaign_id=campaign_id)


@require_POST
def delete_campaign(request, campaign_id):
    campaign = get_object_or_404(EmailCampaign, id=campaign_id)
    campaign.delete()
    return redirect("campaigns")


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
    return _csv_response(list(BusinessListing.objects.all().values()), "leads_all.csv")


def download_phone_csv(request):
    return _csv_response([{"phone": v} for v in BusinessListing.objects.values_list("phone", flat=True)], "leads_phones.csv")


def download_email_csv(request):
    return _csv_response([{"email": v} for v in BusinessListing.objects.values_list("email", flat=True)], "leads_emails.csv")


def download_website_csv(request):
    return _csv_response([{"website": v} for v in BusinessListing.objects.values_list("website", flat=True)], "leads_websites.csv")


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
