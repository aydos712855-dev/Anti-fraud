# YouTrade Antifraud System

An anti-fraud system for a prop-trading platform. It ingests trading activity from Match-Trader, runs rule-based and ML-based checks, calculates a risk score for each account, finds links between accounts, reviews payout requests, and sends alerts to Telegram and email.

## Stack

| Layer      | Technology                                                  |
|------------|-------------------------------------------------------------|
| API        | FastAPI + Uvicorn (Python 3.11)                             |
| Database   | PostgreSQL 15 with TimescaleDB                              |
| Queue      | Redis 7 + Celery (worker and beat)                          |
| ML         | scikit-learn, pandas, NumPy, SciPy, NetworkX                |
| Alerts     | Telegram Bot API, SMTP                                      |
| Frontend   | Single-page app (`frontend/index.html`) served by FastAPI   |

## Project structure

```
.
├── docker-compose.yml        # postgres, redis, backend, celery_worker, celery_beat
├── .env.example              # environment variable template
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── seed.py               # fills the DB with demo data
│   ├── db/init.sql           # schema, applied on first Postgres start
│   └── app/
│       ├── main.py           # FastAPI entrypoint
│       ├── config.py         # settings (loaded from env)
│       ├── db.py             # async SQLAlchemy session
│       ├── celery_app.py     # Celery app + beat schedule
│       ├── api/              # auth + REST routes
│       ├── models/           # ORM models
│       ├── modules/
│       │   ├── rules/        # rule engine
│       │   ├── ml/           # anomaly, bot and sync-trading detectors
│       │   ├── scoring/      # risk score calculator
│       │   ├── graph/        # account link graph
│       │   ├── payout/       # payout checks
│       │   └── alerts/       # Telegram / email delivery
│       └── tasks/            # Celery tasks: ingestion, scoring, ML training
└── frontend/
    └── index.html
```

## Getting started

Requirements: Docker and Docker Compose.

1. Create your environment file and fill in real values:

   ```bash
   cp .env.example .env
   ```

   | Variable | Purpose |
   |----------|---------|
   | `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | Database and Redis passwords |
   | `SECRET_KEY` | JWT signing key (generate with `openssl rand -hex 32`) |
   | `MATCH_TRADER_API_URL`, `MATCH_TRADER_API_KEY` | Trading platform API |
   | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram alerts |
   | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | Email alerts |
   | `ECONOMIC_CALENDAR_API_KEY` | Economic calendar sync |
   | `REFERENCE_FEED_URL` | Reference quotes feed |
   | `ML_RETRAIN_CRON` | ML retraining schedule |
   | `APP_ENV`, `LOG_LEVEL` | App settings |

2. Start everything:

   ```bash
   docker compose up -d --build
   ```

3. (Optional) Load demo data:

   ```bash
   docker compose exec backend python seed.py
   ```

Once running:

- App UI: http://localhost:8000
- API docs (Swagger): http://localhost:8000/docs
- Health check: http://localhost:8000/health

Exposed ports: `8000` (API), `5433` (Postgres), `6379` (Redis).

> **Users:** creating users (`POST /api/v1/users`) requires an existing admin, and no admin is created automatically. Insert the first admin into the `users` table by hand.

## API

All routes are under `/api/v1`:

| Prefix       | Description                       |
|--------------|-----------------------------------|
| `/users`     | Login (`/users/login`), user management |
| `/accounts`  | Trading accounts                  |
| `/alerts`    | Fraud alerts                      |
| `/payouts`   | Payout requests and checks        |
| `/rules`     | Detection rules and backtesting   |
| `/graph`     | Account link graph                |
| `/scoring`   | Risk scores                       |
| `/reports`   | Reports                           |

Authentication uses a JWT bearer token returned by `POST /api/v1/users/login` (OAuth2 password form).

## Background jobs

Celery beat schedule (`backend/app/celery_app.py`):

| Task | Schedule |
|------|----------|
| Ingest trades from Match-Trader | every minute |
| Recompute risk scores for changed accounts | every 5 minutes |
| Retrain ML models | daily at 03:00 UTC |
| Sync economic calendar | daily at 00:05 UTC |

Worker queues: `default`, `ml`, `alerts`.

## Useful commands

```bash
docker compose logs -f backend          # API logs
docker compose logs -f celery_worker    # worker logs
docker compose down                     # stop
docker compose down -v                  # stop and delete DB/Redis/model volumes
```
