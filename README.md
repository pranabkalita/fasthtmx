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

## Notes

- Schema is managed via Alembic migrations (`alembic/versions`).
- Email currently uses SMTP settings from `.env`.
- Account purge retention is configured with `ACCOUNT_PURGE_DAYS`.
- Initial test coverage is included for CSRF, rate limiting utility, and purge job behavior.
