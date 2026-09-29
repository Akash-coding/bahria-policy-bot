from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from documents.models import Document
from rag.embeddings import embedding_status, get_embedding_service
from rag.groq_client import check_groq
from rag.pipeline import ProcessingError, process_document


def _model_present(models: list[str], wanted: str) -> bool:
    return any(
        name == wanted or name.startswith(f"{wanted}:") or wanted.split(":")[0] in name
        for name in models
    )


class Command(BaseCommand):
    help = "Re-index every uploaded policy with the current embedding model."

    def handle(self, *args, **options):
        get_embedding_service.cache_clear()
        if settings.EMBEDDING_PROVIDER == "groq":
            status = check_groq()
            if not status.get("reachable"):
                raise CommandError(f"Groq is not reachable: {status.get('error') or 'unknown error'}")
            embed_model = settings.EMBEDDING_MODEL
            models = status.get("models") or []
            if models and not _model_present(models, embed_model):
                self.stdout.write(
                    self.style.WARNING(
                        f"Embedding model '{embed_model}' was not listed by Groq. "
                        f"Available: {', '.join(models) or 'none'}."
                    )
                )
        else:
            embed_model = settings.EMBEDDING_MODEL

        self.stdout.write(
            f"Warming up embeddings. Configured: {settings.EMBEDDING_PROVIDER} / {embed_model}."
        )
        get_embedding_service().embed_texts(["Bahria University policy"])
        status = embedding_status()
        self.stdout.write(
            self.style.SUCCESS(
                "Embedding model is ready: "
                f"provider={status.get('active_provider')} "
                f"model={status.get('active_model')} "
                f"source={status.get('source')}"
            )
        )

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
