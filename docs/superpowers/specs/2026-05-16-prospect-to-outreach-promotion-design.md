# Prospect-to-Outreach Promotion — Design

**Date:** 2026-05-16
**Status:** Draft (pending review)
**Owner:** tilak

## Problem

The Prospect Finder (`/dashboard/prospects`) is currently search + CSV-export only. There is no path from a discovered restaurant to qualification and outreach inside the app. Reps see a list of high-ICP-score restaurants but cannot promote them into the CRM/outreach pipeline without leaving the dashboard.

The Qualification, Lead, and Outreach pages exist but are disconnected. The qualification page accepts only a city/state batch criteria; outreach pages manage campaigns but have no flow to add targets to them from the dashboard. Several bugs in the existing campaign workflow make it non-functional even for users who attempt to use it manually.

This design adds the missing connective tissue — a "Promote to Lead" path from the Prospect Finder that runs qualification and attaches the lead to an outreach campaign — and repairs the campaign workflow bugs that would otherwise prevent the new feature from being usable end-to-end.

## Goals

- Single-click and bulk paths to convert prospect rows into Leads from the Prospect Finder.
- Qualification runs automatically on promotion; high-confidence results auto-approve, low-confidence land in the existing Pending Review queue.
- Promoted Leads attach to a campaign (existing or newly created inline) at promotion time.
- Already-promoted restaurants are visually distinct in the finder and link to their Lead.
- Repair the existing campaign workflow (broken detail link, dropped form fields, no UI feedback on status changes, missing target names, missing add-targets path).

## Non-goals

- Editing campaign templates, schedules, or follow-up cadences from the promotion flow. Those stay on `/dashboard/outreach`.
- Per-role permission gating on promotion (any logged-in user can promote).
- Browser-based end-to-end tests for the new flow.
- Replacing the existing city/state batch qualification flow.

## Decisions (from brainstorming)

1. Promotion creates a full **Lead** record (CRM flow with stage history), not a lightweight tag.
2. UI supports both **per-row promote** and **bulk select + promote**.
3. Qualification runs **automatically on promotion**; high-confidence auto-approves, low-confidence enters the needs-review queue.
4. The rep **picks a campaign** at promotion time (or creates a new one inline, or skips campaign attachment).
5. Already-promoted restaurants show an **"Already a Lead" badge + View link**; no re-promote action.
6. The campaign workflow bugs are **folded into this design** rather than treated as a separate effort.

## User flow & UI

### Prospect Finder (`prospects.html`)

Add a leftmost checkbox column for bulk select (header checkbox toggles all rows). Add a rightmost "Action" column:

- **Not yet a Lead:** `Promote to Lead` button.
- **Already a Lead:** "Already a Lead" pill + `View →` link to `/dashboard/leads/{id}`.

Above the table, a bulk-action toolbar appears when ≥1 row is checked: "*N selected*" · campaign dropdown · `Promote N to Leads` button · `Clear selection`. The existing CSV export button moves to the same row so they don't compete for space.

### Promotion modal (used by both single and bulk)

Triggered by `Promote to Lead` (single) or `Promote N to Leads` (bulk). Contents:

- **Header** — "Promote 1 restaurant" / "Promote 12 restaurants to Leads".
- **Campaign** — required dropdown grouped as: *Active campaigns* · *Drafts* · `+ Create new campaign…` · `— None (add to campaign later) —`.
- **Lead owner** — optional dropdown of reps, defaults to current user.
- **Notes** — optional textarea written to the Lead's first activity note.
- **Bulk-only summary** — "*Will skip N restaurants already promoted*" when applicable.
- **Submit button** — label adapts to the campaign choice:
  - Existing campaign → `Promote and qualify`
  - New campaign → `Create campaign + promote and qualify`
  - None → `Promote and qualify (no campaign)`

### Inline "Create new campaign" sub-form

Selecting `+ Create new campaign…` expands the modal in place (no second modal). Fields:

- **Name** (required text)
- **Channel** — radio: Email · Call · SMS · Multi-channel (matches the existing `campaign_type` values in `outreach.html`; defaults Email)
- **Status** — radio: `Save as draft (no sends yet)` (default) · `Activate immediately`

Templates, schedule, and target criteria are deliberately not editable here; those belong on `/dashboard/outreach`.

### After submission

- Toast (upper-right): *"Promoted 12 leads · Qualification running · 8 added to 'Boston Pilot' campaign"*.
- Each affected row updates in place via htmx: checkbox cleared, action cell flips to `Already a Lead` + `View →`.
- Toast includes a `View Leads` link to `/dashboard/leads` filtered to the just-promoted set (filter mechanism — query param or session-scoped — left to implementation).

### Lead detail page (`lead_detail.html`) — small addition

