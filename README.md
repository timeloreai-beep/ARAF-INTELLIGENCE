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
- Firebase Web API key for provider-mediated password verification
- `KYC_PII_ENCRYPTION_KEY` and Gmail bridge URL/secret for backend KYC/OTP
- Shoonya API key, UID, password and TOTP secret
- CoinDCX API key and secret

Shoonya TOTP values are generated in Python with `pyotp`. `SHOONYA_TOTP_SECRET` is a secret configuration value and is only requested/added by the operator; it is never hard-coded.

The Ghost Admin password is `ADMIN_PASSWORD`; use a long, unique secret. `APP_SECRET` or `JWT_SECRET` must also be set to a strong random value so the short-lived admin token can be signed. Do not expose either value to browser code.

## News priority engine

The backend normalizes and deduplicates results from NewsAPI, Finnhub, Alpha Vantage, Marketaux, NewsData, Coinpaprika, Google News RSS, Moneycontrol RSS, Economic Times RSS, and CoinDesk RSS. Coinpaprika's documented free API does not provide `/v1/news`; its keyless adapter instead combines Bitcoin event updates with significant ticker moves as crypto market signals. CoinDesk replaces CoinGecko because the CoinGecko news endpoint requires a Pro plan. Google News RSS is disabled by default (`NEWS_GOOGLE_RSS_ENABLED=false`); only enable it after confirming that your intended use complies with its feed terms.

Polling is concurrent but rate-aware. Provider intervals are controlled by `NEWSAPI_POLL_SECONDS`, `FINNHUB_POLL_SECONDS`, `ALPHA_VANTAGE_POLL_SECONDS`, `MARKETAUX_POLL_SECONDS`, `NEWSDATA_POLL_SECONDS`, `COINPAPRIKA_POLL_SECONDS`, and `RSS_POLL_SECONDS`. Coinpaprika uses its public API without an API key. Crypto scanning uses public Binance data first, then falls back to CoinGecko and Coinpaprika if a source is unavailable. Their defaults account for typical provider quotas, not a guarantee of any plan's limits. RSS update timing and free-plan delays mean this is not a real-time news wire. The priority lock is triggered by newly discovered, recent macro/geopolitical and crypto-crash/whale headlines and defaults to `NEWS_PRIORITY_LOCK_SECONDS=300`. In production, the background poller runs only when `NEWS_AUTO_POLL_ENABLED=true`; in development, requests to the news feed poll sources when due.

## Ghost Admin and risk enforcement

Open `/admin-ghost-control` and authenticate with `ADMIN_PASSWORD`. The page is not linked from the normal terminal. Its short-lived token is held in session storage. Auto-trade start/stop and the separate global kill switch are persisted to PostgreSQL; an active global kill switch blocks all new entries, including manual paper overrides. Admin overrides still use the standard paper-order endpoint and cannot skip the five-layer auto-entry gates or server risk checks.

`MAX_DAILY_TRADES`, `MAX_PER_TRADE_RISK_PCT`, and `MAX_DAILY_LOSS_AMOUNT` are server-side ceilings; the daily loss amount is denominated in INR and defaults to ₹2,500 per user. Users can configure their exact INR loss limit and daily trade count in Settings. Saving risk settings starts a persisted 24-hour lock. During that window the user cannot alter the limits or raise/reset the P&L watermark. A loss-limit breach starts its own persisted 24-hour lock; only the Ghost Admin Panel can clear it early. Active Redis risk state is keyed per user and survives trading-day boundaries. The terminal must load risk and global kill-switch state from the server before enabling trading; a failed fetch blocks entries. The `/risk/pnl` endpoint derives its watermark from server-persisted paper-position closures and ignores client-reported amounts. Auto-trade requires at least 90% confluence; manual opportunities start at 70%. Paper positions can be fast-exited from the terminal; an exit blocks the same user/setup for 24 hours. Render applies `alembic upgrade head` before starting Uvicorn. A fusion score is a screening threshold, not a statistical guarantee of a win rate. Live trading remains disabled regardless of `LIVE_TRADING_ENABLED`.

The terminal theme, chart timeframe, and news preferences are stored locally and synchronized to configured PostgreSQL, Redis, and Firestore stores. If none of those stores accepts an update, the API reports an error instead of claiming synchronization succeeded.

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

The terminal UI (`static/index.html`, `static/app.js`, and `static/style.css`) is served by FastAPI at `/`, `/app.js`, and `/style.css`. Supporting assets not yet placed in `static/` are served from `LEGACY_JS_BACKUP/`. The frontend's `meta[name="araf-api-base"]` is blank by default, so it uses the page's origin; set it to the API origin when hosting the UI separately and add that UI origin to `CORS_ORIGINS`.

