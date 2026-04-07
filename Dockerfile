FROM python:3.12-slim

WORKDIR /app

# Install system deps for pymupdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
COPY src/ src/
COPY config/ config/
RUN pip install --no-cache-dir ".[web]"

# Pre-seed wacker instance data (SQLite + text cache)
COPY data/wacker/ /root/.synapse/dbs/wacker/

# Include PDFs for initial ingest on first deploy
COPY data/pdfs/ /app/data/pdfs/

EXPOSE 8000

CMD ["uvicorn", "synapse.web.app:create_app", "--host", "0.0.0.0", "--port", "8000", "--factory"]
