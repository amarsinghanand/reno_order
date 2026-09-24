# Reno Order

Custom Frappe v16 app for a kitchen renovation workflow. Standard ERPNext Selling, Stock, Manufacturing, Buying and Accounts stay in place; this app owns the renovation-specific document and validations.

## Point 1 — Custom App & Reno Order

### What was built

- App: `reno_order`
- DocTypes:
  - **Reno Order** (submittable parent)
  - **Reno Order Item** (child table)
  - **Reno Settings** (single) for the discount approval threshold
- Server-side total calculation that overwrites client/API values
- Business validations (qty, rate, installation date)
- Discount submission gate using a configurable role
- `create_sales_order` method that creates one ERPNext Sales Order and blocks duplicates

### Assumptions

- App name is `reno_order` (Frappe requires snake_case). Title is **Reno Order**.
- Extra header fields `company`, `currency` and `sales_order` are required to create and track a standard Sales Order.
- `Order Type` defaults to `Standard` so Part 9 can backfill the same value.
- `Status` uses the assignment lifecycle values and is driven by the Frappe Workflow.
- Discount threshold defaults to **10%**. Approver role defaults to **Sales Manager**. System Manager may always submit.
- Sales User can submit orders at or below the threshold. Higher discounts require the approver role.
- Frappe 16.35 Contact has no `is_billing_contact` field, but ERPNext 16.35 queries it. The app adds that custom field so standard Sales Orders can be created.
- A Reno Order must be **submitted** before a Sales Order can be created.
- The Sales Order is created as **Draft**. Submit remains a user action.
- Item rates are taken from the Reno Order line; pricing rules are ignored so the SO matches Reno totals.
- Financial integrity is enforced in `validate()`. Read-only fields and client math are UX only.

## Point 2 — Workflow, Permissions & Automation

### Workflow

Standard Frappe Workflow **Reno Order Workflow** on the `status` field:

Draft → Confirmed → In Production → Ready for Installation → Installed → Closed, plus Cancelled.

| From | Action | To | Roles |
|---|---|---|---|
| Draft | Confirm | Confirmed | Sales User, Sales Manager |
| Confirmed | Start Production | In Production | Production User, Sales Manager |
| In Production | Mark Ready for Installation | Ready for Installation | Production User, Sales Manager |
| Ready for Installation | Mark Installed | Installed | Site Supervisor, Sales Manager |
| Installed | Close | Closed | Sales Manager, Accounts User |
| Confirmed / In Production / Ready | Cancel | Cancelled | Sales Manager |

Server-side `STATUS_TRANSITIONS` blocks API jumps that skip the workflow.

### Permissions

| Role | Visibility |
|---|---|
| Sales User | Own orders (`owner`) and orders assigned to them |
| Sales Manager | Own + team (Employee `reports_to`). If the manager has no Employee record, they see all Reno Orders |
| Production User | Submitted orders |
| Site Supervisor | Orders assigned to them |
| Accounts User | Read all |
| System Manager | Unrestricted |

Row filters are enforced in `permission_query_conditions` and `has_permission`. Site Supervisor selling-field locks are in Point 11.

### Automation

- Reaching **Installed** enqueues `process_installed_order` on the default queue (`enqueue_after_commit`, `deduplicate`).
- The job creates a **draft** Delivery Note from the linked Sales Order when that SO is submitted. It does not auto-submit the SO/DN, so stock is not reduced until a user submits the DN.
- If no submitted SO exists, processing status is `Awaiting Sales Order Submit`. Submitting the linked Sales Order retries the job.
- The same Delivery Note cannot be created twice (`delivery_note` link + `reno_order` on Delivery Note).
- The Delivery Note insert runs as a system job (`ignore_permissions`) because Site Supervisor may not have Stock rights. User-facing buttons still check permissions.
- Daily scheduler `flag_overdue_installations` sets **Overdue Installation** when Expected Installation Date < today and status is not Installed / Closed / Cancelled.

### Point 2 assumptions

- Workflow is the UI path; `status` is read-only on the form.
- Confirm submits the document (`docstatus` 1). Cancelled uses `docstatus` 2.
- Downstream Installed work prepares a draft Delivery Note; it does not post stock or GL.
- Team visibility uses HR Employee `reports_to`, not a custom User field.

## Point 3 — ERPNext Integration

Complete standard order-to-cash, referenced both ways:

**Reno Order → Sales Order → Delivery Note → Sales Invoice**

| Step | How | Reno Order field | ERPNext field |
|---|---|---|---|
| Sales Order | **Create Sales Order** (after submit) | `sales_order` | `Sales Order.reno_order` |
| Delivery Note | **Create Delivery Note** after SO submit, or Installed job | `delivery_note` | `Delivery Note.reno_order` |
| Sales Invoice | **Create Sales Invoice** after DN submit | `sales_invoice` | `Sales Invoice.reno_order` |

ERPNext **Create** from SO/DN also copies `reno_order` and writes the link back on the Reno Order. Connections on the form list the three documents.

Each mapper is standard ERPNext (`make_delivery_note`, `make_sales_invoice`). The custom app only creates, links, and blocks duplicates.

### Where stock and accounts move

| Event | Stock | Accounting |
|---|---|---|
| Reno Order submit | None | None. Custom document only. |
| Sales Order submit | **Reserved** (`reserved_qty` / Stock Reservation Entry if Selling Settings enable it). Bin available qty falls; actual qty does not. | None |
| Delivery Note submit | **Reduced**. Stock Ledger Entry (SLE) posts issue. Reserved qty is released against the issue. | Optional perpetual inventory: stock credit / COGS debit |
| Sales Invoice submit | None in this flow (invoice is made from the DN, `update_stock` stays 0) | **GL**: Debit Debtors, credit Income (and tax accounts) |
| Payment Entry | None | Debit Bank, credit Debtors |

