FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

WORKDIR /app

# Install system dependencies required for psycopg2, Pillow, and cryptography
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend project source
COPY backend/ /app/

# Hugging Face Spaces and secure PaaS require non-root user (UID 1000)
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# Collect static files
RUN python manage.py collectstatic --noinput || true

EXPOSE 7860 8000

# Apply migrations and launch Gunicorn on dynamic $PORT (7860 for Hugging Face, or assigned PaaS port)
CMD ["sh", "-c", "python manage.py migrate && gunicorn friends_turf.wsgi:application --bind 0.0.0.0:${PORT:-7860} --workers 2 --threads 4 --timeout 90"]
