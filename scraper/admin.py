from django.contrib import admin

from .models import ScrapeJob, BusinessListing, JobLog


@admin.register(ScrapeJob)
class ScrapeJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "search_phrase", "domain", "max_results", "collected_listings", "processed_listings", "emails_found", "created_at", "updated_at")
    list_filter = ("status", "domain", "speed")
    search_fields = ("search_phrase", "locations")
    ordering = ("-created_at",)


@admin.register(BusinessListing)
class BusinessListingAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "email", "website", "location", "search_query", "job", "scraped_at")
    list_filter = ("location",)
    search_fields = ("name", "email", "phone", "website", "location", "search_query")
    ordering = ("-scraped_at",)


@admin.register(JobLog)
class JobLogAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "level", "message", "created_at")
    list_filter = ("level",)
    search_fields = ("message",)
    ordering = ("-created_at",)