### Cancellation

Cancel in reverse of posting: **Sales Invoice → Delivery Note → Sales Order → Reno Order**.

- A submitted SI/DN/SO blocks Reno Order cancel. That stops reserved stock or posted SLE/GL from being left behind.
- Draft downstream documents do not block cancel.
- After a downstream document is cancelled, a new one may be created (`docstatus < 2` is the duplicate key).
- Reno Order cancel never auto-cancels SO/DN/SI.

### Duplicate prevention

| Document | Guard |
|---|---|
| Sales Order | `sales_order` link + `Sales Order.reno_order` where `docstatus < 2` |
| Delivery Note | Same pattern; Installed job is idempotent (`deduplicate` + existing DN) |
| Sales Invoice | Same pattern; mapper also refuses already-invoiced DN qty |

### Demo walkthrough

1. Submit / Confirm a Reno Order.
2. **Create Sales Order** → submit the SO (stock reserved).
3. **Create Delivery Note** (or mark Installed) → submit the DN (stock reduced).
4. **Create Sales Invoice** → submit the SI (GL posted).
5. Open Connections / View buttons to confirm the three links.

The assignment asks for a recorded demo of this flow; the code path above is what to record.

### Point 3 assumptions

- Sales Invoice is created from the **submitted Delivery Note**, not from the Sales Order, so stock and GL stay on separate documents.
- Downstream documents stay **Draft** until a user submits them.
- Perpetual inventory GL on DN submit depends on Company / Stock Settings, not this app.

## Point 4 — Manufacturing Scenario

Kitchen Cabinet is manufactured internally with standard ERPNext manufacturing. The custom app only seeds masters and creates/links the Work Order.

| Master | Value |
|---|---|
| Finished item | Kitchen Cabinet |
| Raw materials | Plywood (2), Laminate (2), Adhesive (1), Hardware (1) |
| Operations / workstations | Cutting → Cutting Bench, Assembly → Assembly Bench, Finishing → Finishing Booth |
| BOM | Active default BOM with operations; transfer material against **Work Order** |

**How Reno Order connects**

1. A Reno Order line with a default BOM (Kitchen Cabinet) is the manufacturing demand.
2. **Create Work Order** inserts a standard Work Order (`production_item`, BOM, qty, warehouses) and sets `Work Order.reno_order`.
3. If a submitted Sales Order exists for the same item, the Work Order also stores `sales_order` / `sales_order_item` so produced qty updates the SO.
4. Work Order **submit** creates standard Job Cards (Cutting, Assembly, Finishing).
5. Standard `make_stock_entry`: **Material Transfer for Manufacture** (Stores → WIP), then **Manufacture** (consume RM, receive Kitchen Cabinet).
6. Job Card and Stock Entry inherit `reno_order` from the Work Order.

No core manufacturing DocTypes were changed. Connections on Reno Order list Work Order, Job Card, and Stock Entry.

### Demo walkthrough

1. Confirm a Reno Order for Kitchen Cabinet.
2. **Create Work Order** → submit the Work Order (Job Cards appear).
3. Complete / submit each Job Card.
4. Create and submit Material Transfer for Manufacture, then Manufacture.
5. Kitchen Cabinet qty appears in Finished Goods.

### Point 4 assumptions

- One open Work Order per Reno Order + item. Cancel it before creating another.
- Capacity planning is disabled on this site so Job Cards do not need a full workstation calendar.
- Raw-material purchase is Point 5. The demo receives RM with a Material Receipt only so manufacture can post.
- Work Order cancel is required before Reno Order cancel if the WO is submitted.

## Point 5 — Buying Scenario

Out-of-stock **Hardware** on a Reno Order is procured with standard ERPNext buying. The custom app only creates the Material Request and keeps `reno_order` on every buying document.

**Reno Order → Material Request → RFQ → Supplier Quotation → Purchase Order → Purchase Receipt → Purchase Invoice**

| Step | How |
|---|---|
| Material Request | **Create Material Request** for stock items that have no BOM and qty > warehouse balance |
| RFQ / SQ / PO / PR / PI | Standard ERPNext **Create**. `reno_order` is copied from the previous document |

Kitchen Cabinet is skipped (it has a BOM — that is manufactured in Point 4). Hardware is the assignment item.

### Where stock and accounts move

| Event | Stock | Accounting |
|---|---|---|
| Material Request / RFQ / SQ / PO submit | None | None. Commitment only. |
| Purchase Receipt submit | **Increased**. SLE posts receipt into the warehouse. | Perpetual inventory: debit Stock In Hand, credit Stock Received But Not Billed |
| Purchase Invoice submit | None (invoice is made from the PR, `update_stock` stays 0) | **GL**: debit Stock Received But Not Billed, credit Creditors |

### Demo walkthrough

1. Confirm a Reno Order whose Hardware qty is greater than on-hand stock.
2. **Create Material Request** → submit.
3. Create RFQ (supplier **Reno Hardware Supplier**) → submit.
4. Create Supplier Quotation → submit.
5. Create Purchase Order → submit.
6. Create Purchase Receipt → submit (stock increases).
7. Create Purchase Invoice → submit (creditor GL).

### Point 5 assumptions

