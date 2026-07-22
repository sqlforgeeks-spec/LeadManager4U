from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.db import close_old_connections, connection
from django.db.utils import OperationalError
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import BusinessListing, ScrapeJob, JobLog
from .scraper import scrape_google_maps, StopScrape, create_shared_drivers
from .domains import DOMAINS
import threading
import queue
import time
import pandas as pd

MAX_RESULTS_CAP = 5000
ACTIVE_JOBS = set()
ACTIVE_JOBS_LOCK = threading.Lock()


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

def home(request):
    if request.method == 'POST':
        search_phrase = request.POST.get('search_phrase', '').strip()
        locations_raw = request.POST.get('locations', '')
        domain = request.POST.get('domain', 'com').strip()
        max_results_raw = request.POST.get('max_results', '1000')
        speed = request.POST.get('speed', 'normal').strip()

        try:
            max_results = int(max_results_raw)
        except ValueError:
            max_results = 1000

        max_results = max(1, min(max_results, MAX_RESULTS_CAP))
        locations = [loc.strip() for loc in locations_raw.split(',') if loc.strip()]
        if speed not in {"slow", "normal", "fast"}:
            speed = "normal"

        job = ScrapeJob.objects.create(
            status="queued",
            search_phrase=search_phrase,
            domain=domain,
            locations=locations_raw,
            max_results=max_results,
            total_locations=len(locations),
            speed=speed,
        )

        threading.Thread(
            target=run_scrape,
            args=(job.id, search_phrase, locations, domain, max_results),
            daemon=True,
        ).start()
        return redirect('job_detail', job_id=job.id)

    recent_jobs = list(ScrapeJob.objects.order_by('-created_at')[:10])
    refresh_home = any(job.status in {"queued", "running", "paused"} for job in recent_jobs)
    home_stats = {
        "total": ScrapeJob.objects.count(),
        "queued": ScrapeJob.objects.filter(status="queued").count(),
        "running": ScrapeJob.objects.filter(status="running").count(),
        "paused": ScrapeJob.objects.filter(status="paused").count(),
        "completed": ScrapeJob.objects.filter(status__in=["completed", "completed_with_errors"]).count(),
    }
    return render(
        request,
        'home.html',
        {
            'domains': DOMAINS,
            'recent_jobs': recent_jobs,
            'refresh_home': refresh_home,
            'home_stats': home_stats,
        },
    )

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
        if job.total_locations:
            job.total_locations = max(job.total_locations, len(locations))
        else:
            job.total_locations = len(locations)
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
                normalized = maps_url.split("?", 1)[0].rstrip("/")
                seen_keys.add(normalized)
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
                        job.save(update_fields=[
                            "total_results",
                            "collected_listings",
                            "processed_listings",
                            "emails_found",
                            "updated_at",
                        ])
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
                            job=job,
                            name=res.get('name', ''),
                            phone=res.get('phone', ''),
                            email=res.get('email', ''),
                            website=res.get('website', ''),
                            maps_url=maps_url,
                            search_query=search_phrase,
                            location=loc,
                        )
                        stats["total_results"] += 1
                        stats["collected_listings"] = max(stats["collected_listings"], stats["total_results"])
                        stats["processed_listings"] = max(stats["processed_listings"], stats["total_results"])
                    elif item["type"] == "collected":
                        stats["collected_listings"] = max(stats["collected_listings"], item["count"])
                    elif item["type"] == "processed":
                        stats["processed_listings"] = max(stats["processed_listings"], item["count"])
                    elif item["type"] == "email_update":
                        domain = item["domain"]
                        email = item["email"]
                        updated = _db_retry(
                            BusinessListing.objects.filter(
                                job=job,
                                email="",
                                website__icontains=domain,
                            ).update,
                            email=email,
                        ) or 0
                        stats["emails_found"] += updated

                    if time.time() - last_save > 2:
                        job.total_results = stats["total_results"]
                        job.collected_listings = stats["collected_listings"]
                        job.processed_listings = stats["processed_listings"]
                        job.emails_found = stats["emails_found"]
                        _db_retry(
                            job.save,
                            update_fields=[
                                "total_results",
                                "collected_listings",
                                "processed_listings",
                                "emails_found",
                                "updated_at",
                            ],
                        )
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
            _db_retry(
                job.save,
                update_fields=[
                    "total_results",
                    "collected_listings",
                    "processed_listings",
                    "emails_found",
                    "updated_at",
                ],
            )
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
                    search_phrase,
                    loc,
                    domain,
                    max_results=remaining,
                    log_fn=lambda message: _log(job, message),
                    should_pause_fn=should_pause,
                    should_stop_fn=should_stop,
                    on_listings_update=lambda count, new: db_queue.put({"type": "collected", "count": count}),
                    on_detail_progress=lambda processed, total: db_queue.put({"type": "processed", "count": processed}),
                    on_result=lambda res, location=loc: db_queue.put({"type": "result", "result": res, "location": location}),
                    on_email_update=lambda domain, email: db_queue.put({"type": "email_update", "domain": domain, "email": email}),
                    speed=job.speed,
                    seed_seen=set(seen_keys),
                    email_cache=email_cache,
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

        if total_results == 0 and had_errors:
            job.status = "failed"
        else:
            job.status = "completed_with_errors" if had_errors else "completed"

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