A new top-section panel: **Qualification result** — confidence %, status (qualified / needs review / not qualified), and the top 3 signals. If status is `needs_review`, inline Approve / Reject buttons reusing the existing `PATCH /dashboard/qualification/{id}/review` endpoint.

### Campaign UI fixes (folded in)

`outreach.html`:

1. Fix Details link — change `/dashboard/outreach/campaigns/{{ c.id }}/detail` → `/dashboard/outreach/campaigns/{{ c.id }}`.
2. Render `campaign_stats` (already computed) — add Targets, Response Rate, Conversion Rate columns.
3. Add a Targets count column linking to the campaign-detail expand row.

`campaign_detail.html`:

4. Status buttons swap a status pill instead of `hx-swap="none"`.
5. Target list reads `target.restaurant.name` (eager-loaded), not the non-existent `target.restaurant_name`.
6. Add a "Lead" column linking to `/dashboard/leads/{lead_id}` when present.

## Backend orchestration

### New service: `src/services/prospect_promotion.py`

```
promote_prospects(
    session,
    restaurant_ids: list[UUID],
    campaign_id: UUID | None,
    new_campaign: NewCampaignSpec | None,  # mutually exclusive with campaign_id
    owner: str | None,
    notes: str | None,
    actor: str,
) -> PromotionResult
```

Per-restaurant steps (each in its own savepoint so failures isolate):

1. **Dedup check** — if a Lead exists for `restaurant_id`, skip → counted as `already_lead`.
2. **Create Lead** — `source="prospect_finder"`, `status="new"`, `lifecycle_stage="new"`, owner = provided or actor. Snapshot fields copied from the restaurant + latest ICP score (`icp_total_score`, `icp_fit_label`, `is_independent`, `has_delivery`, `matched_restaurant_name`, `match_confidence`). Initial stage-history entry written via existing lead service.
3. **QualificationResult** — if a result exists with status ∈ {qualified, needs_review} and `expires_at > now()`, reuse it; otherwise insert a new row with `status='pending'`, `expires_at = now() + 1 hour`.
4. **Attach to campaign** — if `campaign_id` or `new_campaign` provided, insert `OutreachTarget(campaign_id, restaurant_id, lead_id, status='pending', communication_status='queued')`. `UNIQUE(campaign_id, restaurant_id)` prevents dupes.
5. **Enqueue qualification task** — `qualify_restaurant_task.delay(restaurant_id, qualification_result_id, lead_id)` (skipped if reusing existing result).
6. **Audit log** — one `audit_logs` row capturing actor + restaurants + campaign per batch.

`PromotionResult` carries: `promoted`, `skipped_already_lead` (with their `lead_id`s), `failed` (with reasons), `campaign_id`, `qualification_task_ids`, `reused_qualifications`, `data_warnings`.

### Inline campaign creation

If `new_campaign` is provided, `create_campaign(name, campaign_type, status='draft'|'active', created_by=actor)` runs first in the outer transaction. Its `campaign_id` then flows into the per-restaurant loop. If the create raises, the request fails before any Lead is created — no orphan state.

### Qualification — always async

Single or bulk, qualification runs in Celery. Reasons: consistent UX (toast always says "Qualification running"), no blocking HTTP on LLM calls. The Lead detail page shows a "Qualifying..." badge that polls every 3 seconds until a final status arrives.

### Polling endpoint

`GET /dashboard/leads/{id}/qualification` — htmx partial returning the current qualification card. Stop conditions: status becomes final, 5 minutes elapse (badge becomes "Qualification stalled — retry"), or the user navigates away.

