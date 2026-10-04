# How the server gets deployed

`git push origin main` is the whole deploy. Railway builds `server/` and ships it.
**Migrations run automatically — do not run `alembic` against prod by hand.**

## The one file that controls it

[`server/railway.toml`](./railway.toml). Railway reads it because the service's root
directory is `/server` and no explicit config path is set, so the default lookup lands
there. It is the *only* source of deploy config — every equivalent field in the Railway
dashboard is `null`, deliberately, so that the repo stays authoritative.

```toml
[deploy]
preDeployCommand  = "alembic upgrade head"                                   # migrations
startCommand      = "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1"
healthcheckPath   = "/healthz"                                               # NOT /health
healthcheckTimeout = 90
restartPolicyType = "on_failure"
```

## Order of operations

1. Build the image from `server/`.
2. **Pre-deploy container**: runs `alembic upgrade head` to completion, on Railway's
   private network with `DATABASE_URL` already injected. Nothing is serving from the
   new image yet — the *previous* container is still taking all traffic.
3. Non-zero exit here **aborts the deploy**. The new image never takes traffic and the
   old container keeps serving.
4. On success, the web container starts and must pass `/healthz` within 90s.

The consequence worth internalising: there is no window in which new code is reachable
against an old schema. Step 2 finishes before step 4 begins.

## Verifying a deploy

```bash
railway link --project fletcher --environment production --service guitar-trainer-v2
railway logs --deployment --lines 200 | grep -iE 'alembic|running upgrade'
```

A deploy that applied something looks like this — note the pre-deploy container
starting and stopping *before* the web process boots:

```
Starting Container
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Running upgrade 0009 -> 0010, deploy-path probe
Stopping Container          <- pre-deploy container, done
Starting Container          <- web process
INFO:     Application startup complete.
```

A deploy with no pending migration logs the first two lines and no `Running upgrade`.
That silence is normal, and is why the mechanism is easy to miss.

To read the schema version directly (there is no `psql` on the dev machine; the
internal `DATABASE_URL` is not reachable from a laptop, only `DATABASE_PUBLIC_URL`):

```bash
railway variables --service Postgres --kv | grep '^DATABASE_PUBLIC_URL='
python3 -c "import asyncio,asyncpg; print(asyncio.run(
  asyncpg.connect('<url>').__aenter__()))"   # or a throwaway asyncpg script:
# conn.fetch('select version_num from alembic_version')
```

## Cost controls ride in on the deploy (FLE-92)

Two independent spend limits live in `app/ai/governor.py`, and because a push of `main`
is the whole deploy, **both arrive the moment anyone pushes** — neither is behind a
feature flag you have to remember to turn on. They resolve differently, and the
difference is the thing to get right:

| control | env var | absent/empty → |
|---|---|---|
| per-user breakdown cap | `FLETCHER_CAP_BREAKDOWN` | **enforces `BREAKDOWN_CAP = 5`/day** |
| global dollar ceiling | `FLETCHER_PILOT_CEILING_USD` | **enforces `$25.00`** |

`effective_cap` and `effective_pilot_ceiling` both fall back to the code default when
the var is unset, and only the literal strings `off`/`none`/`unlimited`/`-1` remove a
limit. A typo fails toward spending less, by design.

**The consequence that bites: prod is capped-off only because `FLETCHER_CAP_BREAKDOWN=off`
is *present*.** Delete that variable, or stand up an environment without it, and the
per-user cap silently self-enables at 5/day — the FLE-58 lockout, with nobody having
decided to flip anything. It is a deliberate absence, not a leftover. Don't tidy it up.

### Verified state, 2026-10-04

- Prod runs `6e8426f`, **ten commits behind `main`** — the ceiling is not deployed, so
  right now nothing bounds total pilot spend.
- `FLETCHER_CAP_BREAKDOWN=off`. `FLETCHER_PILOT_CEILING_USD` and `FLETCHER_PILOT_START`
  are both unset, so the first push of `main` turns the $25 ceiling on at its default
  and measures it against every row ever written.
- Measured spend against that ceiling: **$0.507 of $25 (2.0%)**, ~122 breakdowns of
  headroom. The FLE-95 backfill of 143 historical rows contributes ~$0.06 of it, so
  counting all-rows-ever is survivable and `FLETCHER_PILOT_START` can wait until the
  pilot actually opens.

Re-measure before trusting those numbers:

```bash
railway variables --service Postgres --json | python3 -c "import json,sys; print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])"
cd server && DATABASE_URL='<that url>' .venv/bin/python -m scripts.pilot_spend
```

Exit codes: `0` under 80%, `1` over 80%, `2` already refusing calls, `3` ceiling
switched off entirely.

### Flipping the per-user cap on

Enforcement reaching a real device is a product decision, not a deploy step. Hernan
owns the shape and the timing (FLE-92), and as of 2026-10-04 the flip is **held** until
his on-device cold-start walk (FLE-93) is signed off — turning the cap on mid-walk locks
him out of his own acceptance test. Pushing `main` is fine while the hold stands: it
deploys the ceiling with ~$24.49 of headroom and leaves the cap off, because the env var
says so.

When it is time, the flip is `FLETCHER_CAP_BREAKDOWN=5` on the Railway `fletcher`
service — no code change and no rebuild. Both limits are read per call rather than at
import, so the value a variable edit lands takes effect immediately; Railway applies the
edit by restarting the service, so expect a brief restart rather than a full build.

## Writing a migration

Test it against a local/dev Postgres first — `alembic upgrade head`, then
`downgrade -1`, then `upgrade head` again. A migration that fails in prod's pre-deploy
is *safe* (the deploy aborts, Postgres rolls the transaction back, the old container
keeps serving) but it blocks the deploy for everyone until it is fixed, and `main` is
shared.

Two things that bite:

- `alembic/env.py` rewrites the URL scheme to sync `postgresql://` for psycopg2.
  Runtime code uses `postgresql+asyncpg://`. Both schemes have to be handled; don't
  "simplify" either rewrite.
- Autogenerate compares table comments. A decorative `COMMENT ON TABLE` shows up as
  drift in whatever the next autogenerated revision is.

## There is no Procfile any more

There used to be a `server/Procfile` containing only `web: uvicorn ...`. It was dead
config — `railway.toml`'s `startCommand` outranks a Procfile, and Railway's deployment
manifest confirmed the `--workers 1` form was what actually ran. It was deleted because
it read as the authoritative description of the release path while omitting the
migration step, which is exactly how FLE-68 came to be filed: someone read the Procfile,
correctly observed it had no `alembic` in it, and concluded prod was deploying code
against un-migrated schemas. It never was.

If you need to know what a given deploy actually ran, ask Railway rather than inferring
it from files — `meta.serviceManifest` on the deployment records the resolved
`startCommand`, `preDeployCommand`, `healthcheckPath` and `configFile`.