def results(request):
    listings = BusinessListing.objects.all().order_by('-scraped_at')
    recent_jobs = list(ScrapeJob.objects.order_by('-created_at')[:8])
    stats = {
        "total": BusinessListing.objects.count(),
        "with_phone": BusinessListing.objects.exclude(phone="").count(),
        "with_email": BusinessListing.objects.exclude(email="").count(),
        "with_website": BusinessListing.objects.exclude(website="").count(),
    }
    return render(
        request,
        'result.html',
        {
            'listings': listings,
            'domains': DOMAINS,
            'recent_jobs': recent_jobs,
            'stats': stats,
        },
    )

def download_csv(request):
    listings = BusinessListing.objects.all().values()
    df = pd.DataFrame(listings)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="listings.csv"'
    df.to_csv(path_or_buf=response, index=False)
    return response


def _download_all_column(column):
    values = list(BusinessListing.objects.values_list(column, flat=True))
    df = pd.DataFrame({column: values})
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="listings_{column}.csv"'
    df.to_csv(path_or_buf=response, index=False)
    return response


def download_phone_csv(request):
    return _download_all_column("phone")


def download_email_csv(request):
    return _download_all_column("email")


def download_website_csv(request):
    return _download_all_column("website")


def _job_payload(job):
    return {
        "id": job.id,
        "status": job.status,
        "status_display": job.get_status_display(),
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
    jobs = list(ScrapeJob.objects.order_by('-created_at')[:10])
    stats = {
        "total": ScrapeJob.objects.count(),
        "queued": ScrapeJob.objects.filter(status="queued").count(),
        "running": ScrapeJob.objects.filter(status="running").count(),
        "paused": ScrapeJob.objects.filter(status="paused").count(),
        "completed": ScrapeJob.objects.filter(status__in=["completed", "completed_with_errors"]).count(),
    }
    payload = []
    for job in jobs:
        listing_count = BusinessListing.objects.filter(job=job).count()
        email_count = BusinessListing.objects.filter(job=job).exclude(email="").count()
        data = _job_payload(job)
        data["collected"] = listing_count
        data["processed"] = listing_count
        data["emails_found"] = email_count
        payload.append(data)
    return JsonResponse(
        {
            "jobs": payload,
            "stats": stats,
            "timestamp": timezone.localtime(timezone.now()).strftime("%H:%M:%S"),
        }
    )


def api_job_status(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    listing_count = BusinessListing.objects.filter(job=job).count()
    email_count = BusinessListing.objects.filter(job=job).exclude(email="").count()
    progress = 0
    if job.max_results:
        progress = min(100, int(listing_count * 100 / job.max_results))
    logs = job.logs.order_by('-created_at')[:200]
    listings = BusinessListing.objects.filter(job=job).order_by('-scraped_at')[:200]
    data = _job_payload(job)
    data["collected"] = listing_count
    data["processed"] = listing_count
    data["emails_found"] = email_count
    return JsonResponse(
        {
            "job": data,
            "progress": progress,
            "logs": [
                {
                    "level": log.level,
                    "message": log.message,
                    "created_at": timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M:%S"),
                }
                for log in logs
            ],
            "listings": [
                {
                    "name": listing.name,
                    "phone": listing.phone,
                    "email": listing.email,
                    "website": listing.website,
                    "location": listing.location,
                }
                for listing in listings
            ],
            "timestamp": timezone.localtime(timezone.now()).strftime("%H:%M:%S"),
        }
    )


def job_detail(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    listings = BusinessListing.objects.filter(job=job).order_by('-scraped_at')[:200]
    logs = job.logs.order_by('-created_at')[:200]
    recent_jobs = list(ScrapeJob.objects.exclude(id=job.id).order_by('-created_at')[:6])

    progress = 0
    if job.max_results:
        progress = min(100, int(job.collected_listings * 100 / job.max_results))

    is_running = job.status in {"queued", "running"}
    return render(
        request,
        'job.html',
        {
            'job': job,
            'listings': listings,
            'logs': logs,
            'progress': progress,
            'is_running': is_running,
            'can_pause': job.status == "running",
            'can_resume': job.status in {"paused", "failed"},
            'is_paused': job.status == "paused",
            'domains': DOMAINS,
            'recent_jobs': recent_jobs,
        },
    )


def download_job_csv(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    listings = BusinessListing.objects.filter(job=job).values()
    df = pd.DataFrame(listings)
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename=\"job_{job_id}_listings.csv\"'
    df.to_csv(path_or_buf=response, index=False)
    return response


def _download_job_column(request, job_id, column):
    job = get_object_or_404(ScrapeJob, id=job_id)
    values = list(BusinessListing.objects.filter(job=job).values_list(column, flat=True))
    df = pd.DataFrame({column: values})
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename=\"job_{job_id}_{column}.csv\"'
    df.to_csv(path_or_buf=response, index=False)
    return response


def download_job_phone_csv(request, job_id):
    return _download_job_column(request, job_id, "phone")


def download_job_email_csv(request, job_id):
    return _download_job_column(request, job_id, "email")


def download_job_website_csv(request, job_id):
    return _download_job_column(request, job_id, "website")


@require_POST
def pause_job(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.status == "running":
        job.status = "paused"
        job.save(update_fields=["status", "updated_at"])
        _log(job, "Job paused by user.")
    return redirect('job_detail', job_id=job.id)


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
            _log(job, "Job resumed by user (restart all locations).")
        else:
            job.save(update_fields=["status", "last_error", "updated_at"])
            _log(job, "Job resumed by user.")
        if not _is_job_active(job_id):
            locations = [loc.strip() for loc in job.locations.split(',') if loc.strip()]
            threading.Thread(
                target=run_scrape,
                args=(job.id, job.search_phrase, locations, job.domain, job.max_results),
                daemon=True,
            ).start()
    return redirect('job_detail', job_id=job.id)


@require_POST
def stop_job(request, job_id):
    job = get_object_or_404(ScrapeJob, id=job_id)
    if job.status in {"running", "paused", "queued"}:
        job.status = "failed"
        job.last_error = "Stopped by user."
        job.save(update_fields=["status", "last_error", "updated_at"])
        _log(job, "Job stopped by user.", level="ERROR")
    return _redirect_back(request, reverse('job_detail', args=[job.id]))
