# Production Deployment Guide

This document describes how to deploy this application on a Linux VPS in production.

## Production Architecture

This app needs these long-running components:

1. Web app process running FastAPI via Uvicorn
2. ARQ worker process for queued emails and scheduled jobs
3. MySQL database
4. Redis server for rate limiting and job queue storage
5. Nginx reverse proxy in front of the app

Important: the web process and the worker are separate processes. If the worker is down, email-dependent flows still proceed and email payloads are persisted for deferred retries; delivery is delayed until worker and queue connectivity recover.

## What The App Loads From Environment

The app reads its configuration from `.env` through [app/config.py](app/config.py).

Required settings:

```env
APP_NAME=FastAuth
APP_URL=https://your-domain.com
DEBUG=false
SECRET_KEY=use-a-long-random-secret

DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=fastauth
DB_USER=fastauth
DB_PASSWORD=strong-db-password

REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

SESSION_COOKIE_NAME=session_id
SESSION_MAX_AGE=604800

MAIL_USERNAME=your-smtp-user
MAIL_PASSWORD=your-smtp-password
MAIL_FROM=noreply@your-domain.com
MAIL_FROM_NAME=FastAuth
MAIL_SERVER=smtp.your-provider.com
MAIL_PORT=587
MAIL_STARTTLS=true
MAIL_SSL_TLS=false

LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_MINUTES=15
ACCOUNT_PURGE_DAYS=30
```

Production rules:

1. Set `DEBUG=false`
2. Set `APP_URL` to the real public HTTPS URL
3. Use a strong random `SECRET_KEY`
4. Use a real SMTP provider; queued email still depends on valid SMTP credentials

## VPS Packages

On Ubuntu or Debian, install the base services first:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx redis-server mysql-server build-essential pkg-config default-libmysqlclient-dev
```

If your host already provides MySQL or Redis separately, you can skip the local packages and point `.env` to the managed services instead.

## Create App Directory

Example layout:

```bash
sudo mkdir -p /opt/fasthtmx
sudo chown -R "$USER":"$USER" /opt/fasthtmx
cd /opt/fasthtmx
```

Copy the project into `/opt/fasthtmx`.

## Python Environment And Dependencies

```bash
cd /opt/fasthtmx
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Database Setup

Create a production database and user in MySQL:

```sql
CREATE DATABASE fastauth CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'fastauth'@'localhost' IDENTIFIED BY 'strong-db-password';
GRANT ALL PRIVILEGES ON fastauth.* TO 'fastauth'@'localhost';
FLUSH PRIVILEGES;
```

Then run migrations:

```bash
cd /opt/fasthtmx
source .venv/bin/activate
python -m alembic upgrade head
```

## Redis Setup

Enable and start Redis:

```bash
sudo systemctl enable redis-server
sudo systemctl start redis-server
```

If Redis is password protected, set `REDIS_PASSWORD` in `.env`.

## Create The Production .env File

Use `.env.example` as the base and create the real `.env`:

```bash
cd /opt/fasthtmx
cp .env.example .env
```

Then edit `.env` for production values.

Minimum production changes:

1. `APP_URL=https://your-domain.com`
2. `DEBUG=false`
3. Real MySQL credentials
4. Real Redis settings
5. Real SMTP settings
6. Strong `SECRET_KEY`

## Manual Start Commands

Before setting up `systemd`, verify the app manually.

Start the web process:

```bash
cd /opt/fasthtmx
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Start the worker in another shell:

```bash
cd /opt/fasthtmx
source .venv/bin/activate
arq app.worker.WorkerSettings
```

Do not use `--reload` in production.

## systemd Services

Use `systemd` to keep both processes alive.

### Web Service

Create `/etc/systemd/system/fasthtmx-web.service`:

```ini
[Unit]
Description=FastHTMX Web App
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/fasthtmx
EnvironmentFile=/opt/fasthtmx/.env
ExecStart=/opt/fasthtmx/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Worker Service

Create `/etc/systemd/system/fasthtmx-worker.service`:

