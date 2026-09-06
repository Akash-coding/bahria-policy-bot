from django.core.management.base import BaseCommand

from documents.models import Document
from rag.embeddings import get_embedding_service
from rag.pipeline import ProcessingError, process_document
from rag.vectorstore import get_vector_store


class Command(BaseCommand):
    help = "Re-index every uploaded policy with the current embedding model."

    def handle(self, *args, **options):
        get_embedding_service.cache_clear()
        store = get_vector_store()
        store.reset()
        get_vector_store.cache_clear()

        documents = list(Document.objects.order_by("id"))
        if not documents:
            self.stdout.write("No policy documents found.")
            return

        ok = 0
        failed = 0
        for document in documents:
            self.stdout.write(f"Reprocessing {document.id}: {document.title}")
            try:
                processed = process_document(document.id)
                self.stdout.write(self.style.SUCCESS(f"  completed ({processed.chunk_count} chunks)"))
                ok += 1
            except ProcessingError as exc:
                self.stdout.write(self.style.ERROR(f"  failed: {exc}"))
                failed += 1

        self.stdout.write(self.style.SUCCESS(f"Done. {ok} ok, {failed} failed."))
