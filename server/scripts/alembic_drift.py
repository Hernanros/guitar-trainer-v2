"""alembic_drift.py — does prod's alembic_version tell the truth? (FLE-134)

This exists because of FLE-133: prod's `alembic_version` was advanced to `0015` by
hand, on a database that had none of 0015's effects. Nothing deployed for a day, so
nothing noticed. The stamp/schema divergence is invisible until the next deploy tries
to compute an upgrade path and the pre-deploy container exits non-zero.

The rule in server/DEPLOY.md ("do not run `alembic` against prod by hand") already
existed and was correct. This script is the signal that fires when it is broken
anyway, WITHOUT waiting for a deploy to discover it.

Entirely read-only against prod. It opens one connection, reads `alembic_version`
and `information_schema`, and writes nothing.


WHY THE OBVIOUS CHECK IS NOT ENOUGH
-----------------------------------
FLE-134 proposed comparing prod's stamp against the chain in `origin/main`:

    sd._upgrade_revs("head", prod_alembic_version)   # raises if not in the chain

That check does not catch FLE-133, and the repo history shows why. Prod was stamped
`0015`. Migration 0015 landed on `main` at 2026-10-04 21:21 (commit 7fc1bec). From
that moment on, `0015` is a perfectly ordinary revision in main's chain with a
computable path to head, so the check reports "behind head — normal pending deploy".
Green, while prod was in fact undeployable. The comparison is against the wrong
reference: the damage was never between the stamp and `main`, it was between the
stamp and (a) the code prod is actually running, and (b) prod's actual schema.

So this script runs three checks. CHECK 1 is the proposal above — kept because it
genuinely catches a stamp written from a branch that never merged. CHECK 2 and
CHECK 3 are the ones that catch FLE-133.


CHECK 1 — is the stamp a revision this repo knows, with a path to head?
    Catches: a stamp from an abandoned or unmerged branch; a chain broken by a
    deleted or renumbered revision file.
    Does NOT catch: FLE-133 (see above).

CHECK 2 — does the stamp agree with the commit prod is actually running?
    The invariant, which falls straight out of DEPLOY.md's deploy path: the
    pre-deploy container runs `alembic upgrade head` using the migration scripts
    baked into the image being deployed. It therefore cannot leave the database
    stamped at anything other than the chain head of the commit it was built from.

        a SUCCESS deployment of commit C  =>  alembic_version == head(C's chain)

    Any deviation means the stamp was written by something other than the deploy
    path. This is the check that would have fired the moment FLE-133's hand-stamp
    landed on 2026-10-04: prod runs 6e8426f, whose chain stops at 0012, and the
    stamp said 0015 — a revision that commit has never heard of.

    Needs the Railway CLI, authenticated and linked (see DEPLOY.md). Skipped with a
    loud UNVERIFIED line if unavailable, never silently passed.

CHECK 3 — the schema-drift check: are the stamped revision's effects actually there?
    This is the one that answers FLE-133's standing caveat, that `alembic_version`
    is not trustworthy evidence that a revision's effects are present.

    It does not guess and it does not consult the ORM models. It builds the schema
    that the stamp CLAIMS prod has — a throwaway database, `alembic upgrade <stamp>`
    from empty — and diffs that reference against prod's live schema. Anything the
    reference has and prod lacks is a migration that was stamped but did not take.

    Building the reference AT THE STAMPED REVISION rather than at head is the whole
    trick. It is what separates "legitimately behind" from "lying". Prod today is
    missing both `song_catalog.rights_status` (0015, stamped — real drift) and
    `song_catalog.verified_tab` (0016, not stamped — simply not deployed yet). A
    comparison against head's models flags both and cannot tell you which is which.
    A comparison against the stamp flags only the first.

    Needs a non-prod Postgres to build the reference in. Skipped with an UNVERIFIED
    line if absent. It refuses outright to build the reference on prod's own server.

    Blind spot, stated plainly: this compares SCHEMA. A data-only migration (0012's
    seed, 0017's backfill, 0019's inserts) has no schema footprint, so a stamp that
    skipped one is invisible here. Those need a row-shape assertion, which is what
    FLE-133 did by hand.


USAGE (from the server/ directory)
    # prod, resolving the public proxy URL via the Railway CLI itself
    .venv/bin/python -m scripts.alembic_drift --from-railway \
      --ref-url postgresql://gt:devpass@localhost:5433/postgres

    # prod, with the URL supplied (DEPLOY.md shows how to read it)
    DATABASE_URL='postgresql://...@<proxy-host>:<port>/railway' \
      .venv/bin/python -m scripts.alembic_drift

    # chain check only — no Postgres to build a reference in, no Railway CLI
    DATABASE_URL=... .venv/bin/python -m scripts.alembic_drift \
      --skip-deploy-check --skip-schema-diff

`--ref-url` points at ANY non-prod Postgres, naming a database to connect to in
order to issue CREATE DATABASE; the reference itself goes in a uniquely-named
throwaway database that is always dropped again.


EXIT CODES — chosen so a routine or cron can treat this as a check without parsing.
    0  Fully verified clean. Stamp == chain head == head of the deployed commit, and
       prod's schema matches the stamp. Every check ran.
    1  Nothing broken, but NOT a full clean bill. Either prod is legitimately behind
       head (an ordinary pending deploy), or a check could not run and was reported
       UNVERIFIED. Deliberately not 0, so that 0 keeps meaning "actually verified".
    2  DRIFT. Prod is undeployable now, or the stamp is not telling the truth, or the
       stamp was written outside the deploy path. This is the alarm.
    3  The check could not run at all (no DATABASE_URL, no connection, no
       alembic_version table). Reported as failure rather than "fine", for the same
       reason scripts/pilot_spend.py reports a switched-off ceiling as failure: a
       check that silently stops checking is the condition this script exists to end.

ALERT ON EXIT >= 2. Exit 1 is for reading, not for paging.


ONE IMPORT TRAP, BECAUSE IT WILL BITE WHOEVER EDITS THIS
--------------------------------------------------------
`server/alembic/` contains an `__init__.py`, which makes it a regular package that
hard-shadows the installed `alembic` distribution whenever `server/` is on sys.path.
Running this as `-m scripts.alembic_drift` from `server/` does exactly that, and
`import alembic.script` then fails with ModuleNotFoundError. alembic.ini's
`prepend_sys_path = .` arranges the same collision for the alembic CLI, which
survives it only because the real package is imported before the path is prepended.
So: this module takes `server/` off sys.path, imports the real alembic, and puts it
back. Submodules imported later resolve through the already-loaded package's
__path__, not sys.path, so one early import is enough. Do not "tidy" this.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

# --- the import trap described above. Must run before importing alembic. --------
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _import_real_alembic():
    """Import the installed alembic, not server/alembic/."""
    shadowed = [
        p for p in list(sys.path)
        if os.path.abspath(p or os.getcwd()) == _SERVER_DIR
    ]
    for p in shadowed:
        sys.path.remove(p)
    try:
        import alembic.config  # noqa: F401
        import alembic.script

        return alembic.script
    finally:
        for p in shadowed:
            sys.path.insert(0, p)


_alembic_script = _import_real_alembic()

import psycopg2  # noqa: E402

ALEMBIC_DIR = os.path.join(_SERVER_DIR, "alembic")


def _repo_root() -> str:
    """The git toplevel, so `<rev>:<path>` lookups are not prefix-sensitive.

    `git ls-tree <rev>:<path>` interprets a relative path against the CURRENT
    directory's prefix, not the repository root. Run from server/, the path
    "server/alembic/versions" therefore resolves to nothing — and does so with exit
    status 0 and empty output, not an error, so CHECK 2 reported "no parseable
    revisions" instead of a path bug. Every git call below runs at the toplevel.
    """
    try:
        return subprocess.run(
            ["git", "-C", _SERVER_DIR, "rev-parse", "--show-toplevel"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return _SERVER_DIR


_REPO_ROOT = _repo_root()

# Derived from the real paths rather than hardcoded, so moving server/ cannot
# reintroduce the silent-empty-listing failure described in _repo_root().
VERSIONS_SUBPATH = os.path.relpath(
    os.path.join(ALEMBIC_DIR, "versions"), _REPO_ROOT
)

# Exit codes. Names rather than bare ints at the return sites.
EXIT_CLEAN = 0
EXIT_ATTENTION = 1
EXIT_DRIFT = 2
EXIT_CANNOT_RUN = 3


def _sync_url(raw: str) -> str:
    """Normalise a DATABASE_URL to the sync psycopg2 driver.

    Same three schemes alembic/env.py and app/db/session.py have to handle: Railway
    injects `postgres://`, runtime code uses `postgresql+asyncpg://`.
    """
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgres://", "postgresql://", 1)
    )


def _hostport(url: str) -> tuple[str | None, int | None]:
    parts = urlsplit(_sync_url(url))
    return parts.hostname, parts.port


# ---------------------------------------------------------------------------
# prod state
# ---------------------------------------------------------------------------
def read_prod_stamp(url: str) -> tuple[str | None, list[str]]:
    """Return (stamp, problems). stamp is None when it cannot be established."""
    problems: list[str] = []
    conn = psycopg2.connect(_sync_url(url))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            " WHERE table_schema = 'public' AND table_name = 'alembic_version')"
        )
        if not cur.fetchone()[0]:
            problems.append("no alembic_version table — alembic has never run here")
            return None, problems
        cur.execute("SELECT version_num FROM alembic_version")
        rows = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    if not rows:
        problems.append("alembic_version table is present but EMPTY — nothing stamped")
        return None, problems
    if len(rows) > 1:
        # Multiple rows mean multiple heads. upgrade head is ambiguous from here.
        problems.append(
            f"alembic_version holds {len(rows)} rows ({', '.join(sorted(rows))}) — "
            "multiple heads, so there is no single current revision"
        )
        return None, problems
    return rows[0], problems


def snapshot_schema(url: str) -> dict:
    """Introspect one database into comparable sets.

    Constraints of contype 'n' are excluded on purpose. PostgreSQL 18 materialises
    NOT NULL as a pg_constraint row; PostgreSQL 16 does not. Prod is 18 and the dev
    image is 16, so including them produces ~125 phantom "extra in prod" findings.
    Nullability is already compared per column via is_nullable, so nothing is lost.
    """
    conn = psycopg2.connect(_sync_url(url))
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = {r[0] for r in cur.fetchall()}
        cur.execute(
            "SELECT table_name, column_name, data_type, is_nullable, "
            "       COALESCE(column_default, '') "
            "  FROM information_schema.columns WHERE table_schema = 'public'"
        )
        columns = {(r[0], r[1]): (r[2], r[3], r[4]) for r in cur.fetchall()}
        cur.execute(
            "SELECT c.relname, con.conname, con.contype "
            "  FROM pg_constraint con "
            "  JOIN pg_class c ON c.oid = con.conrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            " WHERE n.nspname = 'public' AND con.contype <> 'n'"
        )
        constraints = {(r[0], r[1], r[2]) for r in cur.fetchall()}
        cur.execute(
            "SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public'"
        )
        indexes = {(r[0], r[1]) for r in cur.fetchall()}
    finally:
        conn.close()
    return {
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
    }


# ---------------------------------------------------------------------------
# CHECK 1 — the stamp against this checkout's chain
# ---------------------------------------------------------------------------
def script_directory():
    from alembic.config import Config  # real package, already loaded above

    config = Config()
    config.set_main_option("script_location", ALEMBIC_DIR)
    return _alembic_script.ScriptDirectory.from_config(config)


def _step_id(step) -> str:
    """The revision id out of whatever _upgrade_revs handed back.

    It yields RevisionStep objects whose `.revision` is a Script, not a string, and
    that nesting is not part of any public contract — so unwrap defensively.
    """
    rev = getattr(step, "revision", step)
    return str(getattr(rev, "revision", rev))


def check_chain(stamp: str) -> tuple[str, str, list[str]]:
    """Classify the stamp against the chain in this checkout.

    Returns (verdict, head, lines). verdict is clean | behind | unresolvable.
    """
    sd = script_directory()
    heads = sd.get_heads()
    lines: list[str] = []
    if len(heads) != 1:
        return (
            "unresolvable",
            ", ".join(sorted(heads)) or "(none)",
            [f"this checkout has {len(heads)} heads ({', '.join(sorted(heads))}) — "
             "`upgrade head` is ambiguous regardless of what prod is stamped at"],
        )
    head = heads[0]

    if stamp == head:
        return "clean", head, [f"stamp {stamp} == chain head {head}"]

    try:
        # Private, and the only API that answers exactly "can a path be computed".
        # Guarded by the ResolutionError/KeyError catch below rather than trusted.
        pending = sd._upgrade_revs(head, stamp)
    except Exception as exc:  # alembic raises several distinct types here
        return (
            "unresolvable",
            head,
            [
                f"stamp {stamp} is NOT in this checkout's chain "
                f"({type(exc).__name__}: {exc})",
                "`alembic upgrade head` cannot compute a path from here — a deploy "
                "built from this commit would abort in its pre-deploy container.",
            ],
        )
    lines.append(
        f"stamp {stamp} is in the chain, {len(pending)} revision(s) behind head "
        f"{head}: {', '.join(_step_id(r) for r in reversed(pending))}"
    )
    return "behind", head, lines


# ---------------------------------------------------------------------------
# CHECK 2 — the stamp against the commit prod is actually running
# ---------------------------------------------------------------------------
_REV_RE = re.compile(r"^revision\s*[:=]\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
_DOWN_RE = re.compile(
    r"^down_revision\s*[^=]*=\s*(?:['\"]([^'\"]+)['\"]|None)", re.MULTILINE
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", _REPO_ROOT, *args],
        check=True, capture_output=True, text=True,
    ).stdout


def chain_head_at_commit(commit: str) -> tuple[str | None, set[str], str | None]:
    """Compute the migration chain head as of `commit`.

    Returns (head, revisions_present, error). Reads the version files out of that
    commit's tree rather than the working copy, which is the entire point: it answers
    "what did the image prod is running actually know about".
    """
    try:
        listing = _git("ls-tree", "--name-only", f"{commit}:{VERSIONS_SUBPATH}")
    except subprocess.CalledProcessError:
        try:
            _git("cat-file", "-e", f"{commit}^{{commit}}")
        except subprocess.CalledProcessError:
            return None, set(), (
                f"commit {commit[:9]} is not in this checkout — `git fetch` and retry"
            )
        return None, set(), f"commit {commit[:9]} has no {VERSIONS_SUBPATH}"

    if not listing.strip():
        # Exit 0 with no output. git resolved the tree-ish and found nothing there,
        # which in practice means the path is wrong rather than the directory empty.
        return None, set(), (
            f"`git ls-tree {commit[:9]}:{VERSIONS_SUBPATH}` listed nothing — the path "
            f"is not resolving (looked from {_REPO_ROOT})"
        )

    revisions: set[str] = set()
    downs: set[str] = set()
    for name in listing.split():
        if not name.endswith(".py") or name.startswith("__"):
            continue
        blob = _git("show", f"{commit}:{VERSIONS_SUBPATH}/{name}")
        m = _REV_RE.search(blob)
        if not m:
            continue
        revisions.add(m.group(1))
        d = _DOWN_RE.search(blob)
        if d and d.group(1):
            downs.add(d.group(1))

    if not revisions:
        return None, revisions, f"no parseable revisions in {commit[:9]}"
    tips = revisions - downs
    if len(tips) != 1:
        return None, revisions, (
            f"{commit[:9]} has {len(tips)} chain tips ({', '.join(sorted(tips))})"
        )
    return tips.pop(), revisions, None


def railway_active_deployment() -> tuple[dict | None, str | None]:
    """The active production deployment's commit and status, via the Railway CLI."""
    try:
        out = subprocess.run(
            ["railway", "status", "--json"],
            check=True, capture_output=True, text=True, timeout=90,
        ).stdout
    except FileNotFoundError:
        return None, "the `railway` CLI is not installed"
    except subprocess.TimeoutExpired:
        return None, "`railway status` timed out"
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or "").strip().splitlines()
        return None, f"`railway status` failed: {tail[-1] if tail else 'unknown'}"

    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None, "`railway status --json` did not return JSON"

    for env in data.get("environments", {}).get("edges", []):
        node = env.get("node", {})
        if node.get("name") != "production":
            continue
        for svc in node.get("serviceInstances", {}).get("edges", []):
            deployments = svc.get("node", {}).get("activeDeployments") or []
            for dep in deployments:
                meta = dep.get("meta") or {}
                if meta.get("commitHash"):
                    return {
                        "commit": meta["commitHash"],
                        "status": dep.get("status"),
                        "message": (meta.get("commitMessage") or "").splitlines()[:1],
                    }, None
    return None, "no active production deployment with a commit hash was reported"


