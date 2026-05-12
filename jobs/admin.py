from django.contrib import admin

from .models import Job, JobListing, RecommendedJob, ScrapeJob


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "user", "application_status", "created_at")
    list_filter = ("application_status", "created_at")
    search_fields = ("title", "company", "user__email")
    readonly_fields = ("created_at",)


@admin.register(RecommendedJob)
class RecommendedJobAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "user", "match_score", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("title", "company", "user__email", "url")
    readonly_fields = ("created_at",)


@admin.register(ScrapeJob)
class ScrapeJobAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "status", "progress_pct", "current_step", "created_at", "finished_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "current_step", "message")
    readonly_fields = (
        "id", "user", "created_at", "finished_at", "params_json", "progress_pct",
        "total_steps", "completed_steps", "current_step", "message", "error",
    )

    def has_add_permission(self, request):
        return False


@admin.register(JobListing)
class JobListingAdmin(admin.ModelAdmin):
    list_display = ("title", "company", "source", "scrape_job", "scraped_at")
    list_filter = ("source", "scraped_at")
    search_fields = ("title", "company", "url")
    readonly_fields = (
        "id", "scrape_job", "source", "title", "company", "company_url",
        "location", "country", "posted", "salary", "url", "description",
        "raw_text", "scraped_at", "unique_hash",
    )

    def has_add_permission(self, request):
        return False