- One open Material Request per Reno Order. Manufactured items (default BOM) are never requested.
- RFQ email to suppliers is left off (`send_email` = 0). No credentials are stored.
- Supplier **Reno Hardware Supplier** is created on migrate.

## Point 6 — REST API / Mobile Integration

Site Supervisor mobile APIs. All methods are `@frappe.whitelist` **without** `allow_guest`. They only touch installation fields (status, remarks, photos). Selling fields cannot be changed through these endpoints.

| Method | Endpoint | Body |
|---|---|---|
| Update status | `POST /api/method/reno_order.api.update_installation_status` | `reno_order`, `status` (`Installed` only) |
| Add remarks | `POST /api/method/reno_order.api.add_installation_remarks` | `reno_order`, `remarks` |
| Attach photo | `POST /api/method/reno_order.api.attach_site_photo` | `reno_order` + multipart `file` (or `filename` + `filedata`) |
| Read job | `GET /api/method/reno_order.api.get_installation` | `reno_order` |

Status uses the same `STATUS_TRANSITIONS` / workflow as Desk. Installed is allowed only from **Ready for Installation**. Photos are stored as **private** File attachments (jpeg, png, webp, gif).

### Authentication

Frappe token auth — no passwords in the app.

1. Desk → User → API Access → **Generate Keys**.
2. Mobile stores the key pair in the device keychain, not in source.
3. Every request sends:

```
Authorization: token <api_key>:<api_secret>
```

Alternatives: session cookie after `/api/method/login`, or OAuth bearer if the site has an OAuth client. Guest requests return `AuthenticationError`.

### Authorization

- Caller must be logged in.
- `frappe.has_permission(..., write)` plus the Reno Order permission hook.
- Site Supervisor may update only orders where `assigned_to` is themselves.
- Site Supervisor has **submit** on Reno Order so Frappe allows `update_after_submit` for installation fields. They still cannot create or cancel orders.
- Sales Manager / System Manager may update installation details for demo/support.
- Invalid transitions and unassigned supervisors return explicit errors.

```bash
curl -X POST http://127.0.0.1:8011/api/method/reno_order.api.update_installation_status \
  -H "Authorization: token $API_KEY:$API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"reno_order":"RO-2026-00001","status":"Installed"}'
```

### Point 6 assumptions

- The mobile app is a Frappe client. We do not add a separate JWT stack.
- This endpoint only marks **Installed**. Other lifecycle steps stay on Desk / workflow.
- Site photos are private Files, not a new child table.

## Point 7 — Third-Party Integration

External **CRM** sync. A mock CRM is built in (`mock://reno-crm`) so the bench does not need the internet and does not HTTP-call itself (that would deadlock `bench serve`).

On submit, CRM work is **queued** (see Point 8). The job POSTs the Reno Order as JSON. Failures are logged and stored on the document; they do not roll back the order.

| Concern | How it is handled |
|---|---|
| Authentication | Outbound `Authorization: Bearer <token>`. Inbound HMAC-SHA256 (`X-Reno-Signature`) |
| Request / response | JSON payload + `crm_id` on success |
| Error handling | 4xx is fatal. Missing `crm_id` is fatal. Order stays submitted |
| Logging | `frappe.logger("reno_crm")`, Error Log on failure, **Integration Request** row per attempt |
| Retry | Timeouts, connection errors, HTTP 429/5xx. Exponential backoff, `crm_max_retries` |
| Timeout | `requests` timeout from `crm_timeout_seconds` (default 5s) |
| Credentials | Password fields on Reno Settings (encrypted). Never written to source or Integration Request headers |

Desk → Reno Order → **Sync to CRM** retries a failed push. **View → CRM Requests** opens the Integration Request list.

Inbound webhook: `POST /api/method/reno_order.crm.receive_crm_webhook`

### Point 7 assumptions

- The mock CRM is acceptable per the assignment. Point the Base URL at a real host to use HTTP.
- Tokens are generated on migrate if missing. They are not hardcoded.

## Point 8 — Background Processing

A 10–20 second CRM call must not block Reno Order save/submit. Submit only **enqueues** `reno_order.crm.run_crm_sync_job`.

| Requirement | Implementation |
|---|---|
| Background job | `frappe.enqueue` after commit |
| Queue | **`long`** (default timeout 1500s; job timeout 120s) |
| Failure handling | Job catches errors, sets `crm_sync_status=Failed`, writes Error Log + Integration Request. Submit already committed. |
| Retry | HTTP retries inside the job (Point 7). Job-level re-queue up to 3 attempts on timeout / 429 / 5xx |
| Logging | `frappe.logger("reno_crm")` on enqueue, start, retry, finish |
| Duplicate protection | Stable `job_id` `reno-crm-sync-{name}`, `deduplicate=True`, `is_job_enqueued` guard |

Desk shows **Queued** immediately. The long worker then sets **Synced** or **Failed**. **Sync to CRM** queues another job; if one is already queued, it is skipped.

### Point 8 assumptions

- Installed → Delivery Note already used `default` + `deduplicate` (Point 2). CRM uses `long` because the remote API is the slow part.
- Unit tests call `run_crm_sync_job` directly; they do not depend on a running worker.
- `bench start` (or a long worker) is required for live async execution.

## Point 9 — Data Migration / Patch

`Order Type` is now mandatory (`Standard` / `Custom` / `Premium`). About 50,000 live Reno Orders may have a blank value. Patch:

`reno_order.patches.v1_0.backfill_reno_order_type`

It sets **Standard** only where `trim(ifnull(order_type, '')) = ''`. Custom / Premium / Standard are left alone.

