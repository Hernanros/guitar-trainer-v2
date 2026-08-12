"""GET + POST /api/v1/admin/curator — skill-node proposal curator queue (D-11).

X-Admin-Token authentication is enforced on BOTH endpoints via get_admin_token dep.
Constant-time comparison in get_admin_token (hmac.compare_digest) prevents timing attacks.

Threat model:
  T-04-03-04 CSRF: single-user POC accepts CSRF risk on POST; X-Admin-Token custom header
    cannot be injected by cross-origin form submissions (CORS/preflight blocks non-simple
    headers), so cross-origin attacks are neutralised without a CSRF nonce.
  T-04-03-05 IDOR: single admin manages the whole system — cross-user visibility intentional.
"""
import uuid as _uuid
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_token
from app.db.session import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CuratorActionBody(BaseModel):
    """JSON body schema for POST /admin/curator/action (for API consumers).

    The HTML form uses Form() parameters directly; this model documents the
    equivalent JSON contract for programmatic access.
    """

    proposal_id: UUID
    action: Literal["approve", "reject", "merge_with"]
    canonical_id: Optional[UUID] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACK_LINK = "<a href='/api/v1/admin/curator'>Back to queue</a>"
_ACTION_RECORDED_HTML = (
    "<html><body>"
    "<p>Action recorded.</p>"
    f"{_BACK_LINK}"
    "</body></html>"
)

_PAGE_STYLE = """
<style>
  body { font-family: sans-serif; padding: 1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.85rem; }
  th { background: #f0f0f0; }
  .actions button { margin-right: 4px; cursor: pointer; }
  .tab-links { margin-bottom: 1rem; }
  .tab-links a { margin-right: 1rem; }
</style>
"""


def _pending_table_html(rows: list) -> str:
    """Render skill_node_proposals rows as HTML table with approve/reject/merge_with actions."""
    if not rows:
        return "<p><em>No pending proposals.</em></p>"
    header = (
        "<table>"
        "<tr>"
        "<th>ID</th><th>Proposed Name</th><th>Fuzzy Score</th>"
        "<th>Verdict</th><th>Reason</th><th>Created At</th><th>Actions</th>"
        "</tr>"
    )
    body_rows = []
    for row in rows:
        row_id = str(row.id)
        approve_form = (
            f"<form method='POST' action='/api/v1/admin/curator/action' style='display:inline'>"
            f"<input type='hidden' name='proposal_id' value='{row_id}'>"
            f"<input type='hidden' name='action' value='approve'>"
            f"<button type='submit'>Approve</button>"
            f"</form>"
        )
        reject_form = (
            f"<form method='POST' action='/api/v1/admin/curator/action' style='display:inline'>"
            f"<input type='hidden' name='proposal_id' value='{row_id}'>"
            f"<input type='hidden' name='action' value='reject'>"
            f"<button type='submit'>Reject</button>"
            f"</form>"
        )
        body_rows.append(
            f"<tr>"
            f"<td><small>{row_id[:8]}…</small></td>"
            f"<td>{row.proposed_name}</td>"
            f"<td>{row.fuzzy_score}</td>"
            f"<td>{row.verifier_verdict or ''}</td>"
            f"<td>{row.verifier_reason or ''}</td>"
            f"<td>{str(row.created_at)[:19]}</td>"
            f"<td class='actions'>{approve_form}{reject_form}</td>"
            f"</tr>"
        )
    return header + "".join(body_rows) + "</table>"


def _rejected_table_html(rows: list) -> str:
    """Render skill_node_rejections rows as HTML table."""
    if not rows:
        return "<p><em>No rejections recorded.</em></p>"
    header = (
        "<table>"
        "<tr><th>Proposed Name</th><th>Reason</th><th>Created At</th></tr>"
    )
    body_rows = [
        f"<tr>"
        f"<td>{row.proposed_name}</td>"
        f"<td>{row.reason}</td>"
        f"<td>{str(row.created_at)[:19]}</td>"
        f"</tr>"
        for row in rows
    ]
    return header + "".join(body_rows) + "</table>"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/admin/curator", response_class=HTMLResponse)
