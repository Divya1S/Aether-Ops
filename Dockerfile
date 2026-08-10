# AetherOps — zero-dependency image: base Python is the entire runtime.
FROM python:3.12-slim

WORKDIR /app
COPY src/ src/
COPY pyproject.toml README.md ./

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    AETHEROPS_BIND=0.0.0.0

# A container that publishes a port must be given a real token at run time
# (AETHEROPS_API_TOKEN): the server refuses to boot on a non-loopback bind
# with the built-in dev token. See api/server.py:_preflight.

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
    CMD python -c "import urllib.request; \
        urllib.request.urlopen('http://localhost:8080/health', timeout=2)"

CMD ["python", "-m", "aetherops.api"]