| Requirement | How |
|---|---|
| Production-safe | Raw SQL, no `get_doc` / `save` / hooks / CRM jobs. `modified` is not touched |
| No overwrite | `WHERE` only blank / whitespace |
| Idempotent | Second run updates 0 rows. Recorded in **Patch Log** |
| Locking / performance | 1,000-name batches, `ORDER BY name`, `commit` after each batch so InnoDB row locks are short |

### Deploy and validate on a live site

1. Take a DB backup (`bench --site <site> backup --with-files`).
2. Deploy the app (`git pull` / `bench update --reset` as you usually do). Do **not** add the column as mandatory in the same release without this patch in `[post_model_sync]`.
3. Run during a quiet window: `bench --site <site> migrate`.
4. Frappe runs post-model-sync patches after the DocType (and `reqd`) is in the schema. The patch then backfills blanks so Desk does not fail on old rows.
5. Validate:

```sql
SELECT count(*) FROM `tabReno Order` WHERE trim(ifnull(order_type, '')) = '';
-- expect 0

SELECT order_type, count(*) FROM `tabReno Order` GROUP BY order_type;
-- Custom / Premium counts unchanged
```

6. Desk → **Patch Log** should list `reno_order.patches.v1_0.backfill_reno_order_type`.
7. Re-run `migrate` any time. The patch is skipped once logged; `execute()` itself is also a no-op if no blanks remain.
8. If you must re-apply after a failed mid-run, `bench --site <site> execute reno_order.patches.v1_0.backfill_reno_order_type.execute` is safe.

Do not load 50k documents in Python. Do not one unbounded `UPDATE` without a batch commit.

### Point 9 assumptions

- New documents already default to **Standard** in the DocType and `validate()`.
- Blank includes `NULL`, `''`, and whitespace.
- Commits are skipped when `frappe.flags.in_test` so unit tests can roll back.

## Point 10 — Database & Performance

Desk report **Monthly Reno Order Value** (`Reno Order` → query report). Last 12 months, grouped by month and status, summing `grand_total`.

### SQL

```sql
SELECT
  DATE_FORMAT(transaction_date, '%Y-%m') AS month,
  status,
  SUM(grand_total) AS order_value,
  COUNT(*) AS order_count
FROM `tabReno Order`
WHERE transaction_date >= %(from_date)s
  AND transaction_date <= %(to_date)s
  AND docstatus < 2
GROUP BY DATE_FORMAT(transaction_date, '%Y-%m'), status
ORDER BY month, status
```

The date filter is a **range on the column**, not `DATE_FORMAT(transaction_date)` in the WHERE clause, so the index can be used.

### EXPLAIN before optimization

173 live rows. Indexes were only `PRIMARY (name)`, `creation`, `modified`.

| type | possible_keys | key | rows | Extra |
|---|---|---|---|---|
| ALL | NULL | NULL | 173 | Using where; Using temporary; Using filesort |

Full table scan. That is the plan `IGNORE INDEX (idx_reno_order_date_status)` still produces after the index exists.

### Optimization

Covering composite index (patch `reno_order.patches.v1_0.add_reno_order_date_status_index`):

```sql
ALTER TABLE `tabReno Order`
ADD INDEX `idx_reno_order_date_status`
  (transaction_date, status, docstatus, grand_total),
ALGORITHM=INPLACE, LOCK=NONE
```

- **transaction_date first** — matches the 12-month range (leftmost prefix).
- **status** — grouping / optional equality filter.
- **docstatus** — `docstatus < 2` without a table lookup.
- **grand_total** — covering for `SUM(grand_total)` (`Using index`).

### EXPLAIN after optimization

Same query on a temporary 20,000-row seed (dates spread over 24 months so the 12-month filter is selective). Seed was deleted afterwards.

| type | possible_keys | key | rows | Extra |
|---|---|---|---|---|
| range | idx_reno_order_date_status | idx_reno_order_date_status | 10102 | Using where; **Using index**; Using temporary; Using filesort |

Range scan on the covering index (~half the rows, as expected). `Using index` means InnoDB did not touch the clustered table for the aggregate.

### Why this index / when indexes help or hurt

- An index helps when the filter is **selective** and matches a **leftmost prefix** (range on date, then status). A covering index avoids reading the clustered PK for aggregates.
- An index hurts when the table is tiny (optimizer prefers a scan), the predicate is not selective (almost every row is in the last 12 months), or there are many writes — each INSERT/UPDATE of those columns maintains another B-tree.
- Do not index low-cardinality `status` alone. Do not wrap `transaction_date` in a function in WHERE.

### Safe production rollout

1. Backup. Prefer a replica or quiet window.
2. InnoDB `ALGORITHM=INPLACE, LOCK=NONE` so DML continues (MariaDB 10+). Fall back to a blocking `ADD INDEX` if INPLACE is refused.
3. For very large tables, use `pt-online-schema-change` or add the index on a replica, promote, then add on the old primary.
4. `bench --site <site> migrate` runs the patch once (Patch Log).
5. Validate with `EXPLAIN` and `SHOW INDEX FROM \`tabReno Order\``. Watch replication lag and `Handler_write`.

### Point 10 assumptions

- Value is `grand_total` (after discount). Cancelled (`docstatus = 2`) is excluded.
- Current site volume is small; EXPLAIN after the index was measured on a temporary 20k seed.

## Point 11 — Permissions

Server-side matrix. JavaScript only locks the form; it is not the security boundary.

