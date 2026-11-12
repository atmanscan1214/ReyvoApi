# Reyvo Developer Guide

**Project:** Reyvo — multi-tenant outbound growth platform for staffing agencies
**Component:** Backend API (`src/`)
**Last updated:** 10 November 2026
**Status:** Backend foundation complete; 36 tests passing; lint clean

This guide covers everything needed to install the tooling, run the API locally, understand the
architecture, work with the database, use local and WorkOS authentication, and extend the project.

Companion documents:

- [`Reyvo_Detailed_Design_Document.md`](Reyvo_Detailed_Design_Document.md) — product/design source of truth
- [`src/README.md`](src/README.md) — API reference and endpoint summary

---

## 1. What has been built

A modular-monolith backend API built with **FastAPI**, **SQLAlchemy 2.0 (async)**, **PostgreSQL**,
and **Alembic**, covering:

| Area | Implemented |
|---|---|
| Authentication | Registration, login, refresh, `/me`, JWT access/refresh tokens, Argon2id hashing |
| Enterprise SSO | Optional WorkOS SSO alongside local password login |
| Tenancy | Tenant profile, memberships, roles, usage, audit logs, tenant scoping |
| CRM | Companies, contacts, CSV import/export, contact lists, segments |
| Suppressions | Global/tenant suppressions and public unsubscribe links |
| Inboxes | Gmail/Microsoft OAuth start/callback, token encryption, sending caps |
| Email server | Brevo for platform/transactional email and campaign sending (ESP), alongside Gmail/Graph (reference stack; planned, not implemented) |
| AI drafting | Semantic Kernel prompt functions for per-recipient email drafts and grounded knowledge-base chat |
| Campaigns | CRUD, preflight checks, approval, launch/pause/resume, enrollments, preview |
| Replies | Reply listing/classification updates, task creation |
| Pipeline | Opportunities and tasks |
| Dashboard | Aggregate summary metrics |
| Admin | Platform-admin tenant management and audit log |
| Billing | Plans, subscription state, Stripe checkout/webhook skeleton |
| Background work | Isolated sender worker skeleton (not enabled for production sending) |
| API docs | Scalar UI at `/docs`, OpenAPI at `/openapi.json` |
| Quality | 36 pytest tests, Ruff formatting/linting |

> The actual bulk send loop, reply-sync webhooks, AI classification pipeline, and rate limiting
> remain explicit extension points. Do not enable production sending until those are complete.

---

## 2. Architecture at a glance

```text
Client / SPA
     │  HTTPS + Bearer JWT
     ▼
FastAPI app (reyvo.main)
     │
     ├── middleware: request ID, CORS, error envelope
     ├── api/v1/*        thin HTTP routers
     ├── api/deps.py     authentication, roles, tenant context
     ├── services/*      domain logic (auth, CRM, campaigns, SSO, billing)
     ├── schemas/*       Pydantic request/response validation
     ├── db/*            SQLAlchemy models, enums, async session
     └── integrations/*  Gmail, Microsoft Graph, Brevo, AI (Semantic Kernel), Stripe, WorkOS
                │
                ▼
        PostgreSQL (tenant-scoped rows, optional RLS)
```

Design principles:

- **Modular monolith.** Routers stay thin; business rules live in `reyvo/services`.
- **Tenant isolation.** The tenant always comes from the authenticated principal, never from a
  request parameter. The service layer filters every tenant-scoped query by `tenant_id`.
- **Async everywhere.** SQLAlchemy async sessions and async provider wrappers.
- **Workers are separate.** Sending and background sync do not run inline with HTTP requests.
- **Optional integrations.** WorkOS, Gmail/Microsoft, AI, Stripe, and Redis are not required to run
  the core API.

---

## 3. Prerequisites

### 3.1 System requirements

| Requirement | Version | Required? |
|---|---|---|
| Python | 3.12+ (local venv uses 3.14.7) | Yes (uv can install it) |
| uv | 0.12+ | Yes |
| PostgreSQL | 16+ (local install uses 18.6) | Yes for the normal setup |
| Redis | 7+ | Optional for now |
| Docker Desktop | Current | Optional |
| Git | Current | Recommended |
| VS Code / PyCharm | Current | Recommended |

The API can also run on SQLite for a quick smoke test, but PostgreSQL is the target database.

### 3.2 Install uv (Windows)

