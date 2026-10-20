# Family Claude bot. Works on amd64 and arm64 (e.g. a Raspberry Pi);
# Cloudflare Containers need the linux/amd64 build.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# iptables: optional LAN-egress firewall. util-linux provides setpriv (drop to
# the bot user). tini reaps the Claude Code subprocesses.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates iptables tini util-linux \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app
COPY pyproject.toml ./
COPY bot ./bot
# claude-agent-sdk ships its own Claude Code binary, so no Node.js is needed.
RUN pip install . && rm -rf build *.egg-info

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh \
    && mkdir -p /data && chown bot:bot /data

# HOME and the work dir live on tmpfs in docker-compose (read-only root fs).
ENV DATA_DIR=/data \
    WORK_DIR=/tmp/bot-work \
    HOME=/tmp/home \
    HEALTH_FILE=/tmp/bot-health

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
    CMD ["python", "-m", "bot.healthcheck"]

ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
