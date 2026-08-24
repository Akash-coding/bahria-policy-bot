FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY backend /srv/backend
COPY sample_policies /srv/sample_policies
COPY docker/backend-entrypoint.sh /srv/docker/backend-entrypoint.sh
RUN chmod +x /srv/docker/backend-entrypoint.sh

WORKDIR /srv/backend
EXPOSE 8000
ENTRYPOINT ["/srv/docker/backend-entrypoint.sh"]
