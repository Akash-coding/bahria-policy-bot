from django.apps import AppConfig


class RagConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rag"
    verbose_name = "RAG"

    def ready(self) -> None:
        import sys

        command = sys.argv[1] if len(sys.argv) > 1 else ""
        if command in {"test", "migrate", "makemigrations", "collectstatic", "shell"}:
            return
        from .embeddings import log_embedding_startup

        log_embedding_startup()