def check_deploy_path(stamp: str) -> tuple[str, list[str]]:
    """Verdict is agrees | diverged | unverified."""
    dep, err = railway_active_deployment()
    if err:
        return "unverified", [f"could not read the active deployment: {err}"]

    commit, status = dep["commit"], dep["status"]
    subject = dep["message"][0] if dep["message"] else ""
    lines = [f"prod runs {commit[:9]} ({status}) {subject}".rstrip()]

    head, revisions, cerr = chain_head_at_commit(commit)
    if cerr:
        return "unverified", lines + [cerr]

    lines.append(f"that commit's chain head is {head}")

    if status != "SUCCESS":
        lines.append(
            f"deployment status is {status}, not SUCCESS — the invariant below only "
            "holds for a completed deploy, so this is reported, not judged"
        )
        return "unverified", lines

    if stamp == head:
        lines.append(f"stamp {stamp} matches — the deploy path accounts for it")
        return "agrees", lines

    if stamp not in revisions:
        lines.append(
            f"stamp {stamp} IS NOT A REVISION {commit[:9]} HAS EVER HEARD OF "
            f"(it knows {len(revisions)}, up to {head}). The deploy path could not "
            "have written this. Something stamped prod by hand."
        )
        lines.append(
            "This is the FLE-133 signature, and prod is undeployable from this "
            "commit: upgrade head cannot compute a path from an unknown revision."
        )
        return "diverged", lines

    lines.append(
        f"stamp {stamp} is behind that commit's head {head}. A successful deploy "
        "runs `upgrade head` to completion, so it should not have stopped short — "
        "suspect a pre-deploy container that did not finish, or a hand-written stamp."
    )
    return "diverged", lines


