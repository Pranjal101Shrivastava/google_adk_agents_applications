# Deployment

Three services: **AlloyDB** holds the data, **MCP Toolbox** fronts it, and the
**agent backend** talks to Toolbox. The agent never holds a database
credential — that separation is the main reason Toolbox is in the picture.

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   Backend    │─────▶│ MCP Toolbox  │─────▶│   AlloyDB    │
│  Cloud Run   │ MCP  │  Cloud Run   │ SQL  │  PostgreSQL  │
│ FastAPI+ADK  │      │ 19 declared  │      │  + pgvector  │
└──────────────┘      │ SQL tools    │      └──────────────┘
       │              └──────────────┘
       │ verifies the Google ID token,
       │ writes the verified email into session state
       ▼
   ┌────────┐
   │ Client │  sends `Authorization: Bearer <google-id-token>`
   └────────┘
```

## 1. Local development

```bash
cd 05-ecommerce-agent
cp .env.example .env
make dev

make db-up      # Postgres + pgvector + Toolbox, via docker compose
make db-init    # schema + 10 products, 24 variants, 5 stores, 4 orders

make serve      # FastAPI on :8080 — the full flow with verified identity
# or
make web        # ADK dev UI — guest mode, so the personal tools correctly refuse
```

`make web` has no way to supply an ID token, so it runs as a guest. That is
not a limitation to work around: it is the agent demonstrating that personal
data needs a verified sign-in.

Local Postgres has pgvector but not `google_ml_integration`, so there is no
in-database `embedding()` function and semantic search is unavailable. Use the
keyword tools locally; the agent behaves the same, just less cleverly.

## 2. AlloyDB

```bash
gcloud alloydb clusters create storefront-cluster \
  --region=us-central1 --password=<password>

gcloud alloydb instances create storefront-primary \
  --cluster=storefront-cluster --region=us-central1 \
  --instance-type=PRIMARY --cpu-count=2

# Enable the Vertex AI integration so embedding() exists in-database.
gcloud alloydb clusters update storefront-cluster --region=us-central1 \
  --database-flags=password.enforce_complexity=on
```

Then apply the schema and seed, and populate the embeddings — the commented
`UPDATE` at the bottom of `data/seed.sql`. Until you run it,
`search-products-by-description` returns nothing, because it filters on
`embedding IS NOT NULL`. `search-products-by-keyword` works either way, which
is why both exist.

The AlloyDB service account needs `roles/aiplatform.user` for the in-database
`embedding()` calls.

## 3. Toolbox on Cloud Run

```bash
gcloud secrets create storefront-tools --data-file=deployment/toolbox/tools.yaml

gcloud run deploy toolbox \
  --image=us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest \
  --region=us-central1 \
  --service-account=toolbox-identity \
  --set-secrets=/app/tools.yaml=storefront-tools:latest \
  --set-secrets=DB_PASS=storefront-db-password:latest \
  --set-env-vars=PROJECT_ID=$PROJECT_ID,DB_USER=postgres,DB_CLUSTER=storefront-cluster,DB_INSTANCE=storefront-primary,GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID \
  --args='--tools-file=/app/tools.yaml','--address=0.0.0.0','--port=8080' \
  --no-allow-unauthenticated \
  --vpc-connector=alloydb-connector
```

`toolbox-identity` needs `roles/alloydb.client`,
`roles/secretmanager.secretAccessor` and `roles/aiplatform.user`. AlloyDB is
VPC-only, so the connector is required.

## 4. The backend on Cloud Run

```bash
gcloud builds submit --config deployment/cloudbuild.yaml \
  --substitutions=_REGION=us-central1,_TOOLBOX_URL=https://toolbox-xxxx.run.app,_GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID
```

Grant the backend's service account `roles/run.invoker` on the Toolbox service
and `roles/aiplatform.user` for the models.

### Set `SESSION_DB_URL` before you scale past one instance

In-memory sessions live in one process. Two Cloud Run instances, or two uvicorn
workers, means two sets of conversations and a customer whose basket
discussion vanishes on the next message. `SESSION_DB_URL` points
`DatabaseSessionService` at Postgres and fixes it:

```bash
SESSION_DB_URL=postgresql+psycopg2://user:pass@host:5432/sessions
```

The backend logs a warning at startup when it is unset, because this is the
kind of thing that works perfectly in testing and fails under load.

## Security model

**Identity is never a tool argument.** The nine personal-data tools declare:

```yaml
- name: user_email
  authServices:
    - name: google_signin
      field: email
```

Toolbox injects the `email` claim from the caller's *verified* ID token into
that parameter. The model cannot set it, see it, or override it. A customer
who types *"show me bob@example.com's orders"* gets their own orders, because
the parameter was never theirs to choose. `test_every_personal_tool_binds_identity_to_a_verified_claim`
and `test_personal_tool_sql_filters_on_the_bound_email` fail if that ever
slips — the second one matters, because a bound parameter the SQL ignores is
decoration rather than a control.

**The audience is checked.** `verify_google_id_token` passes
`GOOGLE_CLIENT_ID` to the verifier. Without it, a validly signed Google token
minted for any other application would authenticate a user here. That is the
difference between verifying a token and merely decoding one.

**No arbitrary SQL.** The agent can only invoke the 19 declared statements,
with typed parameters. There is no tool that takes SQL as an argument, so no
prompt injection reaches one.

**Write restriction is served, not prompted.** Set
`TOOLBOX_TOOLSET=storefront_browse_only` and the nine personal tools are not
served at all. A prompt that says "do not write" is a suggestion; a toolset
that omits the write tools is a control.

**No credentials in the agent.** `.env.example` contains no database password,
because the agent has no database connection. Toolbox holds the credentials, in
Secret Manager.

## Cost notes

- AlloyDB has no scale-to-zero. For a demo, Cloud SQL (`cloud-sql-postgres` in
  `tools.yaml`) or local Postgres is much cheaper; the schema is identical.
- `gemini-2.5-flash` is the right model here. Retail conversation is
  classification and lookup, not reasoning, and the money arithmetic — the part
  that must be exact — is not done by the model at all.
