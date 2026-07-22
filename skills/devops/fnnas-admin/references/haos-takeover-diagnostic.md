# HAOS Takeover — Diagnostic Notes (2026-06-13)

Concrete pre-flight + login-flow details from a first contact with a Home Assistant
Supervisor VM (HAOS). PVE VM 104, IP `192.168.31.250`.

## Pre-flight

```bash
unset HTTPS_PROXY HTTP_PROXY    # CRITICAL — mihomo blocks HA TLS if proxied
```

| Port | What it is | Notes |
|---|---|---|
| 8123 | HA web UI / REST API | **HTTP, not HTTPS.** Self-signed certs are NOT the HA default. If curl fails with `SSL routines::wrong version number`, try `http://`. |
| 4357 | HAOS host supervisor | Bound to localhost in stock HAOS. **If it's open to LAN, that's a misconfiguration — flag to user.** |
| 22 | HAOS sshd | Disabled by default on HAOS. Enabling requires serial console or `ha os` cli via the supervisor. |
| 80/443 | HA ingress | Not bound on stock HAOS unless user added a reverse proxy. |
| 1883 | Mosquitto MQTT (plain) | If open, Mosquitto add-on is installed. Check auth. |
| 1884 | Mosquitto MQTT (TLS) | Same as above with TLS. |
| 21063 | Z-Wave JS add-on (if present) | |
| 8080/8000 | Various add-ons | |
| 8124 | **Waxgourd (冬瓜) panel** | See "Waxgourd" section below — not a separate HA instance. |

## Login flow

**Long-lived token (preferred):**
1. HA web → click your username (bottom-left)
2. Security tab → "Long-Lived Access Tokens" → Create Token
3. Pass the token to the agent; agent uses `Authorization: Bearer <token>` for all API calls

**Username + password (only if user accepts the risk of plaintext password in chat):**

```bash
# 1. Start the flow
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"client_id":"https://home-assistant.io/iOS","handler":["homeassistant",null],"redirect_uri":"https://home-assistant.io/iOS"}' \
  http://192.168.31.250:8123/auth/login_flow

# 2. Submit credentials
curl -s -X POST -H "Content-Type: application/json" \
  -d '{"client_id":"https://home-assistant.io/iOS","username":"<user>","password":"<pass>"}' \
  http://192.168.31.250:8123/auth/login_flow/<flow_id>

# 3. Exchange the short-lived code for tokens
curl -s -X POST -H "Content-Type: application/x-www-form-urlencoded" \
  -d 'grant_type=authorization_code&code=<code>&client_id=https://home-assistant.io/iOS&redirect_uri=https://home-assistant.io/iOS' \
  http://192.168.31.250:8123/auth/token

# Response: {"access_token":"...","expires_in":1800, "refresh_token":"...","token_type":"Bearer"}
```

**OAuth `client_id` pitfall.** Using a local-IP `client_id` (e.g. `http://192.168.31.250:8123`) can produce a token that the HA REST API immediately rejects with 401. The `client_id` used in `/auth/login_flow`, `/auth/token`, and the redirect URI must be consistent, and HA accepts well-known client IDs such as `https://home-assistant.io/iOS` without extra registration. If you get a valid token but `/api/config` returns 401, retry the flow with `https://home-assistant.io/iOS` as the client_id.

**`/auth/login_flow` 500 → onboarding wasn't completed.**
Every login-flow POST returns 500 (not a validation error) when HA's first-run
onboarding wizard was never finished. Fix: user logs into the HA web UI and
completes "create owner user" — the agent can't bootstrap this from CLI.

## Endpoints worth knowing

| Endpoint | Auth | Use |
|---|---|---|
| `GET /manifest.json` | none | HA version + branding (always works) |
| `GET /` | none | Confirms web UI loads |
| `GET /api/` | token | API discovery |
| `GET /api/config` | token | HA core config (version, components, unit_system, ...) |
| `GET /api/states` | token | All entity states |
| `POST /api/services/<domain>/<service>` | token | Call any service (e.g. `light.turn_on`, `script.reload`) |
| `GET /api/services` | token | List available services |
| `GET /api/config/config_entries/entry` | token | List integrations + their config entries |
| `GET /api/calendars` | token | Calendar entities (limited) |
| `GET /api/history/period/<iso>` | token | Historical state changes |

**REST API endpoint changes in HA 2026.6.2.** The following endpoints return 404 in recent HA versions; automations, scripts, and scenes are now exposed primarily through the WebSocket API (`ws://<host>:8123/api/websocket`):

- `GET /api/automations` → 404
- `GET /api/scripts` → 404
- `GET /api/scenes` → 404
- `GET /api/users` → 404 (user management is UI-only / WS-only)

If you need automation/script/scene configuration, connect to the WebSocket API and send `config/automation/list`, `config/script/list`, `config/scene/list` messages. For quick read-only entity state, `/api/states` still works.

## HAOS host-level operations (NOT the same as HA API)

Things the agent can do at the VM-host level via PVE:
- Snapshot VM 104 (vzdump) — non-destructive, recommended before any HA config change
- Reboot VM 104 — done from PVE side, NOT via HA API
- Reset HAOS password (requires serial console) — agent should refuse without explicit 干

Things only the user can do (or via HA API with admin token):
- Install/remove add-ons
- Modify `configuration.yaml` directly
- Restart HA Core
- Update HAOS

## Security notes

- Default HAOS install: no HTTPS, no rate limiting on `/auth/login_flow` (rate limit is per-flow, not per-IP). The combination is a credential-stuffing target if 8123 is exposed beyond the LAN.
- Long-lived tokens are permanent until manually revoked. They survive password changes. Rotate by deleting and re-creating.
- Service account pattern: create a dedicated "agent" user (Admin or limited per scope), give it its own token, revoke by deleting the user.

## Waxgourd (冬瓜) — "is this a separate HA instance?"

The user mentioned `http://192.168.31.250:8124` as "冬瓜后台". Investigation:

- **It's not a separate OS.** Waxgourd (冬瓜HAOS伴侣) is a third-party **HA management panel** — a React/Vite SPA that talks to the same HA instance at 8123 over its REST API.
- **Correct entry point is `/home/`, not `/`.** `http://192.168.31.250:8124/home/` returns the real SPA HTML with `<title>waxgourd loading...</title>` and a JS bundle at `/home/assets/index-<hash>.js`. `http://192.168.31.250:8124/` returns only a 40-byte redirect stub and will not render anything.
- **It has its own login endpoint.** Reverse-engineering the bundle shows `POST /v1/login` (relative to `/home/` → `http://192.168.31.250:8124/v1/login`). The endpoint accepts HA credentials and returns its own session/token. The exact request body schema wasn't determined in this session — common `{username, password}` variants returned HTTP 400 "请求错误".
- **Browser headless may not render it.** The SPA uses `<script type="module" crossorigin>`; some headless browser stacks silently fail to load the module, leaving a blank page even though `curl` can fetch the bundle. If the user says the page works in their browser but the agent sees blank, trust the user and probe the backend API directly with `curl`.
- The agent doesn't need a separate "waxgourd takeover" — when 8123 is under agent control (via long-lived token), waxgourd is just another consumer of the same HA API. But to log into the panel itself, use the `/home/` URL and the user's HA credentials.

If `8124` is open to LAN and the user has not installed waxgourd as an add-on, ask whether it's running as a separate container (a `docker ps | grep waxgourd` on the PVE host or fnOS will show it).

## Reference

- HA auth flow spec: https://developers.home-assistant.io/docs/auth_api/
- HA REST API: https://developers.home-assistant.io/docs/api/rest/
