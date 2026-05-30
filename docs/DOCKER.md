# Docker Control Setup

The LEGION Dashboard can manage Docker Compose applications (start, stop, restart, pull, rebuild, logs) for apps registered in the control plane.

## Security Model

**Docker socket access is host-admin equivalent.** Mounting `/var/run/docker.sock` into the dashboard container grants full control over the host Docker daemon, including:

- Starting/stopping any container
- Reading logs from any container
- Building and pulling images
- Access to container filesystems via `docker exec`
- Potential privilege escalation paths

### Trust Boundaries

The dashboard mitigates risk through:

1. **Allowlist-only actions** — Only `start`, `stop`, `restart`, `pull`, `rebuild`, `logs` are permitted
2. **Fixed compose paths** — Paths come from the database (seeded from `apps_config.py`), never from user input
3. **No shell execution** — All `subprocess.run` calls use `shell=False` with fixed argv lists
4. **Read-only socket mount** — Socket is mounted `:ro` in some deployments to prevent certain attack vectors (note: write access is required for most Docker operations; `:ro` may break functionality)
5. **Action logging** — Every action is recorded in `app_action_logs` with timestamps, exit codes, and output tails

### Deployment Requirements

**Only deploy dashboard with Docker socket access in trusted environments:**

- Bound to trusted LAN or Tailscale network only
- No public internet exposure
- Operator-only access (no multi-tenant scenarios)
- Host system is dedicated or hardened

## Setup

### 1. Enable Docker Control

Set the environment flag in your `.env` file:

```bash
LEGION_DOCKER_CONTROL_ENABLED=true
```

### 2. Mount Docker Socket

The `docker-compose.yml` includes the socket mount by default:

```yaml
volumes:
  - /srv/repo:/srv/repo:ro
  - /var/run/docker.sock:/var/run/docker.sock
```

If you need to disable Docker control, remove or comment out the socket mount line.

### 3. Verify Installation

After starting the dashboard:

```bash
docker compose exec -T app sh -lc 'docker version && docker compose version'
```

Expected output:
```
Client: Docker Engine - Community
 Version:           29.x.x
 ...

Docker Compose version v5.x.x
```

### 4. Test an Action

```bash
curl -fsS http://127.0.0.1:8720/api/apps/lgn-bar-assistant
```

Check that `compose_exists` is `true` and `build_only` reflects the app's compose file.

## Troubleshooting

### "Docker CLI not found in PATH"

The Docker CLI is not installed in the container. Rebuild with the updated Dockerfile that installs `docker-ce-cli` and `docker-compose-plugin`.

### "Docker socket not found at /var/run/docker.sock"

The socket is not mounted. Add the volume mount to `docker-compose.yml`:

```yaml
- /var/run/docker.sock:/var/run/docker.sock
```

### "Docker socket exists but is not readable/writable"

Check host socket permissions:

```bash
ls -l /var/run/docker.sock
# Should be: srw-rw---- 1 root docker ...
```

Add the container's `app` user to the docker group, or ensure the socket is world-readable (less secure).

### Action returns `not_configured`

The backend detected missing Docker CLI or socket. Check the logs:

```bash
docker compose logs app | grep -i docker
```

## Result Classifications

| Result | Meaning |
|--------|---------|
| `success` | Command exited 0 |
| `not_running` | Logs requested but no containers are up |
| `not_found` | Compose file missing on disk |
| `not_applicable` | Action not meaningful (e.g., `pull` on build-only app) |
| `not_configured` | Docker CLI or socket not available |
| `failed` | Non-zero exit code |
| `timeout` | Command exceeded 300s timeout |

## App Configuration

Apps are registered in `backend/app/apps_config.py`. Each entry specifies:

- `app_id` — Unique identifier
- `display_name` — UI label
- `compose_path` — Absolute path to docker-compose.yml
- `compose_project` — Project name for `-p` flag

Example:
```python
AppConfig(
    app_id="lgn-bar-assistant",
    display_name="LEGION Bar Assistant",
    compose_path="/srv/repo/lgn-bar-assistant/docker-compose.yml",
    compose_project="lgn-bar-assistant",
)
```

Ensure the compose file exists at the specified path and the `/srv/repo` volume is mounted.
