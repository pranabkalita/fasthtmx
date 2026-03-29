# FastAuth (FastAPI + HTMX + Tailwind + MySQL)

Initial implementation scaffold with core auth/account features:

- User registration
- Email verification
- Email verification resend
- User login/logout
- Password reset
- Verified user dashboard
- Profile page
- Profile update with email re-verification
- Password change
- Optional 2FA (TOTP)
- 2FA backup recovery codes
- Account deactivation (soft delete marker)
- Session management (list, revoke one, logout all devices)
- Login attempt tracking and lockout logic
- Redis-backed request rate limiting for auth flows
- CSRF protection for form and HTMX requests
- Background worker for deactivated-account purge
- Queued email delivery through ARQ worker
- Admin audit log view
- Global top progress bar for HTMX requests

## Run

1. Ensure MySQL and Redis are running and match `.env` values.
2. Install dependencies: `pip install -r requirements.txt`
3. Run DB migrations:
   `python -m alembic upgrade head`
4. Start app:
   `uvicorn app.main:app --reload`
5. Start background worker (separate terminal):
   `arq app.worker.WorkerSettings`
6. Run tests:
   `python -m pytest -q`

## Health Checks

- App liveness: `GET /healthz`
- Queue health: `GET /healthz/queue`

`/healthz/queue` verifies that the web process can reach the ARQ Redis queue. If it returns `503`, queued email delivery will fail until Redis and/or queue connectivity is restored.

## Deployment Notes

Run web and worker as separate long-lived processes in production.

Example `systemd` services:

```ini
# /etc/systemd/system/fasthtmx-web.service
[Unit]
Description=FastHTMX Web App
After=network.target

[Service]
WorkingDirectory=/opt/fasthtmx
EnvironmentFile=/opt/fasthtmx/.env
ExecStart=/opt/fasthtmx/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/fasthtmx-worker.service
[Unit]
Description=FastHTMX ARQ Worker
After=network.target

[Service]
WorkingDirectory=/opt/fasthtmx
EnvironmentFile=/opt/fasthtmx/.env
ExecStart=/opt/fasthtmx/.venv/bin/arq app.worker.WorkerSettings
Restart=always

[Install]
WantedBy=multi-user.target
```

Example `docker-compose` split:

```yaml
services:
   web:
      build: .
      command: uvicorn app.main:app --host 0.0.0.0 --port 8000
      env_file: .env
      depends_on:
         - redis

   worker:
      build: .
      command: arq app.worker.WorkerSettings
      env_file: .env
      depends_on:
         - redis

   redis:
      image: redis:7-alpine
```

## Notes

- Schema is managed via Alembic migrations (`alembic/versions`).
- Email delivery is queued through ARQ and sent by the background worker using SMTP settings from `.env`.
- If the worker is not running, auth/profile flows that need outbound email will fail to queue delivery.
- Queue and worker logs now include email template names and ARQ job ids so enqueue events can be correlated with downstream worker execution.
- Worker retries are explicit: queued email jobs can retry up to 3 times with a 120s timeout; the purge job runs once with a 300s timeout.
- Account purge retention is configured with `ACCOUNT_PURGE_DAYS`.
- Initial test coverage is included for CSRF, rate limiting utility, and purge job behavior.
