# AetherOps — zero-dependency image: base Python is the entire runtime.
FROM python:3.12-slim

WORKDIR /app
COPY src/ src/
COPY pyproject.toml README.md ./

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; \
        urllib.request.urlopen('http://localhost:8080/health', timeout=2)"

CMD ["python", "-m", "aetherops.api"]
