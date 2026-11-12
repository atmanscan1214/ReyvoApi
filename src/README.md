# Reyvo API

FastAPI backend for **Reyvo**, a multi-tenant outbound growth platform for staffing agencies.

This service is the backend/API foundation described in `Reyvo_Detailed_Design_Document_v1.0.docx` at the repository root. It is intentionally a modular monolith: synchronous request handling lives in FastAPI routers, domain rules live in `reyvo/services`, and slow/provider-facing work is designed to move to `reyvo/workers` and `reyvo/integrations`.

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
| AI | Semantic Kernel prompt functions for drafting, grounded knowledge-base chat, and embeddings (OpenAI-compatible endpoint) |
| Billing | Stripe (optional at runtime) |
| Background jobs | Arq + Redis (optional; in-process fallback for rate limits) |
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
    integrations/ai/        # Semantic Kernel draft/chat/embedding providers, prompts, factory
    integrations/knowledge/ # Heading-aware docx parsing and chunking
    integrations/billing/   # Stripe gateway
    workers/                # background sender and Arq job worker
  alembic/                  # migrations
  tests/                    # API and tenant-isolation tests
  scripts/                  # local DB setup, demo seed, RLS policies, KB ingestion
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

The test suite uses an in-memory SQLite database and covers health, registration/login, tenant isolation, contacts, suppressions, the campaign lifecycle, WorkOS SSO enrollment/configuration, and the knowledge-base ingestion, retrieval, conversation-context, boundary-refusal, and chat paths.

## AI personalization

