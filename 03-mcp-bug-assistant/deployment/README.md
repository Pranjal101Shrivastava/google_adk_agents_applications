# Deployment

Three moving parts: the **database** (Cloud SQL), the **MCP Toolbox server**
that fronts it, and the **agent**. The agent never holds database credentials —
that is the point of putting Toolbox in between.

```
┌──────────┐     ┌──────────────┐     ┌──────────────┐
│  Agent   │────▶│ MCP Toolbox  │────▶│  Cloud SQL   │
│ Cloud Run│ MCP │  Cloud Run   │ SQL │  PostgreSQL  │
└──────────┘     └──────────────┘     └──────────────┘
      │          declared SQL only      pgvector
      ├──▶ GitHub MCP server (read-only tools)
      ├──▶ Stack Exchange API
      └──▶ Google Search (built-in)
```

## 1. Local development

Nothing below is required to *start* the agent — it will tell you what is
missing. But the ticket tools need a database:

```bash
cd 03-mcp-bug-assistant
cp .env.example .env

make dev
make inventory          # shows which tool groups are live

make db-up              # Postgres + pgvector + Toolbox via docker compose
make db-init            # schema + 10 seed tickets
make web                # http://localhost:8000
```

`make db-up` brings up the Toolbox container too, so `MCP_TOOLBOX_URL` in the
default `.env` already points at it.

Without Docker, run the binary directly:

```bash
make toolbox            # downloads the toolbox binary
make toolbox-run        # serves deployment/mcp-toolbox/tools.yaml on :5000
```

## 2. Cloud SQL

```bash
gcloud sql instances create software-assistant \
  --database-version=POSTGRES_16 --tier=db-g1-small --region=us-central1 \
  --database-flags=cloudsql.enable_google_ml_integration=on

gcloud sql databases create tickets-db --instance=software-assistant
gcloud sql users create app --instance=software-assistant --password=<password>

# Grant the instance's service account permission to call embedding models.
INSTANCE_SA=$(gcloud sql instances describe software-assistant \
  --format='value(serviceAccountEmailAddress)')
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$INSTANCE_SA" --role=roles/aiplatform.user

gcloud sql connect software-assistant --user=app --database=tickets-db \
  < data/schema.sql
gcloud sql connect software-assistant --user=app --database=tickets-db \
  < data/seed.sql
```

Then populate the embeddings — the commented `UPDATE` at the bottom of
`data/seed.sql`. Until you do, `search-tickets` returns nothing, because it
filters on `embedding IS NOT NULL`. `search-tickets-keyword` works either way,
which is why both exist.

## 3. Toolbox on Cloud Run

```bash
gcloud secrets create tools --data-file=deployment/mcp-toolbox/tools.yaml

gcloud run deploy toolbox \
  --image=us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest \
  --region=us-central1 \
  --service-account=toolbox-identity \
  --set-secrets=/app/tools.yaml=tools:latest \
  --set-env-vars=PROJECT_ID=$PROJECT_ID,DB_USER=app \
  --set-secrets=DB_PASS=db-password:latest \
  --args='--tools-file=/app/tools.yaml','--address=0.0.0.0','--port=8080' \
  --no-allow-unauthenticated
```

The `toolbox-identity` service account needs `roles/cloudsql.client`,
`roles/secretmanager.secretAccessor` and `roles/aiplatform.user` (the last one
for the in-database `embedding()` calls).

## 4. The agent on Cloud Run

```bash
echo -n "$GITHUB_PAT" | gcloud secrets create github-pat --data-file=-

gcloud builds submit --config deployment/cloudbuild.yaml \
  --substitutions=_REGION=us-central1,_TOOLBOX_URL=https://toolbox-xxxx.run.app
```

Grant the agent's service account `roles/run.invoker` on the Toolbox service
and `roles/aiplatform.user` for the models.

## Security notes

**The agent cannot issue arbitrary SQL.** It can only invoke the statements
declared in `tools.yaml`, with typed parameters. No prompt injection can make
it run a `DROP TABLE`, because there is no tool that takes SQL as an argument.

**Write restriction belongs in the toolset, not the prompt.** Set
`MCP_TOOLBOX_TOOLSET=tickets_readonly_toolset` and the write tools are not
served at all. A prompt that says "do not write" is a suggestion; a toolset
that omits the write tools is a control.

**GitHub tools are an allowlist.** `GITHUB_TOOL_FILTER` names the read-only
tools; the MCP server exposes write tools too, and nothing good comes of an
assistant that can close issues on its own. `test_github_filter_is_an_allowlist_without_write_tools`
fails if a write tool ever appears in the default list.

**Secrets go through Secret Manager.** `--set-secrets`, not `--set-env-vars`.
Environment variables show up in `gcloud run services describe` output and in
anyone's terminal scrollback.

**The database enforces its own enums.** `CHECK` constraints on `priority` and
`status` mean a model that drifts to `"High"` instead of `"P1 - High"` gets an
error rather than writing a value no query will ever match.
