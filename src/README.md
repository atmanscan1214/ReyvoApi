# Reyvo API

FastAPI backend for **Reyvo**, a multi-tenant outbound growth platform for staffing agencies.

This service is the backend/API foundation described in `../Reyvo_Detailed_Design_Document.md`. It is intentionally a modular monolith: synchronous request handling lives in FastAPI routers, domain rules live in `reyvo/services`, and slow/provider-facing work is designed to move to `reyvo/workers` and `reyvo/integrations`.

For full installation, local PostgreSQL setup, the demo account, WorkOS SSO, and troubleshooting, see [`../DEVELOPER_GUIDE.md`](../DEVELOPER_GUIDE.md).

## Stack

| Concern | Choice |
|---|---|
| Runtime | Python 3.12+ (tested on 3.14) |
| API | FastAPI + Pydantic v2 |
| Database | PostgreSQL 16+ + SQLAlchemy 2.0 async |
| Migrations | Alembic |
| Auth | JWT access/refresh tokens, Argon2id password hashing |
| Tenant isolation | Application-scoped queries plus PostgreSQL RLS helpers |
| Email providers | Gmail API and Microsoft Graph adapters |
| AI | Provider-agnostic adapter (OpenAI-compatible endpoint) |
| Billing | Stripe (optional at runtime) |
| Tests | pytest + httpx + SQLite (aiosqlite) |

## Layout

```text
src/
  reyvo/
    main.py                 # app factory, middleware, exception handlers
    api/deps.py             # auth, tenant context, role dependencies
    api/v1/                 # HTTP routers by domain
    core/                   # config, security, errors, tenancy, pagination
    db/                     # SQLAlchemy base, session, enums, models
    schemas/                # request/response models
    services/               # domain logic
    integrations/email/     # Gmail and Microsoft Graph adapters
    integrations/ai/        # drafting adapter
    integrations/billing/   # Stripe gateway
    workers/                # background sender skeleton
  alembic/                  # migrations
  tests/                    # API and tenant-isolation tests
  scripts/                  # local DB setup, demo seed, RLS policies
```

## Quick start

```bash
cd src
cp .env.example .env
uv sync
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn reyvo.main:app --reload --port 8000
```

On Windows, psycopg's async driver cannot use the default Proactor event loop. Start the
API with the bundled Selector-loop factory instead:

```powershell
uv run uvicorn reyvo.main:app --reload --port 8000 --loop reyvo.dev:selector_loop_factory
```

Open:

- Scalar API reference: <http://localhost:8000/docs>
- OpenAPI JSON: <http://localhost:8000/openapi.json>
- Liveness: <http://localhost:8000/health>
- Readiness: <http://localhost:8000/health/ready>

### Local demo account

After applying migrations, create a ready-to-use owner account:

```powershell
uv run python scripts\seed_demo.py
```

```text
email:    demo@reyvo.com
password: ReyvoDemo123!
tenant:   Reyvo Demo Agency
```

Log in with `POST /api/v1/auth/login` or use the **Authorize** button in the Scalar UI.

### Local PostgreSQL setup

`scripts/setup_local_db.sql` creates the `reyvo` role/database expected by the default
`DATABASE_URL`. Run it once as a PostgreSQL superuser:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f scripts\setup_local_db.sql
uv run alembic upgrade head
```

For a quick local run without PostgreSQL, set:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./reyvo.db
AUTO_CREATE_TABLES=true
```

`AUTO_CREATE_TABLES` is for local development only. Use Alembic in deployed environments.

## Tests

```bash
cd src
uv run pytest
```

The test suite uses an in-memory SQLite database and covers health, registration/login, tenant isolation, contacts, suppressions, the campaign lifecycle, and WorkOS SSO enrollment/configuration.

## Core API surface

| Area | Endpoints |
|---|---|
| Auth | `POST /api/v1/auth/register`, `/login`, `/refresh`, `GET /me`; WorkOS SSO at `/auth/sso/*` |
| Tenant | `GET/PATCH /api/v1/tenants/current`, members, usage, audit logs |
| Companies | CRUD under `/api/v1/companies` |
| Contacts | CRUD, CSV import/export, lists under `/api/v1/contacts` and `/api/v1/lists` |
| Suppressions | List/add/remove under `/api/v1/suppressions` |
| Inboxes | OAuth start/callback, list, update caps, disconnect under `/api/v1/email-accounts` |
| Campaigns | CRUD, submit/approve/launch/pause/resume, preview, enrollments |
| Replies | List/update replies, create tasks |
| Pipeline | Opportunities and tasks |
| Dashboard | `GET /api/v1/dashboard/summary` |
| Admin | Tenant list/status, platform audit under `/api/v1/admin` |
| Billing | Plans, subscription, checkout, Stripe webhook |
| Public | Unsubscribe page/action at `/api/v1/u/{token}` |

All tenant-scoped routes derive the tenant from the access token. Clients cannot select a tenant with a request parameter.

## Authentication flow

1. `POST /api/v1/auth/register` creates a user, a tenant, and an owner membership.
2. The response includes an access token and a refresh token.
3. Send `Authorization: Bearer <access_token>` on subsequent requests.
4. `POST /api/v1/auth/refresh` exchanges a refresh token for a new pair.
5. If a user belongs to multiple tenants, `POST /auth/login` requires `tenant_slug`.

