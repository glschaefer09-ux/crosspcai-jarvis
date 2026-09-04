# JARVIS — server role.
# This is the image for TrueNAS SCALE, CrossPC AI OS and any headless box.
# Same application as the desktop build, started with --server so the UI is
# served to a browser instead of a native window.

FROM python:3.12-slim

LABEL org.opencontainers.image.title="JARVIS" \
      org.opencontainers.image.description="CrossPCAI control centre — chat, agents, sandbox, Slack" \
      org.opencontainers.image.vendor="CCD Enterprise and Development" \
      org.opencontainers.image.version="1.0.0"

# The core app is stdlib-only; git and curl are here because the sandbox is
# where customers run real work.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# Run as a non-root user: the sandbox executes commands, so the blast radius
# should not include the container's own system files.
RUN useradd --create-home --uid 1000 jarvis

WORKDIR /app
COPY jarvis/ /app/jarvis/
COPY pyproject.toml README.md /app/

# Config, database and the sandbox workspace all live here — mount it.
ENV CROSSPCAI_HOME=/data \
    JARVIS_PORT=5580 \
    JARVIS_BIND=0.0.0.0 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data && chown -R jarvis:jarvis /data /app
USER jarvis
VOLUME ["/data"]

EXPOSE 5580 5561 5562

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5580/api/node/hello',timeout=4).status==200 else 1)"

ENTRYPOINT ["python", "-m", "jarvis"]
CMD ["--server", "--bind", "0.0.0.0", "--port", "5580"]