PowerShell:

```powershell
winget install --id astral-sh.uv -e
```

Alternative installer:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify (open a new terminal if needed):

```powershell
uv --version
```

macOS/Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv manages Python versions and the virtual environment. You do **not** need a separate global
Python install, although having one is harmless.

### 3.3 Install Python (managed by uv)

```powershell
uv python install 3.14
```

Any Python 3.12+ works. The Docker image uses Python 3.12; the local environment currently runs
3.14.7.

### 3.4 Install PostgreSQL 18 (Windows)

1. Download the installer from
   [EnterpriseDB PostgreSQL downloads](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads).
2. Run the installer and keep these components enabled:
   - PostgreSQL Server
   - pgAdmin 4
   - Command Line Tools
3. Choose a **superuser (`postgres`) password** and store it safely.
4. Keep the default port **5432** unless it is already occupied.
5. Keep the default locale/encoding settings.

Verify the service is running:

```powershell
Get-Service -Name "postgresql*"
```

Expected:

```text
Status   Name               DisplayName
------   ----               -----------
Running  postgresql-x64-18  postgresql-x64-18 - PostgreSQL Server 18
```

The binaries live at `C:\Program Files\PostgreSQL\18\bin`. Either use the full path or add that
directory to `PATH`:

```powershell
$pgBin = "C:\Program Files\PostgreSQL\18\bin"
[Environment]::SetEnvironmentVariable(
  "Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$pgBin",
  "User"
)
```

Reopen the terminal afterwards and verify:

```powershell
psql --version
```

### 3.5 Install Redis (optional)

Redis is not required to start the API or run tests. It is reserved for queues/rate limiting.

Option A — Docker:

```powershell
docker run -d --name reyvo-redis -p 6379:6379 redis:7-alpine
```

Option B — Memurai (native Windows Redis-compatible server): download the Developer edition from
<https://www.memurai.com/get-memurai> and install it as a Windows service.

Option C — WSL:

```bash
sudo apt install redis-server
sudo service redis-server start
```

Default configuration expects `redis://localhost:6379/0`.

### 3.6 Install Docker Desktop (optional)

Useful for running PostgreSQL/Redis without installing them natively, or for the full stack.

```powershell
winget install --id Docker.DockerDesktop -e
```

