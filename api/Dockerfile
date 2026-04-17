FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Fixed UID/GID so bind-mounts can be chowned
# predictably on the host. Home dir hosts the claude CLI state
# (~/.claude, ~/.claude.json) — in prod it's a tmpfs overlay, in
# dev it's bound from the host for auth persistence.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app \
        --home-dir /home/app --create-home app \
    && mkdir -p /data/uploads \
    && chown -R app:app /data/uploads /home/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R app:app /app

# Read-only root FS (compose) blocks .pyc writes; skip bytecode.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

USER 10001:10001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