| Role | Can view | Can change |
|---|---|---|
| Sales User | Own (`owner`) and assigned | Selling + installation on those rows |
| Sales Manager | Own + team (`Employee.reports_to`). No Employee record → all Reno Orders | Selling + installation |
| Site Supervisor | Assigned installation orders only | Installation remarks, status (workflow), photos |
| Site Supervisor must not | — | Customer, discount, selling price (`rate`), financial totals, item qty/code |
| Accounts User | All (read) | Close via workflow |
| System Manager | Unrestricted | Unrestricted |

Enforcement:

1. `permission_query_conditions` / `has_permission` — list and document access
2. **permlevel 1** on customer, discount, totals, item code/qty/rate/amount. Supervisor has **read** only at permlevel 1
3. `validate_site_supervisor_selling_fields` — compares the previous document and throws `PermissionError` if those fields changed. This catches API / `save()` even if the client sends a new rate

Desk JS `apply_supervisor_field_locks` is UX only.

### Point 11 assumptions

- A user who is both Site Supervisor and Sales User/Manager keeps selling write access.
- Team membership is HR Employee `reports_to` (same as Point 2).
- Mobile APIs from Point 6 only write installation fields; this validate is the backstop if someone posts a full document.

## Point 12 — Client-Side Development

All of this is UX. The server still validates status, roles, assignment and selling fields.

| Behaviour | What the form does |
|---|---|
| Conditional visibility | Installation section after submit / Ready+. CRM section after submit. Supervisor cannot add item rows |
| Dynamic filters | Address & contact limited to the customer. Warehouse & project limited to company. **Assigned To** uses `supervisor_user_query` (Site Supervisor users only) |
| Fetch linked info | Customer name/type intro. Item description, UOM and standard rate when `item_code` is chosen |
| Custom buttons | Create SO/DN/SI/MR/WO, Sync to CRM, View links |
| Status-based actions | **Mark as Installed** only when submitted, status is Ready for Installation, and the user is System Manager, Sales Manager, or the assigned Site Supervisor |
| Friendly messages | Installation date and qty on validate. Overdue / Ready / Installed headlines. Discount-above-threshold warning |

**Mark as Installed** calls `reno_order.mark_as_installed` → the same `update_installation_status` checks as the mobile API. Hiding the button is not enough; a crafted `frappe.call` still fails if the transition or role is wrong.

### Point 12 assumptions

- Workflow actions stay available on Desk. The custom button is the assignment’s explicit “Mark as Installed” control.
- Client math for totals is display-only. `validate()` on the server still overwrites them.

## Point 13 — HRMS Debugging (mid-year leave allocation)

A mid-year joiner (1 July) was getting a **prorated** allocation for event-based leave (Maternity / Paternity / Marriage). Annual Leave should be prorated. The event-based types should not.

### Root cause

HRMS `Leave Policy Assignment.get_new_leaves` (`hrms/hr/doctype/leave_policy_assignment/leave_policy_assignment.py`) has three branches:

1. Compensatory → allocate `0` (scheduler later)
2. Earned leave inside the period → `get_leaves_for_passed_period`
3. **Everything else** → `calculate_pro_rated_leaves(annual_allocation, date_of_joining, effective_from, effective_to)`

`calculate_pro_rated_leaves` returns the full entitlement only when `date_of_joining <= period_start`. Otherwise it scales by remaining days:

`leaves * (period_end - doj + 1) / (period_end - period_start + 1)`

Leave Type has flags for LWP, earned, compensatory, optional and PPL. It has **no** “event-based / fixed entitlement / do not prorate” flag. So Maternity, Paternity and Marriage sit in branch 3 and are treated like Annual Leave.

Example, calendar year 2026, join 1 July, policy 90 days Maternity:

- actual period = 184 days, complete period = 365
- HRMS allocates `90 * 184 / 365 ≈ 45` instead of `90`

Annual Leave of 12 days becoming ~6 is **correct**.

### Why configuration alone is not enough

You can keep event-based types off the Leave Policy and grant them by hand when the event happens. That is a process workaround, not a policy-assignment fix. The same Leave Policy is what the assignment asks to use.

There is no standard checkbox to skip proration, so this cannot be solved from Desk without a custom field.

### Fix (custom app, no HRMS core change)

1. Custom field **Leave Type.is_event_based** — “Event Based (Do Not Prorate)”.
2. `override_doctype_class` on Leave Policy Assignment → `reno_order.leave.RenoLeavePolicyAssignment`.
3. `get_new_leaves` returns the full policy entitlement when `is_event_based` is set; otherwise it calls HRMS.

Migrate seeds Annual Leave (not event-based, 12) and Maternity / Paternity / Marriage (event-based, 90 / 15 / 5).

### Config vs customisation

| Approach | Use when |
|---|---|
| Config / process | Event leave is granted only when the event occurs, not via Leave Policy Assignment |
| Custom field + class override (this fix) | Event leave sits on the same policy and must allocate in full on assignment |
| HRMS core patch | Avoid. Upstream may add a flag later; do not fork HRMS for this |

### Test cases

| Employee | Annual (12) | Maternity (90) | Paternity (15) | Marriage (5) |
|---|---|---|---|---|
| Joined 1 Jan 2026 | 12 | 90 | 15 | 5 |
| Joined 1 Jul 2026 (HRMS default / bug) | ~6 | ~45 | ~7.5 | ~2.5 |
| Joined 1 Jul 2026 (after fix) | ~6 | **90** | **15** | **5** |

Also covered: `frappe.new_doc("Leave Policy Assignment")` is the overridden class; `get_new_leaves` skips proration only when the flag is on.

