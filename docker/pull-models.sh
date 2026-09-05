#!/bin/sh
set -e
OLLAMA_URL="${OLLAMA_URL:-http://ollama:11434}"

echo "Waiting for Ollama at ${OLLAMA_URL}"
i=0
until curl -sf "${OLLAMA_URL}/api/tags" >/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "Ollama did not become ready."
    exit 1
  fi
  sleep 2
done

pull() {
  echo "Pulling $1 ..."
  curl -sfS --max-time 3600 -X POST "${OLLAMA_URL}/api/pull" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$1\",\"stream\":false}"
  echo
}

pull gemma3:4b
pull nomic-embed-text
echo "Installed models:"
curl -sfS "${OLLAMA_URL}/api/tags"
echo
echo "Models ready."