async def curator_queue(
    tab: str = "pending",
    _token: None = Depends(get_admin_token),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render curator queue HTML page.

    ?tab=pending (default) shows pending skill_node_proposals.
    ?tab=rejected shows recent skill_node_rejections (last 50).
    """
    tab_links = (
        "<div class='tab-links'>"
        "<a href='?tab=pending'>Pending Proposals</a>"
        "<a href='?tab=rejected'>Recent Rejections</a>"
        "</div>"
    )

    if tab == "rejected":
        result = await db.execute(
            text(
                "SELECT proposed_name, reason, created_at "
                "FROM skill_node_rejections "
                "ORDER BY created_at DESC LIMIT 50"
            )
        )
        rows = result.fetchall()
        content_html = _rejected_table_html(rows)
        heading = "<h2>Recent Rejections</h2>"
    else:
        result = await db.execute(
            text(
                "SELECT id, proposed_name, fuzzy_score, verifier_verdict, verifier_reason, created_at "
                "FROM skill_node_proposals "
                "WHERE status = 'pending' "
                "ORDER BY created_at DESC"
            )
        )
        rows = result.fetchall()
        content_html = _pending_table_html(rows)
        heading = "<h2>Pending Proposals</h2>"

    html = (
        f"<html>"
        f"<head>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Fletcher — Curator Queue</title>"
        f"{_PAGE_STYLE}"
        f"</head>"
        f"<body>"
        f"<h1>Fletcher Curator Queue</h1>"
        f"{tab_links}"
        f"{heading}"
        f"{content_html}"
        f"</body>"
        f"</html>"
    )
    return HTMLResponse(content=html)


@router.post("/admin/curator/action")
async def curator_action(
    proposal_id: UUID = Form(...),
    action: str = Form(...),
    canonical_id: Optional[str] = Form(None),
    _token: None = Depends(get_admin_token),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Process a curator action on a skill_node_proposals row.

    Accepts form-encoded body (from the HTML curator page).
    Actions:
      approve   — mark proposal approved; if a user-scoped skill_nodes row for this
                  proposed_name has canonical_node_id=NULL, update it to a new canonical
                  (canonical_node_id = self.id).
      reject    — mark proposal rejected; insert a skill_node_rejections row.
      merge_with — mark proposal merged into canonical_id; update user-scoped skill_nodes
                   rows for the same proposed_name to point at canonical_id.
    """
    if action not in ("approve", "reject", "merge_with"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {action!r}. Must be approve, reject, or merge_with.")

    # Fetch the proposal to get the proposed_name (needed for skill_nodes lookup)
    proposal_result = await db.execute(
        text("SELECT id, proposed_name, status FROM skill_node_proposals WHERE id = :pid"),
        {"pid": str(proposal_id)},
    )
    proposal_row = proposal_result.fetchone()
    if proposal_row is None:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found.")

    proposed_name = proposal_row.proposed_name

    if action == "approve":
        # Mark proposal approved
        await db.execute(
            text("UPDATE skill_node_proposals SET status = 'approved' WHERE id = :pid"),
            {"pid": str(proposal_id)},
        )
        # If a user-scoped skill_nodes row for this proposed_name exists with canonical_node_id=NULL,
        # promote it to canonical by setting canonical_node_id = self.id (the row IS the new canonical).
        await db.execute(
            text(
                "UPDATE skill_nodes "
                "SET canonical_node_id = id "
                "WHERE name = :name AND canonical_node_id IS NULL"
            ),
            {"name": proposed_name},
        )
        await db.commit()

    elif action == "reject":
        await db.execute(
            text("UPDATE skill_node_proposals SET status = 'rejected' WHERE id = :pid"),
            {"pid": str(proposal_id)},
        )
        await db.execute(
            text(
                "INSERT INTO skill_node_rejections (id, proposed_name, reason, verifier_response, created_at) "
                "VALUES (:rid, :name, 'rejected by curator', NULL, NOW())"
            ),
            {"rid": str(_uuid.uuid4()), "name": proposed_name},
        )
        await db.commit()

    elif action == "merge_with":
        if not canonical_id:
            raise HTTPException(
                status_code=400,
                detail="canonical_id is required for merge_with action.",
            )
        try:
            canonical_uuid = UUID(canonical_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"canonical_id {canonical_id!r} is not a valid UUID.")

        await db.execute(
            text(
                "UPDATE skill_node_proposals "
                "SET status = 'merged', canonical_id = :cid "
                "WHERE id = :pid"
            ),
            {"cid": str(canonical_uuid), "pid": str(proposal_id)},
        )
        # Update user-scoped skill_nodes rows for this proposed_name to point at canonical_id
        await db.execute(
            text(
                "UPDATE skill_nodes "
                "SET canonical_node_id = :cid "
                "WHERE name = :name"
            ),
            {"cid": str(canonical_uuid), "name": proposed_name},
        )
        await db.commit()

    return HTMLResponse(
        content=_ACTION_RECORDED_HTML,
        status_code=303,
        headers={"Location": "/api/v1/admin/curator"},
    )