> If you use the Docker database while the native PostgreSQL service is running, both will try to
> bind port 5432. See [Troubleshooting](#13-troubleshooting).

### 3.7 Install Git (recommended)

```powershell
winget install --id Git.Git -e
```

There is currently no Git repository in the project folder. To start tracking history:

```powershell
cd D:\Projects\Reyvo
git init
git add .
git commit -m "Initial Reyvo backend"
```

`src/.gitignore` already excludes `.env`, virtual environments, caches, SQLite files, and build
artifacts.

### 3.8 Recommended editor extensions

VS Code:

- Python (Microsoft)
- Ruff (Astral Software)
- Even Better TOML
- Docker (optional)
- PostgreSQL (optional)

---

## 4. First-time project setup

All commands below run from `D:\Projects\Reyvo\src` unless stated otherwise.

### Step 1 — Install dependencies

```powershell
cd D:\Projects\Reyvo\src
uv sync
```

This creates `.venv`, installs runtime and dev dependencies, and installs the `reyvo` package in
editable mode.

Verify:

```powershell
uv run python --version
uv run ruff --version
```

### Step 2 — Create the local environment file

```powershell
Copy-Item .env.example .env
```

Generate development secrets:

```powershell
uv run python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))"
uv run python -c "from cryptography.fernet import Fernet; print('TOKEN_ENCRYPTION_KEY=' + Fernet.generate_key().decode())"
```

Put both values into `.env`. A working local `.env` has at least:

```dotenv
APP_NAME=Reyvo API
ENVIRONMENT=local
DEBUG=false
SECRET_KEY=<at least 32 characters>
TOKEN_ENCRYPTION_KEY=<fernet key>
DATABASE_URL=postgresql+psycopg://reyvo:reyvo@localhost:5432/reyvo
AUTO_CREATE_TABLES=false
```

Keep `DEBUG=false` unless you want SQLAlchemy to echo every SQL statement.

> `.env` is ignored by Git. Never commit real secrets. The values above are development-only.

### Step 3 — Create the PostgreSQL role and database

`scripts/setup_local_db.sql` creates/refreshes the `reyvo` login role and the `reyvo` database.

Run it as the PostgreSQL superuser. It prompts for the `postgres` password chosen during install:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f scripts\setup_local_db.sql
```

What it does:

- Creates `reyvo` with password `reyvo` (or resets the password if the role exists)
- Creates the `reyvo` database owned by `reyvo`
- Grants privileges

Verify the application credentials:

```powershell
$env:PGPASSWORD = "reyvo"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U reyvo -h localhost -d reyvo -c "select current_database(), current_user;"
Remove-Item Env:PGPASSWORD
```

Expected:

```text
 current_database | current_user
------------------+--------------
 reyvo            | reyvo
```

### Step 4 — Apply database migrations

```powershell
uv run alembic upgrade head
```

Expected:

```text
Running upgrade  -> a7ca34fde91a, initial schema
Running upgrade a7ca34fde91a -> 7d226fe14b46, add workos sso fields
```

Check the current revision:

```powershell
uv run alembic current
```

Expected:

```text
7d226fe14b46 (head)
```

### Step 5 — Create the demo account

```powershell
uv run python scripts\seed_demo.py
```

```text
Demo account created
  email:    demo@reyvo.com
  password: ReyvoDemo123!
  tenant:   Reyvo Demo Agency (reyvo-demo-agency-...)
```

The script is idempotent: if the account already exists it prints a message and exits.

### Step 6 — Start the API

**Windows (required for PostgreSQL):**

```powershell
uv run uvicorn reyvo.main:app --reload --port 8000 --loop reyvo.dev:selector_loop_factory
```

Or use the bundled launcher, which sets the correct loop and enables reload:

```powershell
uv run python -m reyvo.dev
```

**macOS/Linux:**

```bash
uv run uvicorn reyvo.main:app --reload --port 8000
```

> **Why the extra flag on Windows?** psycopg's async driver cannot run on Windows'
> `ProactorEventLoop`. `reyvo/dev.py` provides a `SelectorEventLoop` factory that uvicorn accepts
> via `--loop`. Alembic and the seed script already apply the same fix internally.

### Step 7 — Verify

Open:

| URL | Purpose |
|---|---|
| <http://localhost:8000/docs> | Scalar API reference |
| <http://localhost:8000/openapi.json> | OpenAPI schema |
| <http://localhost:8000/health> | Liveness |
| <http://localhost:8000/health/ready> | Database readiness |

Log in from PowerShell:

```powershell
$body = @{
  email = "demo@reyvo.com"
  password = "ReyvoDemo123!"
} | ConvertTo-Json

$tokens = Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/api/v1/auth/login `
  -ContentType "application/json" `
  -Body $body

$headers = @{ Authorization = "Bearer $($tokens.access_token)" }
Invoke-RestMethod -Uri http://localhost:8000/api/v1/auth/me -Headers $headers
```

In Scalar, call `POST /api/v1/auth/login`, copy `access_token`, click **Authorize**, and paste the
token. Scalar persists the token across reloads.

---

## 5. Environment variables

| Variable | Required | Default / example | Notes |
|---|---|---|---|
| `APP_NAME` | No | `Reyvo API` | Displayed by health/docs |
| `ENVIRONMENT` | No | `local` | `local`, `test`, `staging`, `production` |
| `DEBUG` | No | `false` | `true` echoes SQL statements |
| `API_V1_PREFIX` | No | `/api/v1` | Router prefix |
| `APP_PUBLIC_URL` | No | `http://localhost:3000` | Public app URL; unsubscribe links, allowed SSO return target |
| `SECRET_KEY` | **Yes** | — | JWT signing and encryption fallback; min 32 chars |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `30` | Refresh token lifetime |
| `JWT_ALGORITHM` | No | `HS256` | JWT algorithm |
| `TOKEN_ENCRYPTION_KEY` | Recommended | — | Fernet key for provider tokens |
| `DATABASE_URL` | **Yes** | `postgresql+psycopg://reyvo:reyvo@localhost:5432/reyvo` | Async psycopg URL |
| `AUTO_CREATE_TABLES` | No | `false` | Dev-only; use Alembic otherwise |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Reserved for worker/queues |
| `CORS_ORIGINS` | No | `http://localhost:3000,...` | Comma-separated allowed origins |
| `LOG_LEVEL` | No | `INFO` | Standard logging level |
| `GOOGLE_*` | No | — | Gmail OAuth client |
| `MICROSOFT_*` | No | — | Microsoft Graph OAuth client |
| `BREVO_*` | No | — | Brevo email server (`API_KEY`, `SENDER_EMAIL`, `SENDER_NAME`); planned |
| `WORKOS_API_KEY` | No | — | Enables WorkOS SSO (identity provider) |
| `WORKOS_CLIENT_ID` | No | — | Enables WorkOS SSO |
| `WORKOS_REDIRECT_URI` | No | `http://localhost:8000/api/v1/auth/sso/callback` | Must match WorkOS dashboard |
| `WORKOS_WEBHOOK_SECRET` | No | — | Reserved for future WorkOS webhooks |
| `AI_*` | No | — | AI drafting endpoint |
| `STRIPE_*` | No | — | Billing and webhook verification |
| `DEFAULT_DAILY_CAP` / `DEFAULT_HOURLY_CAP` | No | `50` / `25` | Sending safety defaults |
| `REQUIRE_EMAIL_ACCOUNT_FOR_LAUNCH` | No | `true` | Campaign launch guard |

Generate a Fernet key:

```powershell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 6. Running the application

### 6.1 Local process

| Platform | Command |
|---|---|
| Windows | `uv run uvicorn reyvo.main:app --reload --port 8000 --loop reyvo.dev:selector_loop_factory` |
| Windows shortcut | `uv run python -m reyvo.dev` |
| macOS/Linux | `uv run uvicorn reyvo.main:app --reload --port 8000` |

### 6.2 Quick run on SQLite (no PostgreSQL)

Useful for a short smoke test or demos. Set in `.env`:

```dotenv
DATABASE_URL=sqlite+aiosqlite:///./reyvo.db
AUTO_CREATE_TABLES=true
```

Then start normally. `AUTO_CREATE_TABLES` creates the schema at startup; use Alembic instead for
PostgreSQL and deployed environments.

### 6.3 Docker Compose

```powershell
cd D:\Projects\Reyvo\src
docker compose up -d db redis
uv run alembic upgrade head
uv run uvicorn reyvo.main:app --reload --port 8000 --loop reyvo.dev:selector_loop_factory
```

Full stack (API + database + Redis + worker) built from the Dockerfile:

```powershell
docker compose up --build
```

Notes:

- Compose uses PostgreSQL 16; the native install uses 18. Both are compatible.
- The API container runs `alembic upgrade head` before starting uvicorn.
- `docker compose down` keeps the database volume; `docker compose down -v` deletes it.
- `.env` must exist because Compose loads it via `env_file`.
- If native PostgreSQL is running, stop it before `docker compose up` or change the host port
  mapping (below).

### 6.4 Ports

| Service | Port |
|---|---|
| API | 8000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Frontend (future) | 3000 |

---

## 7. Testing and code quality

```powershell
cd D:\Projects\Reyvo\src

# Full test suite
uv run pytest -q

# One module
uv run pytest tests/test_sso.py -q

# Verbose
uv run pytest -v

# Lint
uv run ruff check .

# Auto-fix lint
uv run ruff check --fix .

# Format
uv run ruff format .

# Verify formatting without writing
uv run ruff format --check .
```

Current baseline:

```text
36 passed
All checks passed!
```

Test characteristics:

- In-memory SQLite via `aiosqlite` — no PostgreSQL or Redis needed.
- External providers are faked/monkeypatched; no network calls.
- WorkOS is configured with test credentials in `tests/conftest.py`.
- Covers auth, SSO provisioning/mismatch/linking, tenant isolation, contacts, suppressions, and
  campaign lifecycle.
- Tests exercise security boundaries (cross-tenant reads/writes/lists).

Before opening a pull request, run:

```powershell
uv run ruff format . ; uv run ruff check . ; uv run pytest -q
```

---

## 8. Database and migrations

### 8.1 Core tables

| Group | Tables |
|---|---|
| Identity | `users`, `memberships` |
| Tenancy | `tenants`, `subscriptions`, `plans`, `usage_counters`, `audit_logs` |
| CRM | `companies`, `contacts`, `contact_lists`, `contact_list_members`, `segments`, `imports` |
| Outreach | `email_accounts`, `campaigns`, `campaign_steps`, `campaign_enrollments`, `messages` |
| Inbox/pipeline | `replies`, `opportunities`, `tasks` |
| Compliance | `suppressions` |
| Platform | `webhook_events`, `alembic_version` |

Model definitions: `reyvo/db/models.py`. Enumerations: `reyvo/db/enums.py`.

Registration writes to four tables in one transaction:

```text
users 1 ──< memberships >── 1 tenants
                                 │
                                 └── 1 subscriptions
```

### 8.2 Common Alembic commands

```powershell
# Apply all migrations
uv run alembic upgrade head

# Current revision
uv run alembic current

# Migration history
uv run alembic history

# Create a migration from model changes (requires a reachable DB at head)
uv run alembic revision --autogenerate -m "add example table"

# Roll back one revision
uv run alembic downgrade -1

# Roll back everything
uv run alembic downgrade base
```

Migration files live in `alembic/versions/`.

### 8.3 Schema-change workflow

1. Edit the SQLAlchemy model in `reyvo/db/models.py`.
2. Start from a migrated database at head.
3. Run `uv run alembic revision --autogenerate -m "describe change"`.
4. **Review the generated migration.** Add server defaults for new non-nullable columns and check
   indexes/constraints.
5. Run `uv run alembic upgrade head`.
6. Test the downgrade path.
7. Add or update tests.
8. Run Ruff and pytest.

Alembic's `env.py` includes a Windows `SelectorEventLoop` fix, so migrations work with psycopg.

### 8.4 Row-level security (production)

`scripts/enable_rls.sql` enables PostgreSQL RLS with `FORCE` on tenant-scoped tables. The app sets:

```sql
SELECT set_config('app.tenant_id', '<tenant-uuid>', true);
```

Apply after migrations:

```powershell
$env:PGPASSWORD = "reyvo"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U reyvo -h localhost -d reyvo -f scripts\enable_rls.sql
Remove-Item Env:PGPASSWORD
```

Notes:

- RLS is defense-in-depth; the service layer already filters by tenant.
- `suppressions` and `audit_logs` allow `tenant_id IS NULL` global rows.
- `plans`, `users`, and `webhook_events` are intentionally global.
- Local development usually does not need RLS. Validate all endpoints after enabling it, because
  SQLite-based tests do not exercise PostgreSQL RLS.
- In production, connect with a non-owner role or keep `FORCE ROW LEVEL SECURITY` enabled.

---

## 9. Authentication and authorization

### 9.1 Local password flow

1. `POST /api/v1/auth/register` creates the user, tenant, owner membership, and trial subscription.
2. The response contains an access token, refresh token, expiry, and tenant summary.
3. Send `Authorization: Bearer <access_token>` on tenant-scoped requests.
4. `POST /api/v1/auth/refresh` exchanges the refresh token for a new pair.
5. `GET /api/v1/auth/me` returns the user, tenant, and role.
6. If an account belongs to multiple tenants, login requires `tenant_slug`.

Login request:

```json
{
  "email": "demo@reyvo.com",
  "password": "ReyvoDemo123!",
  "tenant_slug": null
}
```

Token pair response:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 1800,
  "tenant": {
    "id": "...",
    "name": "Reyvo Demo Agency",
    "slug": "reyvo-demo-agency-..."
  }
}
```

Security properties:

- Passwords hashed with Argon2id (`pwdlib`).
- JWT access/refresh tokens signed with `SECRET_KEY`.
- Provider tokens encrypted with Fernet.
- Access token claims bind the user, tenant, and role.
- Disabled users, suspended tenants, and inactive memberships are rejected.

### 9.2 Roles

| Role | Capabilities |
|---|---|
| `owner` | Everything, including member role changes and tenant administration |
| `admin` | Tenant settings, SSO configuration, audit logs, campaigns |
| `member` | Standard CRM and campaign access |

Role checks use `require_roles(...)` from `reyvo/api/deps.py`.

### 9.3 WorkOS enterprise SSO (optional)

Local login always remains available. SSO is opt-in per tenant and per deployment.

**Current local state:** WorkOS keys are blank, so SSO reports:

```json
{
  "available": false,
  "configured": false,
  "enabled": false
}
```

SSO endpoints return a clear `400` rather than breaking the API.

#### WorkOS setup

1. Create a WorkOS account/environment.
2. Create an **Organization** (for example, the customer's company).
3. Create an **SSO Connection** for that organization and configure the customer's identity provider.
4. Add the redirect URI:
   `http://localhost:8000/api/v1/auth/sso/callback`
   (use a public HTTPS URL for real IdPs).
5. Add to `.env`:

```dotenv
WORKOS_API_KEY=sk_...
WORKOS_CLIENT_ID=client_...
WORKOS_REDIRECT_URI=http://localhost:8000/api/v1/auth/sso/callback
```

6. Restart the API.

#### Link a tenant

As an owner or admin:

```http
PATCH /api/v1/tenants/current/sso
Content-Type: application/json

{
  "workos_organization_id": "org_...",
  "workos_sso_connection_id": "conn_..."
}
```

Read or disable:

```http
GET    /api/v1/tenants/current/sso
DELETE /api/v1/tenants/current/sso?reason=disable
```

#### Sign-in flow

1. `GET /api/v1/auth/sso/authorize?tenant_slug=<slug>&login_hint=<email>`
   - Optional: `domain_hint`, `return_to`
   - Returns `{ "authorization_url": "...", "state": "..." }`
2. Redirect the browser to `authorization_url`.
3. WorkOS redirects to `GET /api/v1/auth/sso/callback?code=...&state=...`.
   - With an allow-listed `return_to`, the API redirects there with tokens in the URL fragment.
   - Otherwise it returns a JSON token pair.
4. Server/SPA clients can also call `POST /api/v1/auth/sso/callback` with `{ "code", "state" }`.

Behavior:

- New SSO users are provisioned with `auth_provider = "workos_sso"`.
- Existing local accounts are linked by email; their password keeps working.
- First user in an empty tenant becomes `owner`; otherwise the WorkOS role slug maps to
  `owner`/`admin`, defaulting to `member`.
- The returned WorkOS profile must match the tenant's configured organization/connection.
- `state` is a short-lived signed JWT bound to the tenant.
- `return_to` must be same-origin with `CORS_ORIGINS` or `APP_PUBLIC_URL`.
- SSO sign-ins are written to `audit_logs` as `auth.sso_login`.

#### Local SSO testing

Many IdPs require HTTPS. For local end-to-end testing, expose port 8000 through a tunnel
(for example ngrok or Cloudflare Tunnel), set `WORKOS_REDIRECT_URI` to the public URL, and register
that URL in WorkOS. WorkOS test connections can often use localhost directly.

---

## 10. API overview

Interactive docs: <http://localhost:8000/docs> (Scalar).
Schema: <http://localhost:8000/openapi.json>.

| Area | Base path |
|---|---|
| Auth | `/api/v1/auth` |
| Tenants | `/api/v1/tenants/current` |
| Companies | `/api/v1/companies` |
| Contacts | `/api/v1/contacts` |
| Contact lists | `/api/v1/lists` |
| Suppressions | `/api/v1/suppressions` |
| Email accounts | `/api/v1/email-accounts` |
| Campaigns | `/api/v1/campaigns` |
| Replies | `/api/v1/replies` |
| Tasks | `/api/v1/tasks` |
| Opportunities | `/api/v1/opportunities` |
| Dashboard | `/api/v1/dashboard` |
| AI drafting | `/api/v1/ai` |
| Admin | `/api/v1/admin` |
| Billing | `/api/v1/billing` |
| Webhooks | `/api/v1/webhooks` |
| Public unsubscribe | `/api/v1/u/{token}` |

Every tenant-scoped route derives `tenant_id` from the access token. There is no client-supplied
tenant selector.

Error envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request payload is not valid.",
    "details": {},
    "request_id": "..."
  }
}
```

`X-Request-ID` is returned on every response and accepted from clients for correlation.

---

## 11. Integrations

All integrations are optional at runtime.

### 11.1 Gmail / Google Workspace

```dotenv
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/api/v1/email-accounts/oauth/google/callback
```

The adapter lives in `reyvo/integrations/email/`. Real sending is not enabled yet.

### 11.2 Microsoft 365 / Graph

```dotenv
MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_TENANT_ID=common
MICROSOFT_REDIRECT_URI=http://localhost:8000/api/v1/email-accounts/oauth/microsoft/callback
```

### 11.3 AI drafting (Semantic Kernel)

```dotenv
AI_API_KEY=
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=gpt-4.1-mini
```

Any OpenAI-compatible endpoint works. Drafting and grounded knowledge-base chat are implemented
with [Semantic Kernel](https://learn.microsoft.com/semantic-kernel/) prompt functions in
`reyvo/integrations/ai/`, behind the `DraftProvider`/`ChatProvider` protocols.

### 11.4 Stripe billing

```dotenv
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_PRICE_STARTER=
STRIPE_PRICE_GROWTH=
STRIPE_PRICE_SCALE=
```

Billing endpoints and webhook verification are scaffolded; Stripe is not required to run the API.

### 11.5 WorkOS (identity provider)

See [section 9.3](#93-workos-enterprise-sso-optional). SDK: `workos` 5.x, wrapped asynchronously via
`anyio.to_thread.run_sync`. WorkOS is the identity provider for enterprise SSO; local
email/password login always remains available.

### 11.6 Brevo (email server, planned)

```dotenv
BREVO_API_KEY=
BREVO_SENDER_EMAIL=
BREVO_SENDER_NAME=Reyvo
```

Brevo is the reference email server for **platform/transactional** mail (verification, invites,
billing) and a **campaign-send (ESP) path** alongside the per-user Gmail/Graph adapters. It is
**not implemented yet**: there are no `BREVO_*` settings in `reyvo/core/config.py` and no Brevo
adapter registered in `reyvo/integrations/email/`. Any Brevo adapter must respect
`REQUIRE_EMAIL_ACCOUNT_FOR_LAUNCH` and the daily/hourly sending caps, and must reconcile with
design decision D-03 (which chose customer-owned mailboxes for MVP sending).

---

## 12. Background worker

`reyvo/workers/sender.py` is an isolated entry point:

```powershell
uv run python -m reyvo.workers.sender
```

It is intentionally separated from the HTTP process. Planned jobs:

- `send.due` — schedule and send due enrollment steps
- `sync.replies` — Gmail Pub/Sub and Microsoft Graph change notifications
- `classify.reply` — rules + AI reply classification
- `report.rollup` — daily campaign/tenant/inbox metrics
- `retention.purge` — retention and deletion jobs

The send loop, provider retry/rate limiting, and reply ingestion are not production-ready yet.

---

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `psql` is not recognized | Use the full path `C:\Program Files\PostgreSQL\18\bin\psql.exe` or add the `bin` directory to `PATH` and reopen the terminal. |
| `password authentication failed for user "postgres"` | Use the superuser password chosen during installation. Reset it via pgAdmin, or temporarily set `trust` in `pg_hba.conf` and restart the service. |
| `password authentication failed for user "reyvo"` | Run `scripts\setup_local_db.sql` again as `postgres`; confirm `DATABASE_URL` matches the role/password. |
| `psycopg cannot use the 'ProactorEventLoop'` | On Windows, start uvicorn with `--loop reyvo.dev:selector_loop_factory`. Alembic and the seed script already handle this. |
| `port 5432 is already in use` | Native PostgreSQL and Docker are both trying to bind 5432. Stop one: `Stop-Service postgresql-x64-18` (admin), or change Compose to `"5433:5432"` and set `DATABASE_URL=...@localhost:5433/reyvo`. |
| `ModuleNotFoundError: reyvo` | Run commands from `D:\Projects\Reyvo\src` and use `uv run ...` (the project is installed editable). |
| Tests pass but local API returns 500 | Tests use SQLite. Check `.env`, PostgreSQL connectivity, migrations, and the Windows loop flag. |
| `Relation ... does not exist` | Migrations are not applied: `uv run alembic upgrade head`. |
| Alembic autogenerate produces unexpected diffs | The database is not at head, or the model imports are missing. Run `alembic upgrade head` first. |
| `email-validator` rejects a domain | Use a deliverable-looking domain (`example.com`, `acme.com`). Avoid `.test` and `.local` in seeded accounts. |
| Scalar page is blank | Scalar loads JS from jsDelivr; check internet access or vendor the bundle locally. |
| `401 unauthorized` | Missing/expired token, wrong tenant, or malformed refresh token. Log in again. |
| `403 forbidden` | Role too low, membership inactive, or tenant suspended/canceled. |
| SSH/HTTPS login prompts appear unexpectedly | Not applicable to this backend; check Git credential manager configuration. |

### Reset the local database

Destroy all local data and rebuild:

```powershell
$env:PGPASSWORD = "reyvo"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -c "DROP DATABASE IF EXISTS reyvo WITH (FORCE);"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f scripts\setup_local_db.sql
Remove-Item Env:PGPASSWORD

