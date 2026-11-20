# ARAF Intelligence Python Backend

This backend is the FastAPI migration of the existing JavaScript terminal.

## Start locally

```powershell
Copy-Item .env.example .env
python -m pip install -r requirements.txt
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

This build is paper-only. `LIVE_TRADING_ENABLED` is forcibly held at `false`; an attempt to set it to `true` is ignored with a runtime warning. The broker test routes below only read quotes and never submit orders.

## Required secrets

Fill `.env` or deployment environment variables. Never commit real secrets.

- `APP_SECRET` or `JWT_SECRET`
- Supabase and/or Neon database URL
- MongoDB Atlas URL
- Upstash Redis URL
- Firebase service-account JSON
- Shoonya API key, UID, password and TOTP secret
- CoinDCX API key and secret

Shoonya TOTP values are generated in Python with `pyotp`. `SHOONYA_TOTP_SECRET` is a secret configuration value and is only requested/added by the operator; it is never hard-coded.

## Persistence services

- Supabase PostgreSQL is the relational primary; Neon PostgreSQL is the synchronous secondary. Typed writes go to both. If one database is unavailable, the successful side records a replication-outbox item; the API background worker retries it every 15 seconds after recovery. Responses expose partial/degraded status instead of claiming replication succeeded.
- Upstash Redis stores short-lived quote caches, order idempotency results, authenticated session state, and risk snapshots.
- MongoDB Atlas stores append-only quote/scanner/audit history.
- Firebase Admin verifies Firebase ID tokens. Firestore stores per-user push-device registrations and notification records; FCM delivers push notifications.

The app does not create PostgreSQL tables at startup. Run migrations before enabling relational persistence.

## Relational database migrations

Set `SUPABASE_DATABASE_URL` (primary) and optionally `NEON_DATABASE_URL` (backup) in the environment or `.env`. Do not commit either value.

From the project root:

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
```

With both URLs configured, `upgrade head` migrates both databases. It stops on a migration failure rather than reporting success for only one. To preview SQL without connecting:

```powershell
python -m alembic upgrade head --sql
```

The offline SQL preview uses the primary configured URL; apply it to Neon separately if manually managing migrations.

To create a later migration after changing `backend/app/models.py`:

```powershell
python -m alembic revision --autogenerate -m "describe change"
python -m alembic upgrade head
```

`PersistenceStatus` exposes `primary_applied`, `backup_applied`, `replicated`, `degraded`, `partial_write`, `outbox_queued`, and non-secret error categories. Outbox writes are idempotent and replayable; monitor the service logs and the `replication_outbox` table for unresolved entries.

## Frontend and read-only feed checks

FastAPI serves the existing terminal at `/`, its scripts at `/app.js` and `/engines.js`, and the admin HTML at `/admin`. The frontend's `meta[name="araf-api-base"]` is blank by default, so it uses the page's origin; set it to the API origin when hosting the UI separately and add that UI origin to `CORS_ORIGINS`.

The Settings page provides authenticated read-only quote tests:

- `POST /brokers/test/shoonya/quote` with `{"symbol":"2885","exchange":"NSE"}`. Shoonya's `symbol` is its instrument token.
- `POST /brokers/test/coindcx/quote` with `{"symbol":"BTCUSDT"}`.
- `GET /brokers/test/shoonya/positions` and `GET /brokers/test/coindcx/positions` are authenticated read-only snapshots and update the relational position table plus MongoDB history.

Both require `Authorization: Bearer <Firebase ID token>`. Shoonya server credentials and TOTP seed stay in backend environment variables; the browser never sends them. Quote responses include persistence status.

Only the new FastAPI endpoints are connected by this increment. Existing terminal actions outside these endpoints still call the legacy Worker and have not yet been migrated. `/auth/login` and `/auth/pin/verify` remain unimplemented; the feed-test UI uses the existing Firebase sign-in session instead.