```bash
bench --site savyant.localhost execute reno_order.demo.run_leave_allocation_debug
```

### Point 13 assumptions

- Entitlement days (12 / 90 / 15 / 5) are typical India figures; change them on the Leave Policy if HR policy differs.
- Event-based leave still has a validity window (`effective_from` / `effective_to`). Only the **quantity** is not prorated.
- Compensatory and earned-leave scheduler behaviour is unchanged.

## Point 14 — Debugging (Installed timeout still creates a document)

Reported: *“Sometimes when we mark an order as Installed, the page keeps loading and eventually shows an error. However, sometimes the downstream transaction is still created.”*

### How to investigate

| Tool | What you are looking for |
|---|---|
| Browser Network | `mark_as_installed` / `apply_workflow` pending, then **504 / 502 / Request Timeout**. Response body empty; status on the server may already be Installed |
| `logs/web.log` | gunicorn/werkzeug killed the *connection*, not necessarily the worker |
| `logs/worker.log` | Job `reno-installed-<name>` started after the browser already failed |
| Desk → **Error Log** | `Reno Order installed processing failed: RO-…` or `enqueue failed` |
| Desk → **RQ Job** | `job_id` `reno-installed-RO-…` — queued / started / finished / failed |
| SQL | `SELECT name, docstatus FROM `tabDelivery Note` WHERE reno_order='RO-…'` — more than one open DN is the duplicate |
| Reno Order fields | `status`, `processing_status`, `delivery_note` after the “failed” click |

```bash
bench --site savyant.localhost execute reno_order.debug.investigate_installed_timeout --kwargs "{'reno_order': 'RO-2026-00001'}"
bench --site savyant.localhost execute reno_order.demo.run_installed_timeout_debug
```

### Suspected root cause (this implementation)

Mark as Installed is a short HTTP call: workflow → `status = Installed` → `on_update` **enqueues** `process_installed_order` (`enqueue_after_commit`, `job_id=reno-installed-<name>`). The Delivery Note is created on the worker, not on the form save.

The reported symptom is still the classic **timeout-after-commit**:

1. The request commits Installed (and the enqueue) before nginx/gunicorn writes the response.
2. The proxy times out. Desk shows an error; `freeze` stays up until then.
3. The worker continues. The job (or a supervisor double-click / retry) creates the Delivery Note.
4. A second overlapping run used to be able to insert a **second** DN: both called `get_existing_delivery_note()` before either insert committed.

A second, slower variant is creating the DN **on the request thread** (do not do this). That makes the hang routine and still leaves a committed DN when the proxy gives up.

### Fix (duplicate-safe)

| Layer | Guard |
|---|---|
| HTTP | `update_installation_status` is idempotent. Retry after commit returns `already_installed` + current `delivery_note` |
| Queue | Same `job_id`, `deduplicate=True` |
| Job | `SELECT … FOR UPDATE` on the Reno Order, then re-read the DN link |
| Name | Auto DN is `RDN-<reno_order>`. A second insert hits `DuplicateEntryError` and reuses the row |
| Desk | Button ignores a second click while the first is in flight. On HTTP error, reload; if status is already Installed, show that — not a hard failure |

The request path still does **not** create the Delivery Note. Idempotency is what stops the retry from creating a second one.

### Point 14 assumptions

- Downstream document for Installed is the **draft Delivery Note** (stock only moves when a user submits it).
- A 504 on the browser is not rolled back by Frappe once `commit()` has run.
- Unique DN names apply to the background job only. A user-created DN from **Create Delivery Note** still uses the standard series and is found via `reno_order`.

## Point 15 — Testing

Automated tests live in `reno_order/reno_order/doctype/reno_order/test_reno_order.py` and `reno_order/test_leave_allocation.py`. They are `UnitTestCase` (not `IntegrationTestCase`) so they do not fight ERPNext fiscal-year fixtures.

```bash
bench --site savyant.localhost run-tests --app reno_order
```

### Required cases

| Assignment case | Test |
|---|---|
| Total calculation | `test_totals_are_recalculated_on_save`, `test_client_tampered_totals_are_ignored` |
| Discount authorization | `test_sales_user_cannot_submit_discount_above_threshold`, `test_sales_user_can_submit_discount_within_threshold`, `test_approver_can_submit_discount_above_threshold` |
| Invalid installation date | `test_installation_date_before_order_date_is_rejected` |
| Unauthorized API request | `test_api_requires_authentication`, `test_api_rejects_unassigned_supervisor` |
| Permission restrictions | `test_sales_user_sees_own_and_assigned_orders_only`, `test_site_supervisor_sees_assigned_orders_only`, `test_site_supervisor_cannot_change_selling_fields` |
| Sales Order creation | `test_sales_order_is_created_from_reno_order` |
| Duplicate Sales Order prevention | `test_duplicate_sales_order_is_blocked` |
| Installed status processing | `test_mark_as_installed_from_ready`, `test_installed_job_creates_only_one_delivery_note_on_retry`, `test_installed_processing_is_idempotent_without_submitted_so` |
| Patch behavior | `test_order_type_patch_backfills_only_blanks` |

### Extra coverage

Invalid qty, workflow jumps, overdue flag, DN/SI/WO/MR duplicates, CRM enqueue/retry/webhook, report + index, leave proration (Point 13), timeout-and-retry Installed (Point 14).

CRM tests **patch** `frappe.enqueue` and then call `run_crm_sync_job` so they do not need a live Redis worker. Discount tests run as a real Sales User; Administrator is treated as the approver (`System Manager`).

