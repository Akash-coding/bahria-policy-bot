from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import File
from django.core.management.base import BaseCommand

from documents.models import Document, DocumentCategory, DocumentStatus
from rag.pipeline import process_document

OFFICIAL_FILES = [
    {
        "filename": "BU_Student_Handbook.pdf",
        "title": "Bahria University Student Handbook (official)",
        "category": DocumentCategory.ACADEMIC,
        "department": "Registrar / Academics",
        "description": "Official Student Handbook published on bahria.edu.pk. Covers admissions, registration, semester freeze, fees, attendance, examinations, discipline, and scholarships.",
        "version": "official-web",
    },
    {
        "filename": "BU_Ethics_Policy.pdf",
        "title": "Bahria University Ethics Policy (official)",
        "category": DocumentCategory.GENERAL,
        "department": "University Administration",
        "description": "Official ethics policy downloaded from the Bahria University Downloads page.",
        "version": "official-web",
    },
    {
        "filename": "BU_Sexual_Harassment_Policy.pdf",
        "title": "Policy on Protection Against Sexual Harassment (official)",
        "category": DocumentCategory.STUDENT_AFFAIRS,
        "department": "Student Affairs / HEC Compliance",
        "description": "Official anti-sexual-harassment policy from bahria.edu.pk / HEC guidelines adopted by Bahria University.",
        "version": "official-web",
    },
    {
        "filename": "BU_HEC_Sexual_Harassment_Guidelines.pdf",
        "title": "HEC Sexual Harassment Guidelines (official via Bahria)",
        "category": DocumentCategory.STUDENT_AFFAIRS,
        "department": "Student Affairs / HEC Compliance",
        "description": "HEC policy guidelines against sexual harassment in higher education institutions, published via Bahria University downloads.",
        "version": "official-web",
    },
]


class Command(BaseCommand):
    help = "Load official Bahria University policy PDFs downloaded from bahria.edu.pk."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        source_dir = settings.PROJECT_ROOT / "official_policies"
        if not source_dir.exists():
            self.stderr.write(f"Missing folder: {source_dir}")
            return

        admin = User.objects.filter(is_staff=True).first()
        imported = 0
        for spec in OFFICIAL_FILES:
            path = source_dir / spec["filename"]
            if not path.exists():
                self.stderr.write(f"Missing file: {path}")
                continue
            existing = Document.objects.filter(title=spec["title"]).first()
            if existing and not options["force"]:
                self.stdout.write(f"Skipping existing: {spec['title']}")
                continue
            if existing and options["force"]:
                existing.delete()

            document = Document(
                title=spec["title"],
                category=spec["category"],
                department=spec["department"],
                description=spec["description"],
                version=spec["version"],
                file_type="pdf",
                status=DocumentStatus.UPLOADED,
                uploaded_by=admin,
            )
            with path.open("rb") as handle:
                document.file.save(spec["filename"], File(handle), save=True)
            process_document(document.id)
            imported += 1
            document.refresh_from_db()
            self.stdout.write(
                self.style.SUCCESS(
                    f"{spec['title']} -> {document.status} ({document.chunk_count} chunks)"
                )
            )
            if document.error_message:
                self.stderr.write(document.error_message)

        self.stdout.write(self.style.SUCCESS(f"Imported {imported} official document(s)."))