Migration is deliberately phased to avoid cutting off existing features. The splash health check, news feed, risk configuration/P&L, paper-order entry, preferences, read-only broker quote tests, and Trader UID resolution call FastAPI. The corresponding Python scanner routes are available, but not every legacy UI path has been switched over.

The additive Python quant API is `POST /engines/evaluate` (authenticated). It returns the 20-engine fusion report plus indicators and SMC features for explicitly closed candles. `GET /engines/replay` and `GET /engines/replay/export` provide a per-user, process-local replay journal. This is analysis-only: `autoTradeEligible` is informational, `executionEnabled` is always false, and the endpoint cannot place or authorize orders. Context fields such as universe breadth, news sentiment, portfolio positions, broker route, risk budget, and session status must be supplied by the caller; they are not yet sourced from trusted backend market/broker feeds. Missing required data blocks the report. True trade-aggressor tape and verified options-chain/IV/OI inputs remain unavailable/not applicable. The legacy browser fusion renderer remains active until its inputs and response behavior are migrated and regression-tested.

The quant response also exposes EMA, Wilder RSI/ATR, MACD, Bollinger Bands, Supertrend, FVG, displacement/level-break order blocks, liquidity sweeps, BOS/CHoCH, and trade-volume imbalance when buy/sell volume is present. Ordinary candle volume is not treated as footprint data.

## Server-side paper automation

`/automation/settings` stores per-user auto-trade enablement, daily INR capital budget, and market scope in the relational preferences row. The persisted daily budget ledger uses atomic reservations and is mirrored between the configured primary and Neon databases; failed mirror updates are recorded in the replication outbox for retry. Apply Alembic revision `20261124_005` (or simply upgrade to `head`) before using paper positions. The account toggle is subordinate to Ghost Admin's global auto-trade control and kill switch. Live execution remains forcibly disabled.

The terminal polls `/automation/opportunities` and `/automation/scan` evaluates a bounded candidate batch for either India or Crypto. In production, the backend can scan opted-in users in the background when `BACKGROUND_SCANNING_ENABLED=true` and relational persistence is configured: Crypto is scanned continuously at the configured interval, while India is scanned from 09:15–15:30 IST on weekdays that are not in the NSE/BSE 2026 holiday calendar. For another calendar year, set `INDIA_MARKET_HOLIDAYS` to that year's complete comma-separated ISO-date holiday list; unsupported years fail closed for Indian scanning and scheduled login. The Indian universe includes NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, and BANKEX plus high-volume NSE/BSE equities. Listed options are selected from Shoonya's active contract master; a stock falls back to cash-equity paper sizing only when no active option contract exists, never when the listed option fails spread/liquidity checks. Crypto candidates are ranked by Binance USDT turnover and cross-checked against CoinDCX futures quotes. The server cross-checks 1m, 5m, and 1h direction, closed-candle retail-trap/SMC signals, volume-profile confirmation, news state, risk, and server-calculated entry/SL/TP/quantity. Scores 70–79 create Standard manual alerts, 80–89 create High-Conviction manual alerts, and 90+ create Prime Apex alerts; only 90+ can auto-execute on paper with all user/admin/lifecycle controls enabled and minimum 1:2 R:R. Manual claims are one-use, server-sized, and expire after 15 seconds. Browser-generated scores cannot authorize an automated order; `/orders/place` additionally requires a matching server opportunity for every automated entry.

In production, `SHOONYA_DAILY_LOGIN_ENABLED=true` schedules a TOTP login for 08:45 IST on weekdays and stores the AES-GCM-encrypted session in Upstash Redis with an 18-hour TTL and a distributed login lock. An expired-session response triggers a serialized server re-login. Shoonya controls session validity; this cannot guarantee that a mobile-app login will not invalidate or conflict with the API session. Scheduled/refresh login only runs when Shoonya credentials and Redis are configured. All resulting entries remain paper-only.

The authenticated read-only `POST /options/select` selector downloads Shoonya's NFO/BFO contract masters, uses their exact trading symbols/tokens/lot sizes and listed expiries, and supports NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX, and listed NFO stock options. It chooses the nearest active expiry from the master (not a hardcoded weekday), then the most liquid qualifying ATM/ITM CE for bullish or PE for bearish signals. Selection requires positive bid/ask/volume and a spread no wider than 2% by default (request-configurable up to 10%). The Indian Apex scan uses this selector for execution-eligible opportunities and sizes paper risk using option premium candles; it fails closed if no listed contract passes the spread/liquidity gate. This service does not place live orders.