```ini
[Unit]
Description=FastHTMX ARQ Worker
After=network.target redis-server.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/fasthtmx
EnvironmentFile=/opt/fasthtmx/.env
ExecStart=/opt/fasthtmx/.venv/bin/arq app.worker.WorkerSettings
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start them:

```bash
sudo systemctl daemon-reload
sudo systemctl enable fasthtmx-web fasthtmx-worker
sudo systemctl start fasthtmx-web fasthtmx-worker
```

Check status:

```bash
sudo systemctl status fasthtmx-web
sudo systemctl status fasthtmx-worker
```

View logs:

```bash
sudo journalctl -u fasthtmx-web -f
sudo journalctl -u fasthtmx-worker -f
```

## Nginx Reverse Proxy

Create `/etc/nginx/sites-available/fasthtmx`:

```nginx
server {
    listen 80;
    server_name your-domain.com www.your-domain.com;

    location /static/ {
        alias /opt/fasthtmx/static/;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_read_timeout 60s;
    }
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/fasthtmx /etc/nginx/sites-enabled/fasthtmx
sudo nginx -t
sudo systemctl reload nginx
```

## HTTPS With Let's Encrypt

Install Certbot:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

Issue the certificate:

```bash
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

After HTTPS is enabled:

1. Keep `DEBUG=false`
2. Keep `APP_URL` on `https://...`
3. Reconfirm cookies work correctly over HTTPS

The app already sets secure cookies automatically when `DEBUG=false`.

## Health Checks And Smoke Tests

App health:

```bash
curl -i http://127.0.0.1:8000/healthz
```

Queue connectivity health:

```bash
curl -i http://127.0.0.1:8000/healthz/queue
```

Expected behavior:

1. `/healthz` should return `200`
2. `/healthz/queue` should return `200` when the web app can reach Redis
3. Queue health alone does not prove the worker is consuming jobs; check the worker service too

Recommended smoke test after deployment:

1. Register a new user
2. Confirm a verification email is queued and delivered
3. Trigger forgot-password
4. Confirm the reset email is delivered
5. Open the admin queue status page and inspect recent email job results

## Required Ongoing Operations

When you deploy updates:

```bash
cd /opt/fasthtmx
git pull
source .venv/bin/activate
pip install -r requirements.txt
python -m alembic upgrade head
sudo systemctl restart fasthtmx-web fasthtmx-worker
```

If dependencies did not change, `pip install -r requirements.txt` can still be run safely.

## Failure Modes To Watch

### Web Up, Worker Down

Symptoms:

1. Pages load normally
2. Email-related actions fail or stop delivering mail
3. Redis may still look healthy

Check:

```bash
sudo systemctl status fasthtmx-worker
sudo journalctl -u fasthtmx-worker -n 100 --no-pager
```

### Redis Down

Symptoms:

1. Rate limiting breaks
2. Email queueing fails
3. `/healthz/queue` returns `503`

Check:

```bash
sudo systemctl status redis-server
redis-cli ping
```

### MySQL Down

Symptoms:

1. Login and dashboard routes fail
2. Worker jobs that need DB access fail

Check:

```bash
sudo systemctl status mysql
mysql -u fastauth -p -e "SHOW DATABASES;"
```

### SMTP Misconfigured

Symptoms:

1. Jobs queue successfully
2. Worker logs show email job failures

Check worker logs and re-verify `MAIL_*` values.

## Security Baseline

At minimum:

1. Use HTTPS only in production
2. Use a strong random `SECRET_KEY`
3. Use least-privileged MySQL credentials
4. Restrict firewall access so only Nginx is public
5. Do not expose MySQL or Redis publicly unless necessary
6. Keep the VPS updated with security patches
7. Protect `.env` file permissions

Example permission hardening:

```bash
chmod 600 /opt/fasthtmx/.env
```

## Backup Recommendations

At minimum, back up:

1. MySQL database
2. Production `.env`
3. Nginx config
4. systemd unit files

Redis queue state is usually ephemeral and should not be your primary source of record. The durable records for this app are in MySQL.

## Production Readiness Checklist

Before going live, confirm all of the following:

1. `.env` exists with production values
2. `DEBUG=false`
3. `APP_URL` points to the public HTTPS domain
4. MySQL is reachable
5. Redis is reachable
6. SMTP credentials are valid
7. Alembic migrations are applied
8. `fasthtmx-web` service is running
9. `fasthtmx-worker` service is running
10. Nginx is proxying traffic correctly
11. TLS certificate is installed
12. `/healthz` returns `200`
13. `/healthz/queue` returns `200`
14. A real verification email can be sent successfully

## Important Runtime Commands

Web service logs:

```bash
sudo journalctl -u fasthtmx-web -f
```

Worker logs:

```bash
sudo journalctl -u fasthtmx-worker -f
```

Restart both app processes:

```bash
sudo systemctl restart fasthtmx-web fasthtmx-worker
```

Restart Redis:

```bash
sudo systemctl restart redis-server
```

Restart MySQL:

```bash
sudo systemctl restart mysql
```