uv run alembic upgrade head
uv run python scripts\seed_demo.py
```

---

## 14. Development conventions

### Adding an endpoint

1. Add/extend the Pydantic schema in `reyvo/schemas/`.
2. Put business logic in `reyvo/services/` — keep routers thin.
3. Add the route in `reyvo/api/v1/<domain>.py`.
4. Use `Depends(get_principal)` and `require_roles(...)` for auth/roles.
5. Always filter tenant-scoped queries by `principal.tenant_id`.
6. Record security-relevant actions in `audit_service`.
7. Add tests, including a cross-tenant access attempt.
8. Run Ruff and pytest.

### Adding a model

1. Define it in `reyvo/db/models.py` using the existing base/mixins.
2. Add a `tenant_id` column for tenant-scoped data.
3. Generate and review an Alembic migration.
4. Add schemas/services/tests.
5. Update `scripts/enable_rls.sql` if the table is tenant-scoped.

### Error handling

Raise the typed errors in `reyvo/core/exceptions.py` (`BadRequestError`, `UnauthorizedError`,
`ForbiddenError`, `NotFoundError`, `ConflictError`, `PreflightError`,
`ServiceUnavailableError`). The app converts them into the stable error envelope.

### Dependencies

Use uv:

```powershell
uv add <package>
uv add --dev <package>
```

Avoid `pip install` so `pyproject.toml` and `uv.lock` stay in sync.

### Style

- Ruff, line length 100, double quotes, Python 3.12 target.
- `from __future__ import annotations` in Python modules.
- UUID primary keys and UTC timestamps.
- Never log secrets, tokens, or raw provider credentials.

---

## 15. Security notes

- `.env` contains development secrets and is git-ignored. Do not reuse them in staging/production.
- Use a secrets manager in deployed environments.
- Rotate `SECRET_KEY` invalidates all issued JWTs.
- Rotate `TOKEN_ENCRYPTION_KEY` requires re-encrypting stored provider tokens.
- Set `AUTO_CREATE_TABLES=false` outside local development.
- Apply migrations and RLS policies in the deployment pipeline.
- WorkOS API keys and Stripe keys must remain server-side.
- The demo password is public; never create `demo@reyvo.com` in production.

---

## 16. Current status and next steps

Working now:

- API factory, Scalar docs, error envelope, request IDs, CORS
- PostgreSQL schema, Alembic migrations, tenant scoping
- Local auth + optional WorkOS SSO
- CRM, campaigns, replies, pipeline, dashboard, admin, billing scaffolding
- Demo seed and 36 automated tests

Explicit next steps:

1. Implement the send worker with provider retry/rate limiting.
2. Add Gmail Pub/Sub and Microsoft Graph subscription verification and reply ingestion.
3. Add Redis-based rate limiting and token buckets.
4. Add AI reply classification and approval queues.
5. Automate RLS application in the deploy pipeline.
6. Add Brevo as the email server: notifications (verification, invites, billing) and a campaign-send path.
7. Add a CI pipeline: `ruff format --check`, `ruff check`, `pytest`.
8. Add structured logging/metrics and error tracking.

---

## 17. Quick reference

```powershell
# Setup
cd D:\Projects\Reyvo\src
uv sync
Copy-Item .env.example .env
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f scripts\setup_local_db.sql
uv run alembic upgrade head
uv run python scripts\seed_demo.py

# Run
uv run uvicorn reyvo.main:app --reload --port 8000 --loop reyvo.dev:selector_loop_factory

# Quality
uv run ruff format .
uv run ruff check .
uv run pytest -q

# Database
uv run alembic current
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "change"

# Demo login
# POST /api/v1/auth/login
# { "email": "demo@reyvo.com", "password": "ReyvoDemo123!" }
```

Useful URLs:

- Scalar docs: <http://localhost:8000/docs>
- OpenAPI: <http://localhost:8000/openapi.json>
- Health: <http://localhost:8000/health>
- Readiness: <http://localhost:8000/health/ready>

---

*This guide reflects the current local development setup on Windows with PostgreSQL 18.6,
Python 3.14.7, uv 0.12.4, and 36 passing tests.*
