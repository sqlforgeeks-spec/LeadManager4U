from django.contrib import admin
from .models import ScrapeJob, JobLog, BusinessListing, EmailCampaign, EmailSend


@admin.register(ScrapeJob)
class ScrapeJobAdmin(admin.ModelAdmin):
    list_display = ["id", "search_phrase", "source", "status", "collected_listings", "emails_found", "created_at"]
    list_filter = ["status", "source", "speed"]
    search_fields = ["search_phrase", "locations"]


@admin.register(JobLog)
class JobLogAdmin(admin.ModelAdmin):
    list_display = ["id", "job", "level", "message", "created_at"]
    list_filter = ["level"]


@admin.register(BusinessListing)
class BusinessListingAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "email", "phone", "website", "location", "source", "scraped_at"]
    list_filter = ["source"]
    search_fields = ["name", "email", "phone", "website"]


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "status", "total_sent", "total_failed", "created_at"]
    list_filter = ["status"]


@admin.register(EmailSend)
class EmailSendAdmin(admin.ModelAdmin):
    list_display = ["id", "campaign", "listing", "status", "sent_at"]
    list_filter = ["status"]