The scan deliberately fails closed when credentials, candle/quote freshness, timeframe alignment, volume profile, news, or any required engine gate is unavailable. Some broader research inputs (including complete macro/correlation history, trade-aggressor footprint data, and verified options-chain/IV/OI data) are not yet supplied by trusted server feeds; candidates requiring them are blocked rather than promoted. Index candidates require listed options; liquid stock candidates may use cash-equity paper sizing only when no active option expiry/contract exists. The paper-position manager updates trailing stops and closes on server-observed stop/target prices; automatic entries stay blocked until the manager and relational database report ready. Do not treat the browser scanner or `/engines/evaluate` client context as execution-authoritative.

In `static/app.js`, `secureApiCall` uses an explicit FastAPI target allowlist. Trader UID resolution tries FastAPI first and falls back to the Worker only for availability or migration-miss responses; credential rejection is never retried against the Worker. Legacy calls that remain on the Worker are routed through the same-origin FastAPI `/legacy/{target}` allowlisted proxy, avoiding browser-to-Worker CORS requests. Configure the fixed Worker origin with `LEGACY_WORKER_URL`; requests to unapproved targets are rejected.

The backend now includes persisted OTP challenge, PIN credential, KYC, and Trader UID APIs, plus authenticated Shoonya (server-side `pyotp`) and CoinDCX read-only authentication probes. OTP delivery uses the configured Gmail bridge; KYC PII is AES-GCM encrypted before relational persistence; PIN hashes use PBKDF2-SHA256 with Redis-backed lockout. These paths are covered by mocked regression tests only; the tests do not contact live identity or broker services.

The signup/KYC/PIN/profile and broker-vault UI flows remain on the Worker during this phase. The legacy admin still approves KYC in Worker KV, and legacy broker balance/holdings flows consume the Worker vault; switching those callers before approval/vault synchronization would strand approved users or disconnect feeds. Do not cut these over until those dependent workflows and their response contracts are migrated together.

Backend auth routes include `/auth/otp/send`, `/auth/otp/verify`, `/auth/kyc`, `/auth/pin`, `/auth/pin/verify`, `/auth/master-user`, and `/auth/logout`. Broker auth probes are `POST /brokers/shoonya/login` and `POST /brokers/coindcx/login`; they are authenticated, require approved KYC state in the relational user record, and perform no order execution. Apply Alembic revision `20261122_003` before using the auth persistence routes.

The following active UI operations still use the legacy Worker through the FastAPI proxy and must remain available until equivalent FastAPI routes and regression tests are complete:

- Identity and user data: legacy fallback for Trader UID lookup, OTP/PIN actions, `submit_kyc`, `get_master_user`, `save_master_user`, and logout.
- Market data: `search_stock`, `asset_details`, `historical_candles`, `public_market_feed`, `master_market_feed`, and `coindcx_market_feed`.
- Broker and execution: Shoonya UI login, broker vault/relay operations, `broker_positions`, balances, holdings, orders, `fast_exit`, and `reconcile_order`.
- Analysis and content: `deep_scanner`, `ai_guardian`, `translate_text`, `news_article`, `ipo_feed`, and `ipo_news_feed`.
- Other automation and legacy UI operations not yet migrated: the Worker still owns the listed legacy feed, broker-vault, and content features; auto-trade settings, scanner-to-engine paper automation, opportunity cards, and manual card claims now use FastAPI.

Do not remove the Worker fallback until the above operations have backend equivalents and a parity test confirms their response shapes and behavior. Firebase client configuration and other public client identifiers are not server secrets; API credentials, signing secrets, broker passwords/TOTP seeds, and service-account data must stay server-side.

The Settings page provides authenticated read-only quote tests:

- `POST /brokers/test/shoonya/quote` with `{"symbol":"2885","exchange":"NSE"}`. Shoonya's `symbol` is its instrument token.
- `POST /brokers/test/coindcx/quote` with `{"symbol":"BTCUSDT"}`.
- `GET /brokers/test/shoonya/positions` and `GET /brokers/test/coindcx/positions` are authenticated read-only snapshots and update the relational position table plus MongoDB history.

Both require `Authorization: Bearer <Firebase ID token>`. Shoonya server credentials and TOTP seed stay in backend environment variables; the browser never sends them. Quote responses include persistence status.