Outbound emails for campaign steps flagged `ai_generated` are written per recipient by a
[Semantic Kernel](https://learn.microsoft.com/semantic-kernel/) prompt function instead of
rendering a shared template. Every draft is built from that contact's stored profile
(title, company, industry, location, notes, skills, and `custom_fields`) plus the campaign
settings (`tone`, `value_proposition`, `call_to_action`, `extra_instructions`).

Configure the model in `.env`:

```dotenv
AI_API_KEY=sk-...
AI_BASE_URL=https://api.openai.com/v1   # any OpenAI-compatible endpoint
AI_MODEL=gpt-4.1-mini
```

- `POST /api/v1/ai/draft` drafts a standalone email. Pass `contact_id` to use a stored
  profile, or `profile` for ad-hoc facts.
- `POST /api/v1/campaigns/{id}/preview` shows exactly what the contact would receive:
  AI steps are generated, template steps are rendered.
- `reyvo.services.outreach_service.compose_enrollment_message()` is the per-enrollment
  builder for the sender worker; it includes prior subjects and the inbox from-name.

Prompt wording and its version live in `reyvo/integrations/ai/prompts.py`; bump
`PROMPT_VERSION` when changing them. Providers can be swapped through
`reyvo.integrations.ai.factory.set_draft_provider()`.

## Knowledge-base chat

The platform also ships an assistant that answers questions about Reyvo itself, grounded in
`Reyvo_Detailed_Design_Document_v1.0.docx`. Rather than stuffing the whole document into a
prompt, the design document is ingested into the database and retrieved per question:

1. **Ingest** (`scripts/ingest_kb.py`) parses the `.docx` with heading-aware chunking
   (~1,200 characters, 200-character overlap), keeping heading breadcrumbs such as
   `5. Multi-Tenancy > 5.1 Tenant isolation model`, stable anchors, and rendered tables.
2. **Store** in `knowledge_documents` (checksum, parser version, counts, status) and
   `knowledge_chunks` (content, heading path, anchor, token count, optional embedding).
   Re-running ingestion is idempotent: an unchanged SHA-256 checksum is skipped unless
   `--force` is passed, or the stored chunks still lack embeddings after you enable
   embeddings (in which case they are backfilled automatically).
3. **Retrieve** with hybrid search: BM25 lexical ranking plus cosine similarity over stored
   embeddings, fused with Reciprocal Rank Fusion. Embeddings are optional — if
   `AI_EMBEDDING_MODEL` is blank or the provider fails, lexical search keeps working.
4. **Answer** through a Semantic Kernel prompt function that receives numbered sources and a
   short conversation history. The model must cite `[n]` markers and is instructed to refuse
   instead of guessing. If retrieval returns nothing, the API replies with a refusal without
   calling the model at all.

Citations are resolved server-side from the retrieved chunks (never trusted from the model),
so each answer links back to a real heading and anchor. Conversations and messages are
persisted per tenant and user in `chat_conversations` / `chat_messages`, including token
usage and latency.

### Ingest the knowledge base

```powershell
cd src
uv run alembic upgrade head                       # creates the knowledge tables
uv run python scripts\ingest_kb.py                # idempotent
uv run python scripts\ingest_kb.py --force        # re-chunk after a document edit
```

Use `--path`, `--slug`, `--title`, and `--version` to ingest another document, or
`--create-tables` when running against a local SQLite database without migrations. The path
defaults to `DESIGN_DOC_PATH`.

### Configuration

```dotenv
AI_API_KEY=sk-...                          # required for chat and embeddings
AI_EMBEDDING_MODEL=text-embedding-3-small  # blank disables semantic retrieval
DESIGN_DOC_PATH=Reyvo_Detailed_Design_Document_v1.0.docx
KNOWLEDGE_TOP_K=6
KNOWLEDGE_CHAT_ACCESS=platform_admin       # or "members" for tenant-wide access
AI_CHAT_RATE_LIMIT_PER_MINUTE=20           # per user; 0 disables the limit
```

The design document contains commercial assumptions, delivery estimates, and security
internals, so the assistant defaults to `KNOWLEDGE_CHAT_ACCESS=platform_admin`. Set it to
`members` only after curating a customer-facing corpus. The rate limit is shared through Redis
when `REDIS_URL` is set and falls back to an in-process window otherwise.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/ai/chat` | Ask a question; returns the answer plus citations and conversation/message IDs. Pass `conversation_id` for follow-ups and `top_k` (1–12, defaults to `KNOWLEDGE_TOP_K`) to widen or narrow retrieval. |
| `GET /api/v1/ai/conversations` | Paginated list of the caller's conversations |
| `GET /api/v1/ai/conversations/{id}` | Conversation with its messages and citations. `message_limit` (1–500, default 200) caps the most recent messages returned. |
| `DELETE /api/v1/ai/conversations/{id}` | Delete a conversation and its messages |
| `POST /api/v1/ai/knowledge/search` | Raw retrieval preview for debugging: heading, anchor, snippet, and score |
| `GET /api/v1/ai/knowledge/status` | Ingested documents, chunk/embedding counts, and embedding freshness |
| `POST /api/v1/ai/knowledge/reindex` | Platform-admin re-ingestion. `force` re-chunks; `background` enqueues it on the job queue when Redis is configured. |

Chat and search are gated by `KNOWLEDGE_CHAT_ACCESS`; reindex is platform-admin only.
Answers are plain text with `[n]` markers. Citations are filtered server-side to the sources
the answer actually references, so an answer that cites one section does not appear to cite
six. An answer with no citation at all gets one repair attempt; if it still has none, the API
returns the refusal instead of presenting it as knowledge-base content.

#### Conversation context and answer boundaries

- **Context-only follow-ups** such as "tell me more", "why?", or "how does that work?" have
  little retrieval signal of their own. They are resolved against the most recent standalone
  user question, and earlier follow-ups are skipped, so a chain like "tell me more" → "why?"
  stays on the original topic. The full message history is also passed to the model so it can
  interpret pronouns and references.
- **Topic shifts stand on their own.** "What about campaigns?" searches for campaigns, and a
  new question with no matching sources (for example "What is the capital of France?") is
  refused instead of borrowing the previous topic's sources. A follow-up sent as the first
  message of a conversation is refused for the same reason.
- **The prompt treats the knowledge base as the only source of facts.** History is explicitly
  limited to resolving references, never as evidence. The model is told to answer only the
  supported part of a question, to state when the rest is not covered, and to reply with the
  canonical refusal when nothing applies.
- **Answers are validated after generation.** If the model refuses, cites a source number that
  was never supplied, or still has no citation after one repair attempt, the API returns the
  standard refusal with no citations and `used_context: false`. Invented citation markers are
  removed, so the response never points at a source that was not retrieved.

`used_context` is `true` only when the response is grounded in retrieved sources; refusals are
always `false` with an empty `citations` list.

Knowledge documents are platform-wide (shared by every tenant) because they describe the
product; conversations and messages stay tenant- and user-scoped. Prompt wording and its
version (`CHAT_PROMPT_VERSION`) live in `reyvo/integrations/ai/prompts.py`. Chat and
embedding providers are swappable through `set_chat_provider()` and
`set_embedding_provider()` in `reyvo.integrations.ai.factory`.

Embeddings are stored as portable `JSON` columns and ranked in Python, which keeps SQLite
tests and PostgreSQL identical. For a large corpus, move to `pgvector` with an HNSW index and
push cosine ranking into SQL; the service boundary stays the same.

Ingestion indexes paragraphs, headings and table text. Images and figures are not extracted,
so questions about diagrams are answered from the surrounding captions and prose. Changing the
embedding model marks stored vectors stale; `/ai/knowledge/status` reports `embeddings_stale`
and the next reindex regenerates them.

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
| AI | `POST /api/v1/ai/draft`; knowledge-base chat and conversations under `/api/v1/ai/chat` and `/api/v1/ai/conversations` |
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

The HTTP API deliberately does **not** send bulk email inline. Redis-backed background jobs
run in a separate Arq worker process, created in `reyvo/workers/worker.py` and sharing the same
database and `REDIS_URL` as the API.

```bash
# Long-running worker (also the docker-compose `worker` service)
python -m reyvo.workers.worker
# or
arq reyvo.workers.worker.WorkerSettings --check   # connectivity check
```

Scheduled jobs:

- `send_due_enrollments` — sender scaffold pass every 30 seconds. It still refuses to send
  until the provider call lands, but the scheduling and process boundary are in place.
- `cleanup_chat_history` — daily at 03:00, deletes conversations inactive for
  `CHAT_RETENTION_DAYS` (default 90; `0` disables). Messages are deleted explicitly so the
  job behaves identically on SQLite and PostgreSQL.
- `reindex_design_document` — every 6 hours; unchanged checksums are skipped unless forced.

`POST /api/v1/ai/knowledge/reindex` with `{"background": true}` enqueues the reindex instead of
running it inline. If Redis is unavailable the API logs a warning and runs it synchronously, so
the endpoint keeps working during a queue outage.

Redis also backs the per-user AI rate limiter. The limiter uses an atomic sliding-window Lua
script (sorted set per user), so limits are shared across API workers. If Redis is not
configured or is temporarily unreachable, it falls back to an in-process window; cross-worker
limits are looser during the outage and the shared limit resumes automatically. Readiness
(`GET /health/ready`) reports `redis: ok|disabled|unavailable`.

Planned jobs still to land:

- `sync.replies` — Gmail Pub/Sub and Microsoft Graph change notifications.
- `classify.reply` — rules + AI reply classification.
- `report.rollup` — daily campaign/tenant/inbox metrics.

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
| `AI_*` | AI drafting/chat endpoint (`AI_EMBEDDING_MODEL` enables semantic retrieval) |
| `DESIGN_DOC_PATH` | Word document ingested into the knowledge base |
| `KNOWLEDGE_TOP_K` | Default number of chunks retrieved per question |
| `KNOWLEDGE_CHAT_ACCESS` | `platform_admin` (default) or `members` |
| `AI_CHAT_RATE_LIMIT_PER_MINUTE` | Per-user chat/search limit; `0` disables it |
| `REDIS_URL` | Shared rate limiting and Arq job queue; blank disables both |
| `REDIS_TIMEOUT_SECONDS` | Redis connect/command timeout |
| `BACKGROUND_JOBS_ENABLED` | Disable API job enqueue while keeping Redis rate limits |
| `CHAT_RETENTION_DAYS` | Chat cleanup window; `0` keeps conversations forever |
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
- Redis powers shared AI rate limiting and the Arq background worker; see
  [Background work](#background-work) for the schedule and fallback behaviour.
- Run the worker (`python -m reyvo.workers.worker`) alongside the API. Both processes need the
  same `REDIS_URL`; the API still serves requests if Redis is briefly unavailable.
- Treat assistant answers as plain text: render them with escaping or a sanitizing Markdown
  renderer, never as raw HTML. `[n]` markers are resolved to citations by the UI.
- Chat messages can contain customer-entered data. Include conversations and messages in the
  retention/deletion jobs and audit policy before enabling the assistant for members.
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
- Knowledge-base ingestion (heading-aware docx chunking), hybrid retrieval, and grounded chat with citations and conversation persistence
- Replies read/update, tasks, opportunities
- Dashboard counts
- Platform admin tenant management
- Stripe plan/subscription read and webhook skeleton
- Alembic migrations and PostgreSQL RLS policy script
- Tests and Docker Compose (164 passing)

Explicit next steps:

- Implement the send worker and provider retry/rate-limit logic.
- Add Gmail Pub/Sub and Graph subscription webhook verification and reply ingestion.
- Add AI classification and approval queues.
- Automate RLS policy application in the migration/deploy pipeline.
- Add email notifications (verification, invites, billing).
