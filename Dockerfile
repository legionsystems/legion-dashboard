# syntax=docker/dockerfile:1.6

# ---- Frontend build stage ----
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Backend runtime stage ----
FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
        git \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 988 --system docker \
    && groupadd --system app \
    && useradd --system --gid app --home /app --shell /usr/sbin/nologin app \
    && usermod -aG docker app
WORKDIR /app

COPY backend/pyproject.toml ./backend/pyproject.toml
RUN pip install --upgrade pip && pip install ./backend

COPY backend/ ./backend/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

# ---- LEGION worktree tool: repo-owned, copied into the image
# so the dashboard can create per-task worktrees without
# depending on host-side /root/.hermes/.
COPY scripts/legion-worktree-create /usr/local/bin/legion-worktree-create
RUN chmod 0755 /usr/local/bin/legion-worktree-create \
 && chown root:root /usr/local/bin/legion-worktree-create

# Git safe.directory: the app container runs as `app` but
# operates on a host-mounted /srv/repo/legion-dashboard that
# is owned by host root. Mark the known /srv/repo paths as
# safe so the app user can run git commands on them without
# "dubious ownership" failures. Done with --system so the
# config applies to every USER (including the `app` user
# selected below) rather than only root's HOME.
RUN git config --system --add safe.directory /srv/repo/legion-dashboard \
 && git config --system --add safe.directory '*'

RUN chown -R app:app /app
RUN mkdir -p /app/attachments && chown app:app /app/attachments
VOLUME ["/app/attachments"]
USER app

WORKDIR /app/backend
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
