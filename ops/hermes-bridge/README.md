# Hermes Kanban Bridge

Minimal HTTP API wrapping Hermes CLI for Kanban operations.

## Purpose

The LEGION Dashboard needs to create and manage Hermes Kanban tasks, but:
- Hermes has no REST API for Kanban operations
- Hermes CLI is installed on the host, not in containers
- The Dashboard runs in Docker

This bridge runs on the host and exposes HTTP endpoints that containerized services can call via Docker's `host.docker.internal` gateway.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ HOST (lgn-remote-01)                                        │
│ ┌─────────────────┐    ┌─────────────────────────────────┐  │
│ │ Hermes CLI      │ ←  │ Hermes Kanban Bridge (port 8765)│  │
│ │ /usr/local/lib/ │    │ /opt/legion-dashboard/hermes-...│  │
│ └─────────────────┘    └──────────────┬──────────────────┘  │
│                                       │                       │
└───────────────────────────────────────┼───────────────────────┘
                                        │ HTTP
┌───────────────────────────────────────┼───────────────────────┐
│ CONTAINER                             │                       │
│ ┌─────────────────────────────────────▼──────────────────────┐│
│ │ LEGION Dashboard Backend                                   ││
│ │ backend/app/routers/builder.py                             ││
│ │ _create_hermes_task() → http://host.docker.internal:8765   ││
│ └────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────┘
```

## Installation

From the repo root:

```bash
cd /srv/repo/legion-dashboard
sudo ./ops/hermes-bridge/install.sh
```

This will:
1. Create `/opt/legion-dashboard/hermes-bridge/`
2. Copy the bridge script
3. Install systemd service
4. Enable and start the service

## Verification

```bash
# Check service status
systemctl status legion-dashboard-hermes-kanban-bridge.service

# Health check
curl -sS http://127.0.0.1:8765/health

# List tasks
curl -sS http://127.0.0.1:8765/tasks

# Create a task
curl -sS -X POST http://127.0.0.1:8765/tasks \
  -H "Content-Type: application/json" \
  -d '{"title":"Test","assignee":"builder","idempotency_key":"test-001"}'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/tasks` | GET | List tasks (optional `?status=ready`) |
| `/tasks/<id>` | GET | Show specific task |
| `/tasks` | POST | Create task |
| `/tasks/<id>/comment` | POST | Add comment |
| `/tasks/<id>/complete` | POST | Complete task |
| `/assignees` | GET | List available assignees |
| `/boards` | GET | List boards |

## Security

- No `shell=True` — uses subprocess argument arrays
- Board allowlist: only `legion-apps-build-queue`
- Status allowlist: native Hermes statuses only
- Assignee allowlist: `builder`, `default`, `orchestrator`, `reviewer`, `writer`
- Request body size limit: 50KB
- No secrets logged
- Binds to `0.0.0.0:8765` for host-local access

## Configuration

Environment file: `/etc/legion-dashboard/hermes-bridge.env`

Currently unused (all config is hardcoded for security), but reserved for future:
- `HERMES_CLI_PATH` — override Hermes CLI path
- `KANBAN_BOARD` — override default board
- `BIND_PORT` — override default port 8765

## Uninstallation

```bash
sudo ./ops/hermes-bridge/uninstall.sh
```

This will:
1. Stop and disable systemd service
2. Remove runtime files from `/opt/legion-dashboard/`
3. Remove systemd unit
4. Remove config directory

## Development

Source of truth: `/srv/repo/legion-dashboard/ops/hermes-bridge/`

Runtime install: `/opt/legion-dashboard/hermes-bridge/`

To test changes without reinstalling:

```bash
# Stop systemd service
sudo systemctl stop legion-dashboard-hermes-kanban-bridge.service

# Run manually from repo
python3 /srv/repo/legion-dashboard/ops/hermes-bridge/hermes_kanban_bridge.py 8765

# Test in another terminal
curl -sS http://127.0.0.1:8765/health
```

## Troubleshooting

### Bridge not responding

```bash
systemctl status legion-dashboard-hermes-kanban-bridge.service
journalctl -u legion-dashboard-hermes-kanban-bridge.service --no-pager -n 50
```

### Hermes CLI errors

Check Hermes is installed:

```bash
/usr/local/lib/hermes-agent/venv/bin/hermes --version
/usr/local/lib/hermes-agent/venv/bin/hermes kanban boards --json
```

### Container can't reach bridge

Verify Docker gateway:

```bash
docker exec legion-dashboard-app-1 curl -sS http://host.docker.internal:8765/health
```

Ensure `extra_hosts` in docker-compose.yml:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
