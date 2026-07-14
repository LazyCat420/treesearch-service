# Credential rotation — shared Postgres `trader` user

## What happened

`src/db.py:6` hardcoded a live credential as a default fallback:

```python
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://trader:trading_bot_pass@10.0.0.16:5433/trading_bot")
```

This repo is **public**. So is `trading-service`, `lazy-tool-service`, and `scraper-service`.
The password `trading_bot_pass` is published in all four, and it is the credential for the
**shared** `trading_bot` database — the same database the trading system uses.

The literal has been removed from this repo (`src/db.py` now fails fast if `DATABASE_URL`
is unset). **That does not fix the exposure.** The password is in the git history of four
public repos and must be treated as compromised. Only rotation neutralizes it.

Do **not** attempt a git-history rewrite. A force-push on a public repo buys nothing — the
old objects are already cloned, cached, and indexed. Rotate instead.

## Blast radius

`grep -rl trading_bot_pass` across `sun/`, excluding `.venv`/`node_modules`/`.git`:

| Location | Files | Repo visibility |
|---|---|---|
| `trading-service/scripts/**` | 15 | **public** |
| `lazy-tool-service/python/scripts/**` | 15 | **public** |
| `scraper-service/scratch/` | 1 | **public** |
| `postgres-service/docker-compose.yml:11` | 1 | private — **source of truth** |
| `sun/` root + `sun/scratch/` helper scripts | ~14 | untracked / local |
| `treesearch-service` | 0 | **public** — already fixed |

Nearly all of these are debug/scratch scripts that hardcode the URL as a fallback default,
in the same shape this service used to.

## Rotation procedure

Do this in one window, when no other session is mid-deploy. Order matters.

**1. Pick the new password and set it where the DB actually reads it.**
`postgres-service/docker-compose.yml:11` is the source of truth:

```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-trading_bot_pass}   # <- the weak default
```

Set a real `POSTGRES_PASSWORD` in that stack's env and drop the fallback default.

**2. Change it on the running server** (changing compose alone does nothing to an
already-initialised data volume — `POSTGRES_PASSWORD` is only read on first init):

```sql
ALTER USER trader WITH PASSWORD '<NEW_PASSWORD>';
```

**3. Update the central deploy secret.** `deploy-kit/.env.deploy` (gitignored, synced to the
NAS as `.env`) now carries the line added during this cleanup:

```
DATABASE_URL=postgresql+asyncpg://trader:<NEW_PASSWORD>@10.0.0.16:5433/trading_bot
```

**4. Update every other consumer.** Get the live list with:

```bash
grep -rl "trading_bot_pass" . --exclude-dir=.venv --exclude-dir=node_modules \
    --exclude-dir=.git --exclude-dir=__pycache__
```

For each: replace the hardcoded literal with `os.environ["DATABASE_URL"]` (no fallback
default — that is the pattern that caused this). Anything still holding a literal is the
next leak.

**5. Redeploy** `treesearch-service` and confirm it boots. It will now refuse to start with
a clear `RuntimeError` if `DATABASE_URL` is missing, rather than silently connecting with a
published password.

## Why the fail-fast

A `os.getenv(KEY, "<real credential>")` default is what turned a config miss into a public
credential leak: the service worked perfectly with no env configured, so nobody noticed the
fallback was load-bearing. Failing loudly on a missing `DATABASE_URL` makes that class of
mistake impossible to repeat.