### New / changed routes

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/dashboard/prospects/promote` | Single or bulk promote (form-encoded). Returns htmx-fragment rows + toast partial. |
| `GET` | `/dashboard/leads/{id}/qualification` | htmx polling endpoint for the qualification card. |
| `POST` | `/dashboard/outreach/campaigns` (existing) | Accept `start_date`, `end_date`, and optional `status` form fields. |
| `PATCH` | `/dashboard/outreach/campaigns/{id}/status` (existing) | Return a rendered status pill partial. |

### Service-layer changes

- `outreach.list_targets` — add `selectinload(OutreachTarget.restaurant)` and `selectinload(OutreachTarget.lead)`.
- `outreach.create_campaign` (existing signature) — supports `start_date`, `end_date`, `status`.
- No change required to `lead service`, `qualification service`, or `outreach.add_target`.

## Data model

### Migration: `alembic revision -m "prospect promotion: nullable lead identity fields, dedup constraints"`

1. **`leads`**: `ALTER COLUMN first_name DROP NOT NULL`; same for `last_name`, `email`.
2. **`leads`**: `CREATE UNIQUE INDEX uq_leads_restaurant_id ON leads(restaurant_id) WHERE restaurant_id IS NOT NULL` — partial unique; serves both dedup enforcement and the lookup index for the promotion path's existing-Lead check.
3. **`outreach_targets`**: `CREATE UNIQUE INDEX uq_outreach_targets_campaign_restaurant ON outreach_targets(campaign_id, restaurant_id)`.

### Pre-migration sweep

`first_name`, `last_name`, `email` becoming nullable requires a grep for `lead.first_name`, `lead.last_name`, `lead.email` across the codebase. Any consumer that assumes truthiness (Jinja templates, export builders, send-time validators) gets a `None` guard or default. Identified pre-implementation; tracked in the implementation plan.

### Reused without change

- `QualificationResult` — `status='pending'` already supported (`models.py:450`).
- `LeadStageHistory`, `audit_logs`, `OutreachTarget.lead_id`, `OutreachCampaign` — no change.

### New `Lead.source` value

- `"prospect_finder"` — string column, no enum migration; documented as a recognized source.

## Edge cases & error handling

### Concurrency

- **Dup Lead** — defended by `uq_leads_restaurant_id`. Orchestrator catches `IntegrityError`, refetches existing Lead, reports as `already_lead`.
- **Dup target** — defended by `uq_outreach_targets_campaign_restaurant`. Treated as a benign no-op skip.
- **Inline campaign double-create** — outer transaction boundary ensures atomic intent.

### Partial failure (bulk)

- Per-restaurant savepoints isolate failures. `PromotionResult.failed` carries `[{restaurant_id, reason}]`. Toast: *"Promoted 8 · Skipped 3 already-Leads · Failed 1 — view details"*. Details link opens a modal with per-row reasons.
- If all restaurants fail/skip and a new campaign was created, the campaign survives (rep may add targets later); toast surfaces this explicitly.

### Qualification

- **Task never completes** — `expires_at = now() + 1 hour`. Existing `cleanup_tasks.py` gains a sweep that re-dispatches qualification for rows still `pending` past expiry.
- **`not_qualified` verdict** — Lead stays at stage `new`. Detail page shows verdict prominently; rep can override via Approve.
- **Prior qualification reused** — Celery dispatch skipped; toast reports reuse count.

### Data quality

- **No email AND no phone on restaurant** — promotion succeeds; response includes `data_warnings`. UI shows a non-blocking yellow note on affected rows: *"No contact info — outreach send may fail."*

### Capacity

- **Bulk size cap = 100** restaurants per request. Above that, the form rejects with a message pointing to the batch-by-city flow.
- **Rate limiting** — existing `rate_limit_dep.py` reused on `POST /dashboard/prospects/promote`.

### Auth

- `_require_login` on all new routes. No role gating in v1.

## Testing

### Unit — `tests/test_services/test_prospect_promotion.py`

1. Happy-path single.
2. Happy-path bulk (5 restaurants).
3. Already-Lead skip.
4. Inline campaign creation (success and partial failure cases).
5. Concurrent dup (simulated `IntegrityError`).
6. Qualification reuse (existing non-expired result).
7. Mixed batch (promoted + skipped + failed).
8. Empty-campaign case (new campaign survives with 0 targets).

### Migration — `tests/test_db/test_migrations.py` (add case)

- Upgrade → downgrade → upgrade round-trip.
- Insert Lead with NULL identity fields → succeeds after migration, fails before.

### Dashboard routes — `tests/test_dashboard/test_promotion.py`

1. Single promote returns htmx row update + toast.
2. Bulk promote.
3. Inline campaign creation appears in `/dashboard/outreach`.
4. Unauthenticated → 303 to login.
5. Bulk size 101 rejected with cap message.
6. `GET /dashboard/leads/{id}/qualification` returns correct partial in each status.

### Campaign-fix regressions — `tests/test_dashboard/test_outreach.py` (extend)

- Campaign detail route returns 200.
- `start_date` / `end_date` persisted on campaign create.
- Status PATCH returns rendered pill HTML.
- Outreach dashboard renders the new `campaign_stats` columns.
- `list_targets` eager-loads `restaurant.name`.

### Celery task — `tests/test_tasks/test_qualify_restaurant_task.py`

- Updates QualificationResult row, advances Lead lifecycle when confidence high.
- Failure path: retries N times, then cleanup task re-picks.

### Out of scope for v1

- Browser end-to-end (Playwright) tests.
- Load test for bulk size 100.

## Open questions

None known. All identified questions were resolved during brainstorming.

## Rollout

1. Migration deploys first (backwards-compatible: existing Leads keep their populated identity fields).
2. New service + routes deploy alongside the dashboard template changes.
3. Campaign-fix template changes ship in the same release.
4. No feature flag required — the new column / button is additive; existing flows are unchanged or repaired.
