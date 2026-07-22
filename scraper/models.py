from django.db import models


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

    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default="queued")
    search_phrase = models.CharField(max_length=255)
    domain = models.CharField(max_length=32)
    locations = models.TextField()
    max_results = models.PositiveIntegerField(default=1000)
    total_results = models.PositiveIntegerField(default=0)
    collected_listings = models.PositiveIntegerField(default=0)
    processed_listings = models.PositiveIntegerField(default=0)
    emails_found = models.PositiveIntegerField(default=0)
    total_locations = models.PositiveIntegerField(default=0)
    processed_locations = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    speed = models.CharField(max_length=16, choices=SPEED_CHOICES, default="normal")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Job {self.id} - {self.search_phrase}"


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
    website = models.URLField(blank=True)
    maps_url = models.URLField(blank=True)
    search_query = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    scraped_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