Passwords are hashed with Argon2id. Provider tokens are encrypted with Fernet envelope encryption before storage.

### Enterprise SSO with WorkOS

Local email/password login stays available for every account. Tenants can additionally enable
WorkOS SSO without changing how other tenants sign in:

1. Create an Organization (and optionally an SSO Connection) in the WorkOS dashboard.
2. Set `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, and `WORKOS_REDIRECT_URI` on the API.
3. As an owner or admin, link the workspace:
   `PATCH /api/v1/tenants/current/sso` with
   `{"workos_organization_id": "org_...", "workos_sso_connection_id": "conn_..."}`.
4. `GET /api/v1/tenants/current/sso` reports whether SSO is available and enabled; `DELETE` unlinks it.
5. Clients start sign-in with
   `GET /api/v1/auth/sso/authorize?tenant_slug=<slug>&login_hint=<email>`.
   The response contains a WorkOS `authorization_url` and a signed `state`.
6. WorkOS redirects back to `GET /api/v1/auth/sso/callback?code=...&state=...`. If the
   authorize call supplied an allow-listed `return_to`, the callback redirects there with
   tokens in the URL fragment; otherwise it returns a token pair as JSON. SPA and server
   clients can also exchange the code directly with `POST /api/v1/auth/sso/callback`.

On the first SSO sign-in the API provisions the user (or links an existing account by email),
creates a membership, and maps the WorkOS role slug (`owner`, `admin`, or member). `state` is a
short-lived signed JWT, and the returned WorkOS profile must match the tenant's configured
organization/connection. `return_to` must be same-origin with `CORS_ORIGINS` or
`APP_PUBLIC_URL`.

## Tenant isolation

- Every tenant-scoped table has a `tenant_id` column.
- Every query in the service layer filters by the tenant from the authenticated principal.
- `scripts/enable_rls.sql` contains the PostgreSQL row-level security policies expected in production.
- The app sets `app.tenant_id` per request with `set_config(..., true)`; RLS policies use `current_setting('app.tenant_id')`.
- Tests exercise cross-tenant access attempts on read, write, and list endpoints.

## Background work

The HTTP API deliberately does **not** send bulk email inline. `reyvo/workers/sender.py` is the starting point for the send loop described in the design document. It is intentionally isolated from the request path so it can run as its own process and be scaled independently.

Planned worker jobs:

- `send.due` — schedule and send due enrollment steps.
- `sync.replies` — Gmail Pub/Sub and Microsoft Graph change notifications.
- `classify.reply` — rules + AI reply classification.
- `report.rollup` — daily campaign/tenant/inbox metrics.
- `retention.purge` — retention and deletion jobs.

## Configuration

See `.env.example`. Important settings:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing and derived encryption fallback |
| `TOKEN_ENCRYPTION_KEY` | Fernet key for OAuth token encryption (recommended in production) |
| `DATABASE_URL` | SQLAlchemy URL; use `postgresql+psycopg://...` |
| `AUTO_CREATE_TABLES` | Dev-only schema bootstrap |
| `GOOGLE_*` | Gmail OAuth client |
| `MICROSOFT_*` | Microsoft Graph OAuth client |
| `AI_*` | AI drafting endpoint |
| `WORKOS_*` | Optional WorkOS enterprise SSO (`API_KEY`, `CLIENT_ID`, `REDIRECT_URI`, `WEBHOOK_SECRET`) |
| `STRIPE_*` | Billing and webhook verification |
| `APP_PUBLIC_URL` | Used in unsubscribe links |

Generate a Fernet key:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Production notes

- Run behind a TLS-terminating proxy; set `ENVIRONMENT=production`.
- Set `AUTO_CREATE_TABLES=false` and apply migrations with `alembic upgrade head`.
- Enable the RLS policies in `scripts/enable_rls.sql`.
- Use a secrets manager for `SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, provider credentials, and Stripe keys.
- Configure a real email provider OAuth app; Google restricted scopes require verification.
- For enterprise SSO, register the WorkOS redirect URI (`/api/v1/auth/sso/callback`) and keep the API key in the secrets manager.
- Add Redis and a queue (Arq/Celery) before enabling the sender at volume.
- The current scaffold intentionally leaves the actual send loop and reply-sync webhooks as explicit extension points; do not enable production sending until those are implemented and tested.

## Status

Implemented in this scaffold:

- Application factory, settings, error envelope, request IDs, CORS
- Async DB session and model layer
- Registration/login/refresh/me
- Optional WorkOS enterprise SSO alongside local password login
- Tenant profile, members, usage, audit log read, SSO configuration
- Companies, contacts, CSV import/export, lists
- Suppressions and public unsubscribe
- Email account listing, OAuth URL generation/callback, encrypted token storage
- Campaign lifecycle, preflight, enrollment creation, preview rendering
- Replies read/update, tasks, opportunities
- Dashboard counts
- Platform admin tenant management
- Stripe plan/subscription read and webhook skeleton
- Alembic migrations and PostgreSQL RLS policy script
- Tests and Docker Compose (36 passing)

Explicit next steps:

- Implement the send worker and provider retry/rate-limit logic.
- Add Gmail Pub/Sub and Graph subscription webhook verification and reply ingestion.
- Add Redis-based rate limiting and token buckets.
- Add AI classification and approval queues.
- Automate RLS policy application in the migration/deploy pipeline.
- Add email notifications (verification, invites, billing).
