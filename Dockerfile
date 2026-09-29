FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/tmp

WORKDIR /srv

COPY requirements.txt /srv/requirements.txt
# CPU torch only — the default CUDA wheel fills a small VM during pip install.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install -r /srv/requirements.txt

COPY backend /srv/backend
COPY sample_policies /srv/sample_policies
COPY docker/backend-entrypoint.sh /srv/docker/backend-entrypoint.sh
RUN chmod +x /srv/docker/backend-entrypoint.sh

WORKDIR /srv/backend
EXPOSE 8000
ENTRYPOINT ["/srv/docker/backend-entrypoint.sh"]
