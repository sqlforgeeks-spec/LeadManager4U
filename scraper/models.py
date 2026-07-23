from django.db import models
from django.utils import timezone


class SmtpProfile(models.Model):
    name = models.CharField(max_length=255, help_text="Friendly name, e.g. Gmail – john@company.com")
    host = models.CharField(max_length=255, default="smtp.gmail.com")
    port = models.PositiveIntegerField(default=587)
    user = models.CharField(max_length=255)
    password = models.CharField(max_length=500)
    use_tls = models.BooleanField(default=True)
    daily_limit = models.PositiveIntegerField(
        default=300,
        help_text="Max emails this profile can send per day (e.g. 300 for Gmail free, 0 = unlimited)."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EmailTemplate(models.Model):
    """Reusable saved email templates."""
    name = models.CharField(max_length=255, help_text="Friendly label, e.g. 'Dental Outreach – Urgency'")
    subject = models.CharField(max_length=500)
    body = models.TextField()
    industry = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class ScrapeJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("paused", "Paused"),
        ("completed", "Completed"),
        ("completed_with_errors", "Completed With Errors"),
        ("failed", "Failed"),
    ]

    SPEED_CHOICES = [
        ("slow", "Slow"),
        ("normal", "Normal"),
        ("fast", "Fast"),
    ]

    SOURCE_CHOICES = [
        ("maps", "Google Maps"),
        ("bing_maps", "Bing Maps"),
        ("google", "Google Search"),
        ("bing", "Bing"),
        ("yahoo", "Yahoo"),
        ("duckduckgo", "DuckDuckGo"),
        ("yandex", "Yandex"),
        ("ecosia", "Ecosia"),
        ("ask", "Ask.com"),
    ]

    SEARCH_TYPE_CHOICES = [
        ("web", "Web Pages"),
        ("images", "Images"),
        ("videos", "Videos"),
        ("news", "News"),
    ]

    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="queued")
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default="maps")
    search_phrase = models.CharField(max_length=255)
    domain = models.CharField(max_length=32, default="com", blank=True)
    locations = models.TextField(blank=True)
    country = models.CharField(max_length=8, blank=True, default="",
                               help_text="Country code e.g. us, uk, in, au (for search engines)")
    search_type = models.CharField(max_length=16, choices=SEARCH_TYPE_CHOICES, default="web",
                                   help_text="Search type for Google (web, images, videos, news)")
    max_results = models.PositiveIntegerField(default=1000)
    total_results = models.PositiveIntegerField(default=0)
    collected_listings = models.PositiveIntegerField(default=0)
    processed_listings = models.PositiveIntegerField(default=0)
    emails_found = models.PositiveIntegerField(default=0)
    total_locations = models.PositiveIntegerField(default=0)
    processed_locations = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    speed = models.CharField(max_length=16, choices=SPEED_CHOICES, default="normal")
    auto_campaign_created = models.BooleanField(default=False,
                                                help_text="Whether an email campaign was auto-created for this job")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Job {self.id} - {self.search_phrase} ({self.source})"

    def get_source_display_icon(self):
        icons = {
            "maps": "🗺️",
            "bing_maps": "🅱️",
            "google": "🔍",
            "bing": "Ⓑ",
            "yahoo": "Y!",
            "duckduckgo": "🦆",
            "yandex": "Я",
            "ecosia": "🌿",
            "ask": "❓",
        }
        return icons.get(self.source, "🔍")


class JobLog(models.Model):
    LEVEL_CHOICES = [
        ("INFO", "Info"),
        ("WARN", "Warn"),
        ("ERROR", "Error"),
    ]

    job = models.ForeignKey(ScrapeJob, on_delete=models.CASCADE, related_name="logs")
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="INFO")
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Job {self.job_id} [{self.level}]"


class BusinessListing(models.Model):
    LEAD_STATUS_CHOICES = [
        ("fresh", "Fresh"),
        ("following_up", "Following Up"),
        ("converted", "Converted"),
        ("stopped", "Stopped"),
    ]

    job = models.ForeignKey(ScrapeJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="listings")
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True, max_length=500)
    maps_url = models.URLField(blank=True, max_length=500)
    address = models.CharField(max_length=500, blank=True)
    search_query = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    source = models.CharField(max_length=32, default="maps")
    notes = models.TextField(blank=True, help_text="Internal notes about this lead")
    scraped_at = models.DateTimeField(auto_now_add=True)

    # Lead lifecycle
    lead_status = models.CharField(
        max_length=20, choices=LEAD_STATUS_CHOICES, default="fresh",
        help_text="Current pipeline status for this lead"
    )
    is_starred = models.BooleanField(default=False, help_text="Starred/priority lead — shown above others")
    follow_up_date = models.DateField(
        null=True, blank=True,
        help_text="Next follow-up date; shown in notifications when due"
    )
    follow_up_stage = models.PositiveSmallIntegerField(
        default=0,
        help_text="Follow-up cadence stage: 1 tomorrow, 2 in 7 days, 3 in 14 days, then stopped.",
    )
    follow_up_note = models.TextField(blank=True, help_text="Internal note about this lead's status")

    class Meta:
        ordering = ['-is_starred', '-scraped_at']

    def __str__(self):
        return self.name

    @property
    def last_contact(self):
        return self.contact_attempts.first()

    @property
    def contact_count(self):
        return self.contact_attempts.count()

    @property
    def days_since_last_contact(self):
        lc = self.last_contact
        if not lc:
            return None
        delta = timezone.now() - lc.contacted_at
        return delta.days


