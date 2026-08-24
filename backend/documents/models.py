from django.conf import settings
from django.contrib.auth.models import User
from django.db import models


class DocumentCategory(models.TextChoices):
    ACADEMIC = "academic", "Academic Policies"
    EXAMINATION = "examination", "Examination Policies"
    STUDENT_AFFAIRS = "student_affairs", "Student Affairs"
    HR = "hr", "HR Policies"
    FINANCE = "finance", "Finance Policies"
    ADMISSIONS = "admissions", "Admissions Policies"
    LEAVE = "leave", "Leave Policies"
    ATTENDANCE = "attendance", "Attendance Policies"
    GENERAL = "general", "General University Policies"


class DocumentStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    PROCESSING = "processing", "Processing"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class Document(models.Model):
    title = models.CharField(max_length=255)
    category = models.CharField(
        max_length=32,
        choices=DocumentCategory.choices,
        default=DocumentCategory.GENERAL,
        db_index=True,
    )
    department = models.CharField(max_length=128, blank=True, default="")
    description = models.TextField(blank=True, default="")
    file = models.FileField(upload_to="policies/%Y/%m/")
    file_type = models.CharField(max_length=16)
    version = models.CharField(max_length=32, blank=True, default="1.0")
    status = models.CharField(
        max_length=16,
        choices=DocumentStatus.choices,
        default=DocumentStatus.UPLOADED,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    chunk_count = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def file_name(self) -> str:
        return self.file.name.rsplit("/", 1)[-1] if self.file else ""

    @property
    def max_upload_bytes(self) -> int:
        return settings.MAX_UPLOAD_MB * 1024 * 1024


class DocumentChunk(models.Model):
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    content = models.TextField()
    page_number = models.PositiveIntegerField(null=True, blank=True)
    chunk_index = models.PositiveIntegerField()
    vector_id = models.CharField(max_length=128, unique=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "chunk_index"]
        unique_together = [("document", "chunk_index")]

    def __str__(self):
        return f"{self.document.title} #{self.chunk_index}"
