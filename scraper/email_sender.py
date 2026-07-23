"""
SMTP email campaign sender.
Features:
  - Global daily send limit (across all campaigns combined)
  - Per-campaign daily send limit
  - Automatic SMTP rotation: global pool → per-campaign extras → primary creds
  - Per-profile daily caps trigger rotation to next profile
  - AI variation: subtly varies subject/body per email to avoid spam filters
  - Live template refresh: re-reads subject/body from DB every 25 sends
  - Personalised placeholder rendering
  - Background thread execution
"""
import random
import smtplib
import threading
import time
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from django.utils import timezone

logger = logging.getLogger(__name__)


# ─── Placeholder rendering ────────────────────────────────────────────────────

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


# ─── AI Variation Engine ──────────────────────────────────────────────────────

_GREETINGS = [
    "Hi", "Hello", "Hey", "Dear", "Good day",
    "Greetings", "Hi there", "Hello there",
]

_SUBJECT_PREFIXES = [
    "", "", "",  # no prefix most of the time
    "Quick question — ", "Just a thought — ", "Reaching out — ",
    "One thing — ", "A quick note — ", "Friendly nudge — ",
]

_CLOSING_LINES = [
    "Looking forward to hearing from you.",
    "Hope to connect soon.",
    "Would love to chat if you're open to it.",
    "Happy to jump on a quick call anytime.",
    "Feel free to reply whenever you get a chance.",
    "Let me know if this is something you'd find useful.",
    "No pressure — just thought it was worth a mention.",
    "Would appreciate your thoughts on this.",
]

_CONNECTORS = [
    "I came across", "I noticed", "I found", "I discovered",
    "I recently came across", "I stumbled upon",
]


def vary_subject(subject: str) -> str:
    """Return a slightly varied version of the subject line."""
    prefix = random.choice(_SUBJECT_PREFIXES)
    # Occasionally lowercase first word after prefix
    if prefix and subject:
        return prefix + subject[0].lower() + subject[1:]
    return prefix + subject


def vary_body(body: str) -> str:
    """Return a slightly varied version of the email body."""
    lines = body.split("\n")
    if not lines:
        return body

    # Vary the opening greeting (first line) if it starts with a known greeting
    first_line = lines[0]
    for greeting in sorted(_GREETINGS, key=len, reverse=True):
        if first_line.lower().startswith(greeting.lower()):
            new_greeting = random.choice(_GREETINGS)
            lines[0] = new_greeting + first_line[len(greeting):]
            break

    # Vary connector phrases in the body
    body_text = "\n".join(lines)
    for connector in _CONNECTORS:
        if connector.lower() in body_text.lower():
            new_connector = random.choice(_CONNECTORS)
            # Case-insensitive replace first occurrence
            idx = body_text.lower().find(connector.lower())
            body_text = body_text[:idx] + new_connector + body_text[idx + len(connector):]
            break

    # Append or swap closing line if body ends with one of our known phrases
    for closing in _CLOSING_LINES:
        if body_text.rstrip().endswith(closing.rstrip(".")):
            new_closing = random.choice(_CLOSING_LINES)
            body_text = body_text.rstrip()[: -len(closing.rstrip("."))] + new_closing
            break

    return body_text


# ─── SMTP helpers ─────────────────────────────────────────────────────────────

def _build_smtp(host, port, user, password, use_tls):
    """Open and authenticate an SMTP connection. Raises on failure."""
    if use_tls:
        conn = smtplib.SMTP(host, port, timeout=15)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()
    else:
        conn = smtplib.SMTP_SSL(host, port, timeout=15)
    # Only authenticate when credentials are present — some relays don't need auth
    if user and password:
        conn.login(user, password)
    return conn


def _smtp_creds_from_profile(profile):
    return profile.host, profile.port, profile.user, profile.password, profile.use_tls


def _smtp_creds_from_campaign(campaign):
    return campaign.smtp_host, campaign.smtp_port, campaign.smtp_user, campaign.smtp_password, campaign.use_tls


# ─── Main sender ──────────────────────────────────────────────────────────────

