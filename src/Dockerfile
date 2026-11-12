FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY reyvo ./reyvo
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

# Knowledge-base source. Override DESIGN_DOC_PATH (or mount a volume) to point
# at a different document; ingestion and /ai/knowledge/status read it at runtime.
COPY Reyvo_Detailed_Design_Document_v1.0.docx ./

EXPOSE 8000

# The default command runs the API. The sender worker is a separate process:
#   docker compose run --rm worker
CMD ["uvicorn", "reyvo.main:app", "--host", "0.0.0.0", "--port", "8000"]
