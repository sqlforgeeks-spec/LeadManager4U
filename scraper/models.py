from django.db import models


class SmtpProfile(models.Model):
    name = models.CharField(max_length=255, help_text="Friendly name, e.g. Gmail – john@company.com")
    host = models.CharField(max_length=255, default="smtp.gmail.com")
    port = models.PositiveIntegerField(default=587)
    user = models.CharField(max_length=255)
    password = models.CharField(max_length=500)
    use_tls = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

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
    scraped_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-scraped_at']

    def __str__(self):
        return self.name


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