def send_campaign(campaign_id, log_fn=None, should_stop_fn=None):
    """
    Background thread target. Sends all pending EmailSend records for a campaign.

    Limit / rotation priority:
    1. Global daily limit (AutoConfig.global_daily_limit) — across ALL campaigns today.
       Checked at start and every email.
    2. Per-campaign daily limit (campaign.daily_limit) — this campaign only.
    3. SMTP rotation order:
         a. Campaign primary SMTP credentials (with per-profile daily cap from SmtpProfile match)
         b. Campaign extra_smtp_profiles (with per-profile caps)
         c. Global SMTP pool from AutoConfig.global_smtp_profiles (fallback / augment)
    4. When all SMTP slots exhausted → campaign stops.
    """
    from django.db import close_old_connections
    from .models import EmailCampaign, EmailSend, ContactAttempt, AutoConfig

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

    # ── Global config ────────────────────────────────────────────────────────
    global_cfg = AutoConfig.get()
    global_limit = global_cfg.global_daily_limit  # 0 = no global cap

    def _count_global_sent_today():
        """Count all emails sent today across ALL campaigns."""
        try:
            return EmailSend.objects.filter(
                status="sent",
                sent_at__date=timezone.localdate(),
            ).count()
        except Exception:
            return 0

    if global_limit:
        already_sent_today = _count_global_sent_today()
        if already_sent_today >= global_limit:
            log(f"Global daily limit of {global_limit} already reached today ({already_sent_today} sent). Campaign stopped.", "WARNING")
            campaign.status = "stopped"
            campaign.save(update_fields=["status", "updated_at"])
            return

    # ── Build SMTP slot list ─────────────────────────────────────────────────
    # Priority: primary creds → per-campaign extras → global pool
    from .models import SmtpProfile as _SmtpProfile
    smtp_slots = []

    if campaign.smtp_user:
        primary_cap = 0
        try:
            matched = _SmtpProfile.objects.filter(
                user=campaign.smtp_user, host=campaign.smtp_host
            ).first()
            if matched:
                primary_cap = matched.daily_limit
        except Exception:
            pass
        smtp_slots.append({
            "label": campaign.smtp_user,
            "creds": _smtp_creds_from_campaign(campaign),
            "cap": primary_cap,
        })

    # Per-campaign rotation profiles
    seen_ids = set()
    for profile in campaign.extra_smtp_profiles.order_by("name"):
        seen_ids.add(profile.id)
        smtp_slots.append({
            "label": profile.name,
            "creds": _smtp_creds_from_profile(profile),
            "cap": profile.daily_limit,
        })

    # Global SMTP pool — add profiles not already in the list
    try:
        for profile in global_cfg.global_smtp_profiles.order_by("name"):
            if profile.id not in seen_ids:
                smtp_slots.append({
                    "label": f"[Global] {profile.name}",
                    "creds": _smtp_creds_from_profile(profile),
                    "cap": profile.daily_limit,
                })
    except Exception:
        pass

    if not smtp_slots:
        log("No SMTP credentials configured for this campaign.", "ERROR")
        campaign.status = "failed"
        campaign.save(update_fields=["status", "updated_at"])
        return

    # ── Rotation limit: 0 = auto (use per-profile cap), >0 = rotate after N emails ──
    rotation_limit = global_cfg.smtp_rotation_limit  # 0 = auto

    # ── Connect first SMTP slot ───────────────────────────────────────────────
    slot_idx = 0
    slot_sent = 0

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
        campaign=campaign, status="pending"
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

            # ── Global daily limit check ──────────────────────────────────────
            if global_limit and (sent % 10 == 0):
                total_today = _count_global_sent_today()
                if total_today >= global_limit:
                    log(f"Global daily limit of {global_limit} reached ({total_today} sent today across all campaigns). Stopping.", "WARNING")
                    daily_limit_reached = True
                    break

            # ── Per-campaign daily limit check ────────────────────────────────
            if campaign_limit and campaign_sent_today >= campaign_limit:
                log(f"Campaign daily limit of {campaign_limit} reached. Stopping.")
                daily_limit_reached = True
                break

            # ── Per-slot daily limit / rotation ───────────────────────────────
            # rotation_limit > 0 → rotate after that many emails (overrides per-profile cap)
            # rotation_limit == 0 → auto: use each profile's own daily_limit
            effective_cap = rotation_limit if rotation_limit > 0 else smtp_slots[slot_idx]["cap"]
            slot_cap = effective_cap
            if slot_cap and slot_sent >= slot_cap:
                log(f"SMTP '{smtp_slots[slot_idx]['label']}' hit its {slot_cap}/day cap.")
                try:
                    smtp.quit()
                except Exception:
                    pass

                slot_idx += 1
                if slot_idx >= len(smtp_slots):
                    log("All SMTP profiles exhausted for today. Stopping campaign.", "WARNING")
                    daily_limit_reached = True
                    break

                slot_sent = 0
                try:
                    smtp = connect_slot(slot_idx)
                except Exception as exc:
                    log(f"Failed to connect next SMTP slot: {exc}", "ERROR")
                    daily_limit_reached = True
                    break

            # ── Live template refresh (every 25 sends) ────────────────────────
            if sent > 0 and sent % 25 == 0:
                try:
                    campaign.refresh_from_db(fields=["subject", "body", "ai_variation", "send_mode"])
                except Exception:
                    pass

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

                # ── AI variation ──────────────────────────────────────────────
                if campaign.ai_variation:
                    subject = vary_subject(subject)
                    body = vary_body(body)

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

                # ── Update lead lifecycle ─────────────────────────────────────
                prior_email_contacts = ContactAttempt.objects.filter(
                    listing=listing, channel="email"
                ).exclude(notes__startswith="Added to campaign:").count()
                ContactAttempt.objects.create(
                    listing=listing, channel="email", campaign=campaign,
                    notes=f"Sent via campaign: {campaign.name}",
                )
                if listing.lead_status not in ("converted", "stopped"):
                    from datetime import timedelta
                    if prior_email_contacts == 0:
                        listing.lead_status = "following_up"
                        listing.follow_up_date = timezone.localdate() + timedelta(days=1)
                        listing.follow_up_stage = 1
                    elif prior_email_contacts == 1:
                        listing.lead_status = "following_up"
                        listing.follow_up_date = timezone.localdate() + timedelta(days=7)
                        listing.follow_up_stage = 2
                    elif prior_email_contacts == 2:
                        listing.lead_status = "following_up"
                        listing.follow_up_date = timezone.localdate() + timedelta(days=14)
                        listing.follow_up_stage = 3
                    else:
                        listing.lead_status = "stopped"
                        listing.follow_up_date = None
                        listing.follow_up_stage = 4
                    listing.save(update_fields=["lead_status", "follow_up_date", "follow_up_stage"])

                sent += 1
                slot_sent += 1
                campaign_sent_today += 1

                if sent % 10 == 0:
                    log(f"Sent {sent}/{total} (SMTP: {smtp_slots[slot_idx]['label']}, slot cap used: {slot_sent})")

                # ── Send-mode delay ───────────────────────────────────────────
                mode = getattr(campaign, "send_mode", "fast")
                if mode == "burst":
                    time.sleep(0.1)
                elif mode == "slow":
                    time.sleep(random.uniform(7, 15))
                else:  # fast (default)
                    time.sleep(3)

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
        log(f"Campaign stopped — daily limit reached. Sent {sent}, failed {failed}, skipped {skipped}. Use 'Send Now' tomorrow to continue.")
    elif check_stop():
        campaign.status = "stopped"
    else:
        campaign.status = "sent" if (sent > 0 or skipped > 0) and failed == 0 else (
            "failed" if sent == 0 and failed > 0 else "sent"
        )

    campaign.save(update_fields=["status", "total_sent", "total_failed", "total_skipped", "updated_at"])
    log(f"Campaign complete: {sent} sent, {failed} failed, {skipped} skipped.")


# ─── Test email ───────────────────────────────────────────────────────────────

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
        # Use the profile's verified sender address if set, otherwise fall back to SMTP username
        sender_email = profile.from_email.strip() if profile.from_email else profile.user
        sender_name  = profile.from_name.strip()  if profile.from_name  else ""
        from_header  = f"{sender_name} <{sender_email}>" if sender_name else sender_email

        msg = MIMEMultipart("alternative")
        msg["Subject"] = "✅ Test email from LeadManager4U"
        msg["From"] = from_header
        msg["To"] = to_email
        if profile.reply_to:
            msg["Reply-To"] = profile.reply_to
        body = (
            f"This is a test email sent from LeadManager4U.\n\n"
            f"SMTP Profile: {profile.name}\n"
            f"Host: {profile.host}:{profile.port}\n"
            f"Sent from: {sender_email}\n\n"
            f"If you received this, your SMTP profile is working correctly! ✅"
        )
        msg.attach(MIMEText(body, "plain", "utf-8"))
        # Envelope sender must match the verified From address for providers like Brevo
        conn.sendmail(sender_email, [to_email], msg.as_string())
        conn.quit()
        return True, f"Test email sent to {to_email} via {profile.name} (from: {sender_email})."
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