### Point 15 assumptions

- Tests create their own Customer / Item / users (`Reno Test Customer`, `reno.sales@example.com`). They do not depend on Desk data.
- `frappe.set_user` is always restored to Administrator in `finally`.
- Threshold used in discount tests is 10%, matching Reno Settings default.

## Point 16 — Git & code quality

This custom app is its own Git repository (`apps/reno_order`). The bench, `sites/`, and ERPNext/HRMS core are not part of it.

### Required files

| File | Role |
|---|---|
| `README.md` | Design notes, assumptions, and how to run each point |
| `CHANGELOG.md` | Keep a Changelog, starting at 1.0.0 |
| `.gitignore` | Secrets, backups, site data, bytecode, IDE files |

### Must not be in the repository

- Passwords, API tokens, or `site_config.json`
- SQL dumps, `backups/`, or production `sites/`
- `__pycache__/`, `node_modules/`, coverage, logs

CRM credentials are **Password** fields on Reno Settings. `after_install` generates them on the site with `frappe.generate_hash`. Nothing in this tree is a live secret.

### Layout (not one Python file)

| Module | Responsibility |
|---|---|
| `reno_order/doctype/reno_order/` | DocType, form JS, tests |
| `api.py` | Mobile whitelist methods |
| `permissions.py` | Row filters and selling-field lock |
| `workflow.py` / `tasks.py` | Workflow seed and scheduler |
| `crm.py` / `crm_mock.py` | Outbound CRM + mock transport |
| `integrations.py` | Downstream `reno_order` link |
| `manufacturing.py` / `buying.py` | Scenario masters |
| `leave.py` | Event-based leave override |
| `patches/v1_0/` | Backfill and index |
| `reporting.py` + Script Report | Monthly value |

Ruff is configured in `pyproject.toml` (`line-length` 110). Version is `reno_order.__version__` (`1.0.0`).

### Git practice

```bash
cd apps/reno_order
git log --oneline
```

Commit messages describe **why**. Do not commit Desk exports that include Password values. Do not fork `frappe`, `erpnext`, or `hrms` in this repo.

### Point 16 assumptions

- Reviewers clone **this app**, then `bench get-app` / `install-app` onto an existing v16 bench.
- A public remote is optional for this point; Point 17 covers CI promotion.

## Point 17 — CI/CD

GitHub Actions workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

**Code push → Build / Setup → Test → Result**

| Job | What it does |
|---|---|
| **Build / Setup** | Checkout, Python 3.14, `compileall`, reject merge-conflict markers, `ruff check` |
| **Test** | Needs Build. MariaDB 11.8 + Redis 7. `bench init` on `version-16`, install ERPNext, HRMS, then Reno Order. `bench --site test_site run-tests --app reno_order` |
| **Result** | GitHub check on the commit / PR. Red blocks merge if you protect `main` |

CI site passwords (`admin` / MariaDB `root`) exist only in the Actions job. They are not committed. `.github/helper/install.sh` creates `test_site` at runtime.

Push the `reno_order` repo to GitHub and the workflow runs on `main` and on every pull request.

### Development → Staging → Production

Do not deploy `main` by SHA to production. Promote a **git tag**.

```
feature branch ──PR + CI──► main ──tag vX.Y.Z──► staging ──same tag──► production
```

1. **Development** — branch off `main`. CI on the PR is the only gate.
2. **Staging** — when `main` is green, `git tag vX.Y.Z`. On the staging bench:
   - `bench backup --with-files` (take this *before* migrate)
   - `bench get-app reno_order --branch vX.Y.Z` (or `git fetch && git checkout vX.Y.Z` in `apps/reno_order`)
   - `bench --site <staging> migrate`
   - `bench build --app reno_order` if assets changed
   - `bench --site <staging> restart` / reload workers
   - Smoke: create a Reno Order, submit, Mark as Installed, open the monthly report
3. **Production** — only after staging is good. Same tag, same commands, same pre-migrate backup. Enable CRM last (`Reno Settings`), never in the migrate step.

Frappe Cloud: deploy that release to the staging site group, then promote the same release. Self-hosted: Supervisor/systemd restart after migrate; drain `bench worker` / `bench schedule` if a long CRM job is in flight.

### Rollback if production fails

| Failure | Action |
|---|---|
| App code wrong, migrate succeeded (idempotent patches) | `git checkout v<previous>` in `apps/reno_order`, `bench restart`. Our `order_type` backfill and index patches are safe to re-run |
| Migrate failed or schema half-applied | **Restore the pre-deploy backup**: `bench --site <prod> restore <backup>` then `bench migrate` on the previous tag. Do not hand-edit MariaDB |
| Workers / 502 after a good migrate | `bench restart`, check `supervisorctl status`, Redis, disk. Not an app rollback |
| Bad CRM behaviour | Turn off **Enable CRM Sync** in Reno Settings. No redeploy |

Never `bench migrate` forward on production without a backup from the same minute. Never force-push over a released tag. If a patch is not reversible, rollback is always **restore backup + previous tag**, not “migrate down”.

### Point 17 assumptions

- Frappe / ERPNext / HRMS stay on `version-16` in CI (`FRAPPE_BRANCH` etc. can override).
- Protect `main`: required check = this workflow. No direct pushes of untested code.
- Staging and production are separate sites (or site groups), never the same database.

## Point 18 — Production & server knowledge

