FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=speedward.web.settings
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY speedward ./speedward
RUN pip install --no-cache-dir ".[geoip]" \
 && STATIC_ROOT=/app/staticfiles django-admin collectstatic --noinput \
 && useradd --system --create-home app
ENV STATIC_ROOT=/app/staticfiles
USER app
EXPOSE 8000
# One worker + threads: scan progress is held in this process's memory. No access log by design.
CMD ["gunicorn", "speedward.web.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "60"]
