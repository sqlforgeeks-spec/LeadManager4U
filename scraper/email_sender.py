"""
SMTP email campaign sender.
Features:
  - Per-campaign daily send limit (pauses campaign when reached)
  - Automatic SMTP rotation across multiple profiles when one hits its daily cap
  - Personalised placeholder rendering
  - Background thread execution
"""
import smtplib
import threading
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from django.utils import timezone

logger = logging.getLogger(__name__)


def _render_body(template, listing):
    """Simple template rendering with {placeholders}."""
    replacements = {
        "{name}": listing.name or "",
        "{email}": listing.email or "",
        "{phone}": listing.phone or "",
        "{website}": listing.website or "",
        "{location}": listing.location or "",
        "{address}": listing.address or "",
    }
    body = template
    for placeholder, value in replacements.items():
        body = body.replace(placeholder, value)
    return body


def _build_smtp(host, port, user, password, use_tls):
    """Open and authenticate an SMTP connection. Raises on failure."""
    if use_tls:
        conn = smtplib.SMTP(host, port, timeout=15)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()
    else:
        conn = smtplib.SMTP_SSL(host, port, timeout=15)
    conn.login(user, password)
    return conn


def _smtp_creds_from_profile(profile):
    return profile.host, profile.port, profile.user, profile.password, profile.use_tls


def _smtp_creds_from_campaign(campaign):
    return campaign.smtp_host, campaign.smtp_port, campaign.smtp_user, campaign.smtp_password, campaign.use_tls