This bench is self-hosted Honcho (`Procfile`): web on **8011** (`--site savyant.localhost`), Redis cache/queue, `bench schedule`, and one `bench worker`. Production would use Nginx + Gunicorn + Supervisor instead of `bench serve`.

### Frappe Cloud

| Topic | What it is |
|---|---|
| **Application deployment** | App is linked from Git. You pick a branch or tag; Cloud builds assets, installs the app on the site group, runs `migrate`, and restarts workers. Same idea as Point 17: deploy a **release tag**, not a random SHA. No SSH required for a normal release. |
| **Backups** | Cloud takes scheduled off-site backups (database + files). Restore a site from the dashboard to a point in time. Still take an extra `bench backup` equivalent before a risky migrate if you manage the site yourself. |
| **Logs** | Desk **Error Log** / **RQ Job** for app exceptions. Cloud “Logs” shows web, worker, and scheduler streams (the hosted `logs/web.log` and `logs/worker.log`). |
| **Scheduler / workers** | Cloud runs RQ workers (`short`, `default`, `long`) and `bench schedule`. Reno CRM uses **long**; Installed processing uses **default**. If jobs sit in “Queued”, check the site’s worker count and Redis, not the web process. |
| **Site configuration** | `site_config.json` + `common_site_config.json` are edited in the Cloud UI (encryption, domains, limits). Password fields stay on the site. Never put `db_password` or API tokens in the app repo. |

### Self-hosted — role of each process

| Process | Role |
|---|---|
| **Nginx** | TLS terminator and reverse proxy. Routes `Host` to the site (`savyant.localhost`). Serves `/assets`. A wrong Host header hits the wrong site or none (this assignment’s `127.0.0.1` vs site-name issue). |
| **Gunicorn** | WSGI for Desk and the REST API. `bench serve` is only for development. Timeouts here become the Point 14 “page keeps loading” 504. |
| **Supervisor / process manager** | Keeps Nginx, Gunicorn, Redis, `bench worker`, `bench schedule`, and Socket.IO running and restarts them. `bench restart` talks to this. Honcho/`Procfile` is the same idea on this laptop. |
| **Redis** | `redis_cache` (DocType cache) and `redis_queue` (RQ). If Redis is down: login loops, jobs never start, Socket.IO dies. |
| **MariaDB** | One database per site. Source of truth for Reno Order, SO/DN/SI, and patches. Back this up before every production migrate. |
| **Workers** | RQ processes. `process_installed_order` and CRM sync **must not** run on the web request. Scale `long` if CRM backs up. |
| **Scheduler** | `bench schedule` ticks Frappe’s clock and enqueues `hooks.py` jobs (daily `flag_overdue_installations`). It does not execute the job body; a worker does. |

### Troubleshoot

| Symptom | First checks |
|---|---|
| **502 Bad Gateway** | Nginx can reach Gunicorn (`supervisorctl status`, `logs/web.error.log`). Process dead, socket missing, or all workers busy. `bench restart`. Disk full also shows as 502. |
| **Worker queue backlog** | Desk → RQ Job, or `bench --site savyant.localhost doctor` / Redis `llen`. Add a `long` worker for CRM. Stuck job: failed job in Error Log, then `bench worker --queue long`. Deduplicate (`reno-crm-sync-*`, `reno-installed-*`) so retries do not pile up. |
| **Scheduler not running** | `supervisorctl status` / Procfile `schedule` line. `bench --site savyant.localhost scheduler status` then `enable` / `resume`. Confirm `pause_scheduler` is not set in site config. Daily overdue flags will stop if this is down. |
| **High CPU** | `top`: Gunicorn vs worker vs `mysqld`. One slow report without the date/status index (Point 10). A CRM job in a retry loop. `bench disable-scheduler` only as a last resort. |
| **Slow MariaDB** | `SHOW FULL PROCESSLIST`, `EXPLAIN` the monthly report. Confirm `idx_reno_order_date_status` exists. Missing index or `ignore index` looks like `type: ALL`. Add the covering index; do not raise `innodb_buffer_pool` as the first fix. |
| **Disk full** | `df -h`, `sites/<site>/private/files`, `logs/`, old backups. 502 and MariaDB crashes follow. Truncate logs, move backups off the box, then restart. Site photos are **private** files — they count. |
| **Failed migration** | Read the traceback (`bench migrate` output / Error Log). Our patches are idempotent (batched `order_type` backfill, `CREATE INDEX IF NOT EXISTS`). If a core patch fails: **restore the pre-migrate backup**, do not `--skip-failing`. Fix the app, then migrate again. |

### Point 18 assumptions

- Production uses Supervisor + Nginx + Gunicorn. This assignment laptop uses Honcho + `bench serve` on 8011.
- Site name is `savyant.localhost`. Browsing via `127.0.0.1` without a Host alias will not resolve the site.
- Rollback is still “restore backup + previous app tag” (Point 17). There is no migrate-down.

### Configuration

Desk → **Reno Settings**

- Discount Approval Threshold (%)
- Discount Approver Role
- Enable CRM Sync
- CRM Base URL (`mock://reno-crm` or a real URL)
- CRM Timeout / Max Retries
- CRM API Token (Password)
- CRM Webhook Secret (Password)

No credentials are stored in code.

### Installation

```bash
bench get-app ./apps/reno_order   # or copy this app into bench/apps
bench --site <site> install-app reno_order
bench --site <site> migrate
```

On this bench:

```bash
bench --site savyant.localhost install-app reno_order
```

### Tests

```bash
bench --site savyant.localhost run-tests --app reno_order
```

52 unit tests. See Point 15 for the required-case map.
