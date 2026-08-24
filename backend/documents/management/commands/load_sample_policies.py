from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from documents.models import Document, DocumentCategory, DocumentStatus
from rag.pipeline import process_document


SAMPLE_MAP = [
    ("attendance_policy.txt", "Attendance Policy", DocumentCategory.ATTENDANCE, "Academics"),
    ("leave_policy.txt", "Student Leave Policy", DocumentCategory.LEAVE, "Student Affairs"),
    ("academic_probation.txt", "Academic Probation Policy", DocumentCategory.ACADEMIC, "Academics"),
    ("fee_refund_policy.txt", "Fee Refund Policy", DocumentCategory.FINANCE, "Finance"),
    ("semester_freeze_policy.txt", "Semester Freeze Policy", DocumentCategory.ACADEMIC, "Academics"),
    ("examination_policy.txt", "Examination Policy", DocumentCategory.EXAMINATION, "Examinations"),
]


class Command(BaseCommand):
    help = "Load bundled sample policy documents into the knowledge base (for local testing)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-import even if a document with the same title already exists.",
        )

    def handle(self, *args, **options):
        sample_dir = settings.PROJECT_ROOT / "sample_policies"
        if not sample_dir.exists():
            self.stderr.write(f"Sample directory not found: {sample_dir}")
            return

        admin = User.objects.filter(is_staff=True).first()
        created = 0
        for filename, title, category, department in SAMPLE_MAP:
            path = sample_dir / filename
            if not path.exists():
                self.stderr.write(f"Missing sample file: {path}")
                continue
            existing = Document.objects.filter(title=title).first()
            if existing and not options["force"]:
                self.stdout.write(f"Skipping existing document: {title}")
                continue
            if existing and options["force"]:
                existing.delete()

            document = Document(
                title=title,
                category=category,
                department=department,
                description="Bundled sample document for local testing. Replace with official policies before production use.",
                version="sample-1.0",
                file_type="txt",
                status=DocumentStatus.UPLOADED,
                uploaded_by=admin,
            )
            document.file.save(filename, ContentFile(path.read_bytes()), save=True)
            process_document(document.id)
            created += 1
            self.stdout.write(self.style.SUCCESS(f"Imported and processed: {title}"))

        self.stdout.write(self.style.SUCCESS(f"Done. Imported {created} sample document(s)."))