def send_campaign(campaign_id, log_fn=None, should_stop_fn=None):
    """
    Background thread target.  Sends all pending EmailSend records for a campaign.

    Daily-limit behaviour
    ---------------------
    1. If campaign.daily_limit > 0, the campaign stops after that many sends today
       and its status is set to "paused" so the user can resume tomorrow.
    2. Each SMTP profile (extra_smtp_profiles) has its own daily_limit.  When the
       current profile's cap is reached the sender rotates to the next one.
    3. If all profiles are exhausted the campaign pauses until tomorrow.
    """
    from django.db import close_old_connections
    from .models import EmailCampaign, EmailSend, ContactAttempt

    close_old_connections()

    def log(msg, level="INFO"):
        if log_fn:
            try:
                log_fn(msg, level)
            except Exception:
                pass
        logger.info(msg)

    def check_stop():
        if should_stop_fn:
            try:
                return bool(should_stop_fn())
            except Exception:
                pass
        # Also check DB status so the Stop button works immediately
        try:
            return EmailCampaign.objects.filter(id=campaign_id, status="stopped").exists()
        except Exception:
            return False

    try:
        campaign = EmailCampaign.objects.get(id=campaign_id)
    except EmailCampaign.DoesNotExist:
        log(f"Campaign {campaign_id} not found.", "ERROR")
        return

    campaign.status = "sending"
    campaign.save(update_fields=["status", "updated_at"])
    log(f"Campaign '{campaign.name}' started sending.")

    # ── Build the ordered list of SMTP credentials to try ────────────────────
    # First: the campaign's own inline credentials (always present for backward compat)
    # Then: any extra profiles, in the order they were added
    from .models import SmtpProfile as _SmtpProfile
    smtp_slots = []
    if campaign.smtp_user:  # inline credentials exist
        # Try to find a matching saved SmtpProfile so we can apply its daily cap
        primary_cap = 0
        try:
            matched = _SmtpProfile.objects.filter(
                user=campaign.smtp_user,
                host=campaign.smtp_host,
            ).first()
            if matched:
                primary_cap = matched.daily_limit  # 0 = unlimited
        except Exception:
            pass
        smtp_slots.append({
            "label": campaign.smtp_user,
            "creds": _smtp_creds_from_campaign(campaign),
            "cap": primary_cap,
        })
    for profile in campaign.extra_smtp_profiles.order_by("name"):
        smtp_slots.append({
            "label": profile.name,
            "creds": _smtp_creds_from_profile(profile),
            "cap": profile.daily_limit,  # 0 = unlimited
        })

    if not smtp_slots:
        log("No SMTP credentials configured for this campaign.", "ERROR")
        campaign.status = "failed"
        campaign.save(update_fields=["status", "updated_at"])
        return

    # ── Connect to first SMTP slot ────────────────────────────────────────────
    slot_idx = 0
    slot_sent = 0   # emails sent via the current slot today

    def connect_slot(idx):
        s = smtp_slots[idx]
        host, port, user, pwd, tls = s["creds"]
        conn = _build_smtp(host, port, user, pwd, tls)
        log(f"SMTP connected: {s['label']} ({host}:{port})")
        return conn

    try:
        smtp = connect_slot(slot_idx)
    except Exception as exc:
        campaign.status = "failed"
        campaign.save(update_fields=["status", "updated_at"])
        log(f"SMTP connection failed: {exc}", "ERROR")
        return

    pending_sends = EmailSend.objects.filter(
        campaign=campaign,
        status="pending",
    ).select_related("listing")

    total = pending_sends.count()
    log(f"Sending to {total} pending recipients…")

    campaign_limit = campaign.daily_limit  # 0 = unlimited
    campaign_sent_today = 0

    sent = 0
    failed = 0
    skipped = 0
    daily_limit_reached = False

    try:
        for send in pending_sends.iterator():
            if check_stop():
                log("Campaign stopped by user.")
                break

            # ── Campaign-level daily limit check ──────────────────────────────
            if campaign_limit and campaign_sent_today >= campaign_limit:
                log(f"Daily campaign limit of {campaign_limit} reached. Pausing until tomorrow.")
                daily_limit_reached = True
                break

            # ── Per-slot daily limit check / rotation ─────────────────────────
            slot_cap = smtp_slots[slot_idx]["cap"]
            if slot_cap and slot_sent >= slot_cap:
                log(f"SMTP slot '{smtp_slots[slot_idx]['label']}' hit its {slot_cap}/day cap.")
                try:
                    smtp.quit()
                except Exception:
                    pass

                slot_idx += 1
                if slot_idx >= len(smtp_slots):
                    log("All SMTP profiles exhausted for today. Pausing campaign.", "WARNING")
                    daily_limit_reached = True
                    break

                slot_sent = 0
                try:
                    smtp = connect_slot(slot_idx)
                except Exception as exc:
                    log(f"Failed to connect next SMTP slot: {exc}", "ERROR")
                    daily_limit_reached = True
                    break

            listing = send.listing

            if not listing.email:
                send.status = "skipped"
                send.error = "No email address"
                send.save(update_fields=["status", "error"])
                skipped += 1
                continue

            if listing.lead_status in ("converted", "stopped"):
                send.status = "skipped"
                send.error = f"Lead status: {listing.lead_status} — skipped automatically"
                send.save(update_fields=["status", "error"])
                skipped += 1
                continue

            try:
                body = _render_body(campaign.body, listing)
                subject = _render_body(campaign.subject, listing)

                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = (
                    f"{campaign.from_name} <{campaign.from_email}>"
                    if campaign.from_name else campaign.from_email
                )
                msg["To"] = listing.email
                if campaign.reply_to:
                    msg["Reply-To"] = campaign.reply_to

                msg.attach(MIMEText(body, "plain", "utf-8"))

                slot_host, slot_port, slot_user, slot_pwd, slot_tls = smtp_slots[slot_idx]["creds"]
                smtp.sendmail(slot_user or campaign.from_email, [listing.email], msg.as_string())

                send.status = "sent"
                send.sent_at = timezone.now()
                send.save(update_fields=["status", "sent_at"])

                # ── Update lead lifecycle ──────────────────────────────────────
                prior_email_contacts = ContactAttempt.objects.filter(
                    listing=listing, channel="email"
                ).count()
                ContactAttempt.objects.create(
                    listing=listing, channel="email", campaign=campaign,
                    notes=f"Sent via campaign: {campaign.name}",
                )
                if listing.lead_status not in ("converted", "stopped"):
                    from datetime import timedelta
                    interval = 1 if prior_email_contacts == 0 else (7 if prior_email_contacts == 1 else 14)
                    listing.lead_status = "following_up"
                    listing.follow_up_date = timezone.localdate() + timedelta(days=interval)
                    listing.save(update_fields=["lead_status", "follow_up_date"])

                sent += 1
                slot_sent += 1
                campaign_sent_today += 1

                if sent % 10 == 0:
                    log(f"Sent {sent}/{total} (slot: {smtp_slots[slot_idx]['label']}, slot sent today: {slot_sent})")

                time.sleep(0.5)

            except smtplib.SMTPRecipientsRefused:
                send.status = "failed"
                send.error = "Recipient refused"
                send.save(update_fields=["status", "error"])
                failed += 1
            except Exception as exc:
                send.status = "failed"
                send.error = str(exc)[:500]
                send.save(update_fields=["status", "error"])
                failed += 1
                logger.debug(f"Send error to {listing.email}: {exc}")

    finally:
        try:
            smtp.quit()
        except Exception:
            pass

    campaign.total_sent = EmailSend.objects.filter(campaign=campaign, status="sent").count()
    campaign.total_failed = EmailSend.objects.filter(campaign=campaign, status="failed").count()
    campaign.total_skipped = EmailSend.objects.filter(campaign=campaign, status="skipped").count()

    if daily_limit_reached:
        campaign.status = "stopped"
        log(f"Campaign stopped — daily send limit reached. Sent {sent}, failed {failed}, skipped {skipped}. Use 'Resend Failed' or 'Send Now' to continue tomorrow.")
    elif check_stop():
        campaign.status = "stopped"
    else:
        campaign.status = "sent" if (sent > 0 or skipped > 0) and failed == 0 else (
            "failed" if sent == 0 and failed > 0 else "sent"
        )

    campaign.save(update_fields=["status", "total_sent", "total_failed", "total_skipped", "updated_at"])
    log(f"Campaign complete: {sent} sent, {failed} failed, {skipped} skipped.")


