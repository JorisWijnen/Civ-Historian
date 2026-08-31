# Civ Historian webapp -- the on-demand upload-and-run alternative to
# log_watcher.py (which keeps running directly on the host, untouched).
# See docs/webapp.md.
FROM python:3.12-slim-bookworm

# fonts-dejavu-core provides /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
# at the exact path scripts/render_map_lib.py's _FONT_CANDIDATES already
# hardcodes -- no code changes needed. No system ffmpeg package: imageio-
# ffmpeg (in requirements.txt) bundles its own static binary.
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        ca-certificates \
        curl \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y curl gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# ENV HOME=/app so `claude`'s config-dir lookup resolves predictably --
# matches where docker-compose.yml mounts the host's ~/.claude read-only.
ENV HOME=/app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ scripts/
COPY assets/ assets/
COPY docs/ docs/
COPY webapp/ webapp/
RUN mkdir -p sessions data/webhooks

EXPOSE 8000

# --workers 1: single-user tool, avoids concurrent `claude -p`/image-gen
# calls interleaving. --timeout 0: a real run takes several minutes,
# gunicorn must not reap it mid-request.
CMD ["gunicorn", "--chdir", "webapp", "--bind", "0.0.0.0:8000", \
     "--workers", "1", "--timeout", "0", "app:app"]
