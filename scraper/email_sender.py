"""
SMTP email campaign sender.
Sends personalised emails to BusinessListings via a configured campaign.
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


def send_campaign(campaign_id, log_fn=None, should_stop_fn=None):
    """
    Background thread target: sends all pending EmailSend records for a campaign.
    Updates campaign status and per-send status.
    """
    # Import here to avoid circular imports
    from django.db import close_old_connections
    from .models import EmailCampaign, EmailSend

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
        return False

    try:
        campaign = EmailCampaign.objects.get(id=campaign_id)
    except EmailCampaign.DoesNotExist:
        log(f"Campaign {campaign_id} not found.", "ERROR")
        return

    campaign.status = "sending"
    campaign.save(update_fields=["status", "updated_at"])
    log(f"Campaign '{campaign.name}' started sending.")

    # Build SMTP connection
    try:
        if campaign.use_tls:
            smtp = smtplib.SMTP(campaign.smtp_host, campaign.smtp_port, timeout=15)
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(campaign.smtp_host, campaign.smtp_port, timeout=15)
        smtp.login(campaign.smtp_user, campaign.smtp_password)
        log(f"SMTP connected to {campaign.smtp_host}:{campaign.smtp_port}")
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
    log(f"Sending to {total} recipients...")

    sent = 0
    failed = 0
    skipped = 0

    try:
        for send in pending_sends.iterator():
            if check_stop():
                log("Campaign stopped by user.")
                break

            listing = send.listing

            if not listing.email:
                send.status = "skipped"
                send.error = "No email address"
                send.save(update_fields=["status", "error"])
                skipped += 1
                continue

            # Skip leads marked converted or stopped — they opted out or closed
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
                from_addr = (
                    f"{campaign.from_name} <{campaign.from_email}>"
                    if campaign.from_name
                    else campaign.from_email
                )
                msg["From"] = from_addr
                msg["To"] = listing.email
                if campaign.reply_to:
                    msg["Reply-To"] = campaign.reply_to

                # Plain text
                msg.attach(MIMEText(body, "plain"))

                smtp.sendmail(campaign.from_email, [listing.email], msg.as_string())

                send.status = "sent"
                send.sent_at = timezone.now()
                send.save(update_fields=["status", "sent_at"])
                sent += 1

                if sent % 10 == 0:
                    log(f"Sent {sent}/{total}...")

                # Polite delay to avoid rate-limiting
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

    # Update campaign totals
    campaign.total_sent = sent
    campaign.total_failed = failed
    campaign.total_skipped = skipped
    campaign.status = "sent" if failed == 0 and sent > 0 else ("failed" if sent == 0 and failed > 0 else "sent")
    campaign.save(update_fields=["status", "total_sent", "total_failed", "total_skipped", "updated_at"])
    log(f"Campaign complete: {sent} sent, {failed} failed, {skipped} skipped.")


def launch_campaign(campaign_id, log_fn=None, should_stop_fn=None):
    """Spawn campaign in a background daemon thread."""
    t = threading.Thread(
        target=send_campaign,
        args=(campaign_id, log_fn, should_stop_fn),
        daemon=True,
    )
    t.start()
    return t