# ---------------------------------------------------------------------------
# CHECK 3 — the stamp against prod's actual schema
# ---------------------------------------------------------------------------
def build_reference(ref_admin_url: str, stamp: str) -> tuple[dict | None, str | None]:
    """Build the schema `stamp` claims, in a throwaway database. Always drops it."""
    db_name = f"alembic_drift_ref_{uuid.uuid4().hex[:12]}"
    admin = _sync_url(ref_admin_url)

    try:
        conn = psycopg2.connect(admin)
    except Exception as exc:
        return None, f"cannot connect to the reference server: {exc}"

    try:
        conn.autocommit = True
        conn.cursor().execute(f'CREATE DATABASE "{db_name}"')
    except Exception as exc:
        conn.close()
        return None, f"cannot create a reference database: {exc}"
    conn.close()

    parts = urlsplit(admin)
    ref_url = parts._replace(path=f"/{db_name}").geturl()

    try:
        env = dict(os.environ, DATABASE_URL=ref_url)
        proc = subprocess.run(
            [os.path.join(_SERVER_DIR, ".venv", "bin", "alembic"), "upgrade", stamp],
            cwd=_SERVER_DIR, env=env, capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
            return None, (
                f"`alembic upgrade {stamp}` failed while building the reference: "
                + " / ".join(tail)
            )
        return snapshot_schema(ref_url), None
    except subprocess.TimeoutExpired:
        return None, f"`alembic upgrade {stamp}` timed out building the reference"
    except Exception as exc:
        return None, f"building the reference failed: {exc}"
    finally:
        try:
            conn = psycopg2.connect(admin)
            conn.autocommit = True
            conn.cursor().execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
            conn.close()
        except Exception as exc:  # noqa: BLE001 — a leaked scratch DB must be visible
            print(f"  WARNING: could not drop reference database {db_name}: {exc}")


def check_schema(prod_url: str, ref_admin_url: str, stamp: str) -> tuple[str, list[str]]:
    """Verdict is clean | drifted | unverified."""
    if _hostport(prod_url) == _hostport(ref_admin_url):
        return "unverified", [
            "refusing to build the reference on prod's own server — point --ref-url "
            "at a non-prod Postgres"
        ]

    reference, err = build_reference(ref_admin_url, stamp)
    if err:
        return "unverified", [err]

    prod = snapshot_schema(prod_url)
    lines = [f"built the schema {stamp} claims, and diffed it against prod"]

    missing_tables = sorted(reference["tables"] - prod["tables"])
    missing_cols = sorted(set(reference["columns"]) - set(prod["columns"]))
    missing_cons = sorted(reference["constraints"] - prod["constraints"])
    missing_idx = sorted(reference["indexes"] - prod["indexes"])
    mismatched = sorted(
        (k, reference["columns"][k], prod["columns"][k])
        for k in set(reference["columns"]) & set(prod["columns"])
        if reference["columns"][k] != prod["columns"][k]
    )

    drift = bool(missing_tables or missing_cols or missing_cons or missing_idx or mismatched)

    for t in missing_tables:
        lines.append(f"  MISSING TABLE      {t}")
    for tbl, col in missing_cols:
        lines.append(f"  MISSING COLUMN     {tbl}.{col}")
    for tbl, name, ctype in missing_cons:
        lines.append(f"  MISSING CONSTRAINT {tbl}.{name} (contype {ctype})")
    for tbl, name in missing_idx:
        lines.append(f"  MISSING INDEX      {tbl}.{name}")
    for (tbl, col), want, got in mismatched:
        lines.append(f"  COLUMN DIFFERS     {tbl}.{col}: expected {want}, prod has {got}")

    # Extras are reported but are not drift: prod legitimately carries things the
    # reference cannot have, and a later revision's leftovers are not this check's
    # business. Shown because "why is this here" is the next question.
    extras = sorted(set(prod["columns"]) - set(reference["columns"]))
    extra_tables = sorted(prod["tables"] - reference["tables"])
    for t in extra_tables:
        lines.append(f"  (extra in prod)    table {t}")
    for tbl, col in extras:
        lines.append(f"  (extra in prod)    column {tbl}.{col}")

    if drift:
        lines.append(
            f"prod is stamped {stamp} but does not have {stamp}'s effects. The stamp "
            "is not evidence the migration ran."
        )
        return "drifted", lines

    lines.append(f"prod's schema matches {stamp} exactly — the stamp is honest")
    return "clean", lines


# ---------------------------------------------------------------------------
def _section(title: str, lines: list[str]) -> None:
    print(f"\n{title}")
    for line in lines:
        print(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check prod's alembic_version against the chain, the deployed "
                    "commit, and prod's actual schema.",
    )
    ap.add_argument(
        "--from-railway", action="store_true",
        help="resolve the prod URL from `railway variables --service Postgres`",
    )
    ap.add_argument(
        "--ref-url", default=os.environ.get("ALEMBIC_DRIFT_REF_URL"),
        help="non-prod Postgres to build the reference schema in (CHECK 3)",
    )
    ap.add_argument("--skip-deploy-check", action="store_true", help="skip CHECK 2")
    ap.add_argument("--skip-schema-diff", action="store_true", help="skip CHECK 3")
    args = ap.parse_args(argv)

    prod_url = os.environ.get("DATABASE_URL")
    if args.from_railway and not prod_url:
        try:
            out = subprocess.run(
                ["railway", "variables", "--service", "Postgres", "--json"],
                check=True, capture_output=True, text=True, timeout=90,
            ).stdout
            prod_url = json.loads(out)["DATABASE_PUBLIC_URL"]
        except Exception as exc:
            print(f"could not resolve DATABASE_PUBLIC_URL from Railway: {exc}")
            return EXIT_CANNOT_RUN
    if not prod_url:
        print("DATABASE_URL is not set. See the usage block at the top of this file.")
        return EXIT_CANNOT_RUN

    print("Fletcher — prod alembic drift check (FLE-134)")

    try:
        stamp, problems = read_prod_stamp(prod_url)
    except Exception as exc:
        print(f"\ncannot read prod's alembic_version: {exc}")
        return EXIT_CANNOT_RUN

    if stamp is None:
        _section("STAMP", problems)
        print("\nVERDICT: cannot establish a current revision. Nothing else can be checked.")
        return EXIT_CANNOT_RUN

    print(f"  prod alembic_version : {stamp}")

    unverified = 0

    chain_verdict, head, chain_lines = check_chain(stamp)
    _section(f"CHECK 1 — the stamp in this checkout's chain [{chain_verdict.upper()}]",
             chain_lines)

    if args.skip_deploy_check:
        deploy_verdict, deploy_lines = "skipped", ["skipped by --skip-deploy-check"]
    else:
        deploy_verdict, deploy_lines = check_deploy_path(stamp)
    _section(f"CHECK 2 — the stamp vs the commit prod runs [{deploy_verdict.upper()}]",
             deploy_lines)
    if deploy_verdict in ("unverified", "skipped"):
        unverified += 1

    if args.skip_schema_diff:
        schema_verdict, schema_lines = "skipped", ["skipped by --skip-schema-diff"]
    elif not args.ref_url:
        schema_verdict, schema_lines = "unverified", [
            "no --ref-url (or ALEMBIC_DRIFT_REF_URL), so there is nowhere to build "
            "the reference schema"
        ]
    else:
        schema_verdict, schema_lines = check_schema(prod_url, args.ref_url, stamp)
    _section(f"CHECK 3 — the stamp vs prod's actual schema [{schema_verdict.upper()}]",
             schema_lines)
    if schema_verdict in ("unverified", "skipped"):
        unverified += 1

    # --- verdict ----------------------------------------------------------
    print()
    if chain_verdict == "unresolvable":
        print("VERDICT: PROD IS UNDEPLOYABLE. The stamp is not in the deployed chain, "
              "so the pre-deploy container will abort before touching the schema.")
        return EXIT_DRIFT
    if deploy_verdict == "diverged":
        print("VERDICT: THE STAMP WAS NOT WRITTEN BY THE DEPLOY PATH. It disagrees "
              "with the commit prod is running.")
        return EXIT_DRIFT
    if schema_verdict == "drifted":
        print(f"VERDICT: SCHEMA DRIFT. Prod is stamped {stamp} without {stamp}'s "
              "effects, so `upgrade head` will skip it forever and a forward repair "
              "migration is needed.")
        return EXIT_DRIFT

    if unverified:
        print(f"VERDICT: no fault found, but {unverified} check(s) did not run. "
              "Not a clean bill of health.")
        return EXIT_ATTENTION
    if chain_verdict == "behind":
        print(f"VERDICT: healthy. Prod is stamped {stamp}, behind head {head} — an "
              "ordinary pending deploy, and every check agrees.")
        return EXIT_ATTENTION
    print(f"VERDICT: clean. Prod is stamped {stamp} == head, the deployed commit "
          "accounts for it, and the schema matches.")
    return EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
