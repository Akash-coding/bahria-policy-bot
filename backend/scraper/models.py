from django.conf import settings
from django.db import models


class WebsiteStatus(models.TextChoices):
    STARTING = "starting", "Starting"
    DISCOVERING = "discovering", "Discovering pages"
    SCRAPING = "scraping", "Scraping pages"
    PROCESSING = "processing", "Processing content"
    SAVING = "saving", "Saving data"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class PageStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class WebsiteSource(models.Model):
    seed_url = models.URLField(max_length=500)
    domain = models.CharField(max_length=255, unique=True, db_index=True)
    title = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(
        max_length=24,
        choices=WebsiteStatus.choices,
        default=WebsiteStatus.STARTING,
        db_index=True,
    )
    progress_detail = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scraped_websites",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or self.domain


class ScrapedPage(models.Model):
    website = models.ForeignKey(
        WebsiteSource,
        on_delete=models.CASCADE,
        related_name="pages",
    )
    url = models.URLField(max_length=800, db_index=True)
    title = models.CharField(max_length=500, blank=True, default="")
    content = models.TextField(blank=True, default="")
    content_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    source_url = models.URLField(max_length=800)
    parent_url = models.URLField(max_length=800, blank=True, default="")
    file_type = models.CharField(max_length=16, default="html")
    status = models.CharField(
        max_length=16,
        choices=PageStatus.choices,
        default=PageStatus.PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    chunk_count = models.PositiveIntegerField(default=0)
    scraped_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        unique_together = [("website", "url")]

    def __str__(self):
        return self.title or self.url
