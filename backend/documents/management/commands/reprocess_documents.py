from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import requests

from documents.models import Document
from rag.embeddings import get_embedding_service
from rag.ollama_client import check_ollama
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
        status = check_ollama()
        if not status.get("reachable"):
            raise CommandError(f"Ollama is not reachable: {status.get('error') or 'unknown error'}")

        embed_model = settings.EMBEDDING_MODEL
        models = status.get("models") or []
        if not _model_present(models, embed_model):
            raise CommandError(
                f"Embedding model '{embed_model}' is not installed in Docker Ollama. "
                f"Installed: {', '.join(models) or 'none'}. "
                f"Run: docker compose exec ollama ollama pull {embed_model}"
            )

        self.stdout.write("Unloading the chat model so embeddings can load...")
        try:
            requests.post(
                f"{settings.OLLAMA_BASE_URL}/api/generate",
                json={"model": settings.OLLAMA_MODEL, "keep_alive": 0, "prompt": ""},
                timeout=30,
            )
        except requests.RequestException:
            pass

        self.stdout.write(
            f"Warming up {embed_model}. The first request can take several minutes. Do not press Ctrl+C."
        )
        get_embedding_service().embed_texts(["Bahria University policy"])
        self.stdout.write(self.style.SUCCESS("Embedding model is ready."))

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