class ContactAttempt(models.Model):
    CHANNEL_CHOICES = [
        ("email", "Email"),
        ("gmail", "Gmail"),
        ("whatsapp", "WhatsApp"),
        ("telegram", "Telegram"),
        ("call", "Call"),
    ]

    listing = models.ForeignKey(BusinessListing, on_delete=models.CASCADE, related_name="contact_attempts")
    channel = models.CharField(max_length=20, choices=CHANNEL_CHOICES, default="email")
    contacted_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    campaign = models.ForeignKey(
        'EmailCampaign', on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contact_attempts"
    )

    class Meta:
        ordering = ['-contacted_at']

    def __str__(self):
        return f"{self.listing.name} via {self.channel}"


class EmailCampaign(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("scheduled", "Scheduled"),
        ("sending", "Sending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("stopped", "Stopped"),
    ]

    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=500)
    body = models.TextField(help_text="Use {name}, {email}, {phone}, {website}, {location} as placeholders.")
    from_name = models.CharField(max_length=255, blank=True)
    from_email = models.EmailField()
    reply_to = models.EmailField(blank=True)
    smtp_host = models.CharField(max_length=255, default="smtp.gmail.com")
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_user = models.CharField(max_length=255)
    smtp_password = models.CharField(max_length=500)
    use_tls = models.BooleanField(default=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="draft")
    total_sent = models.PositiveIntegerField(default=0)
    total_failed = models.PositiveIntegerField(default=0)
    total_skipped = models.PositiveIntegerField(default=0)
    job_filter = models.ForeignKey(
        ScrapeJob, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="campaigns",
        help_text="If set, only send to leads from this job."
    )
    scheduled_at = models.DateTimeField(null=True, blank=True,
                                        help_text="If set, campaign will be sent automatically at this time.")
    # Daily send limit (0 = no limit). Campaign pauses automatically when reached.
    daily_limit = models.PositiveIntegerField(
        default=0,
        help_text="Max emails to send per day. 0 = unlimited. Campaign pauses when this is reached."
    )
    # Extra SMTP profiles for rotation (used when daily_limit per profile is exceeded)
    extra_smtp_profiles = models.ManyToManyField(
        "SmtpProfile", blank=True, related_name="campaigns",
        help_text="Additional SMTP accounts for automatic rotation when one hits its daily limit."
    )
    # AI variation: slightly vary subject/body per email to avoid spam filters
    ai_variation = models.BooleanField(
        default=True,
        help_text="When enabled, subtly varies the subject line and opening line of each email to avoid spam detection."
    )

    # Send mode: controls the delay between emails
    SEND_MODE_CHOICES = [
        ("burst", "Burst — send all at once"),
        ("fast", "Fast — 3 s between emails"),
        ("slow", "Slow — 7–15 s between emails"),
    ]
    send_mode = models.CharField(
        max_length=10,
        choices=SEND_MODE_CHOICES,
        default="fast",
        help_text="Controls sending speed to protect your SMTP from bans.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def total_targets(self):
        return self.sends.count()

    @property
    def progress_pct(self):
        total = self.total_targets
        if not total:
            return 0
        return min(100, int((self.total_sent + self.total_failed + self.total_skipped) * 100 / total))


class EmailSend(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    ]

    campaign = models.ForeignKey(EmailCampaign, on_delete=models.CASCADE, related_name="sends")
    listing = models.ForeignKey(BusinessListing, on_delete=models.CASCADE, related_name="email_sends")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="pending")
    sent_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        unique_together = [["campaign", "listing"]]

    def __str__(self):
        return f"{self.campaign} → {self.listing}"


class AutoConfig(models.Model):
    """Singleton-style table for global auto-mode settings."""

    # ── Global send limits ────────────────────────────────────────────────────
    global_daily_limit = models.PositiveIntegerField(
        default=0,
        help_text="Maximum emails sent per day across ALL campaigns combined. 0 = no global cap.",
    )
    global_smtp_profiles = models.ManyToManyField(
        "SmtpProfile", blank=True,
        related_name="global_auto_configs",
        help_text="Global SMTP rotation pool — any campaign without its own extra profiles will use these.",
    )
    smtp_rotation_limit = models.PositiveIntegerField(
        default=0,
        help_text="Rotate to next SMTP after this many emails. 0 = Auto (uses each profile's own daily limit).",
    )

    # Auto-scrape
    auto_scrape_enabled = models.BooleanField(default=False)
    auto_scrape_phrase = models.CharField(max_length=255, blank=True, default="")
    auto_scrape_locations = models.TextField(blank=True, default="", help_text="Comma-separated locations")
    auto_scrape_max_results = models.PositiveIntegerField(default=200)
    auto_scrape_interval_hours = models.PositiveIntegerField(default=24, help_text="How often to run auto-scrape (hours)")
    auto_scrape_source = models.CharField(max_length=20, default="maps")  # maps / bing_maps / duckduckgo
    auto_scrape_last_run = models.DateTimeField(null=True, blank=True)
    auto_scrape_next_run = models.DateTimeField(null=True, blank=True)

    # Auto-campaign
    auto_campaign_enabled = models.BooleanField(default=False)
    auto_campaign_smtp_profile = models.ForeignKey(
        SmtpProfile, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="auto_configs",
    )
    auto_campaign_subject = models.CharField(max_length=500, blank=True, default="Quick question for {name}")
    auto_campaign_body = models.TextField(blank=True, default="Hi {name},\n\nI came across your business and wanted to reach out.\n\nBest,\nYour Name")
    auto_campaign_from_name = models.CharField(max_length=255, blank=True, default="")
    auto_campaign_from_email = models.EmailField(blank=True, default="")
    auto_campaign_delay_minutes = models.PositiveIntegerField(default=30, help_text="Minutes to wait after job completes before sending")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Auto Config"

    def __str__(self):
        return "Auto Config"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