def send_test_email(profile_id, to_email, log_fn=None):
    """
    Send a single test email using a saved SMTP profile.
    Returns (success: bool, message: str).
    """
    from django.db import close_old_connections
    from .models import SmtpProfile
    close_old_connections()
    try:
        profile = SmtpProfile.objects.get(id=profile_id)
    except SmtpProfile.DoesNotExist:
        return False, "SMTP profile not found."

    try:
        conn = _build_smtp(profile.host, profile.port, profile.user, profile.password, profile.use_tls)
    except Exception as exc:
        return False, f"Connection failed: {exc}"

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "✅ Test email from LeadManager4U"
        msg["From"] = profile.user
        msg["To"] = to_email
        body = (
            f"This is a test email sent from LeadManager4U.\n\n"
            f"SMTP Profile: {profile.name}\n"
            f"Host: {profile.host}:{profile.port}\n"
            f"User: {profile.user}\n\n"
            f"If you received this, your SMTP profile is working correctly! ✅"
        )
        msg.attach(MIMEText(body, "plain", "utf-8"))
        conn.sendmail(profile.user, [to_email], msg.as_string())
        conn.quit()
        return True, f"Test email sent to {to_email} via {profile.name}."
    except Exception as exc:
        try:
            conn.quit()
        except Exception:
            pass
        return False, f"Send failed: {exc}"


def launch_campaign(campaign_id, log_fn=None, should_stop_fn=None):
    """Spawn campaign in a background daemon thread."""
    t = threading.Thread(
        target=send_campaign,
        args=(campaign_id, log_fn, should_stop_fn),
        daemon=True,
    )
    t.start()
    return t
