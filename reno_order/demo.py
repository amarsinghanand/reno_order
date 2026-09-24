import frappe
from frappe.utils import flt, today

from reno_order.reno_order.doctype.reno_order.test_reno_order import make_reno_order
from reno_order.utils import get_warehouse


def run_order_to_cash(qty=2, rate=150):
	"""Create one live Reno Order → SO → DN → SI chain for the Point 3 demo."""
	warehouse = get_warehouse()
	_ensure_stock("Reno Test Item", warehouse, qty)

	reno = make_reno_order(qty=qty, rate=rate, save=True)
	reno.submit()

	so_name = reno.create_sales_order()
	so = frappe.get_doc("Sales Order", so_name)
	so.submit()
	reserved_after_so = _bin("Reno Test Item", warehouse)

	dn_name = reno.create_delivery_note()
	dn = frappe.get_doc("Delivery Note", dn_name)
	dn.submit()
	stock_after_dn = _bin("Reno Test Item", warehouse)

	si_name = reno.create_sales_invoice()
	si = frappe.get_doc("Sales Invoice", si_name)
	si.submit()
	gl_count = frappe.db.count("GL Entry", {"voucher_no": si_name, "is_cancelled": 0})
	sle_count = frappe.db.count("Stock Ledger Entry", {"voucher_no": dn_name, "is_cancelled": 0})

	reno.reload()
	return {
		"reno_order": reno.name,
		"sales_order": so_name,
		"delivery_note": dn_name,
		"sales_invoice": si_name,
		"warehouse": warehouse,
		"reserved_after_so": reserved_after_so,
		"stock_after_dn": stock_after_dn,
		"sle_on_dn": sle_count,
		"gl_on_si": gl_count,
		"grand_total": flt(reno.grand_total),
	}


def run_manufacturing(qty=1):
	"""Create Kitchen Cabinet Reno Order → Work Order → Job Cards → Stock Entries."""
	from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
	from frappe.utils import add_to_date, now_datetime

	from reno_order.manufacturing import FG_ITEM, RM_ITEMS, ensure_manufacturing_masters

	warehouses = ensure_manufacturing_masters()
	stores = warehouses["stores"]
	for item_code, rm_qty, _rate in RM_ITEMS:
		_ensure_stock(item_code, stores, flt(rm_qty) * qty + 2)

	reno = make_reno_order(qty=qty, rate=800, item_code=FG_ITEM, save=True)
	reno.submit()
	work_orders = reno.create_work_orders()
	wo_name = work_orders[0]
	wo = frappe.get_doc("Work Order", wo_name)
	wo.submit()

	job_cards = frappe.get_all(
		"Job Card",
		{"work_order": wo_name},
		pluck="name",
		order_by="sequence_id asc, creation asc",
	)
	for name in job_cards:
		job_card = frappe.get_doc("Job Card", name)
		if job_card.docstatus:
			continue
		if not job_card.time_logs:
			started = now_datetime()
			job_card.append(
				"time_logs",
				{
					"from_time": started,
					"to_time": add_to_date(started, minutes=30),
					"time_in_mins": 30,
					"completed_qty": job_card.for_quantity,
				},
			)
		else:
			job_card.time_logs[0].completed_qty = job_card.for_quantity
		job_card.submit()

	transfer = frappe.get_doc(make_stock_entry(wo_name, "Material Transfer for Manufacture"))
	if frappe.get_meta("Stock Entry").has_field("reno_order"):
		transfer.reno_order = reno.name
	_set_stock_entry_accounts(transfer, reno.company)
	transfer.insert()
	transfer.submit()

	manufacture = frappe.get_doc(make_stock_entry(wo_name, "Manufacture"))
	if frappe.get_meta("Stock Entry").has_field("reno_order"):
		manufacture.reno_order = reno.name
	_set_stock_entry_accounts(manufacture, reno.company)
	manufacture.insert()
	manufacture.submit()

	wo.reload()
	return {
		"reno_order": reno.name,
		"work_order": wo_name,
		"job_cards": job_cards,
		"material_transfer": transfer.name,
		"manufacture": manufacture.name,
		"produced_qty": wo.produced_qty,
		"fg_stock": _bin(FG_ITEM, warehouses["fg"]),
	}


def run_buying(qty=2):
	"""Create Hardware Reno Order → MR → RFQ → SQ → PO → PR → PI."""
	from erpnext.buying.doctype.request_for_quotation.request_for_quotation import (
		make_supplier_quotation_from_rfq,
	)
	from erpnext.buying.doctype.supplier_quotation.supplier_quotation import make_purchase_order
	from erpnext.stock.doctype.material_request.material_request import make_request_for_quotation
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
	from erpnext.stock.utils import get_stock_balance

	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt

	from reno_order.buying import HARDWARE_ITEM, SUPPLIER, ensure_buying_masters

	ensure_buying_masters()
	warehouse = get_warehouse()
	on_hand = flt(get_stock_balance(HARDWARE_ITEM, warehouse, today()))
	order_qty = on_hand + qty

	reno = make_reno_order(qty=order_qty, rate=30, item_code=HARDWARE_ITEM, save=True)
	reno.submit()
	mr_name = reno.create_material_request()
	mr = frappe.get_doc("Material Request", mr_name)
	mr.submit()

	rfq = make_request_for_quotation(mr_name)
	rfq.reno_order = reno.name
	rfq.message_for_supplier = "Please quote for kitchen hardware required for this Reno Order."
	if not rfq.suppliers:
		rfq.append("suppliers", {"supplier": SUPPLIER, "send_email": 0})
	rfq.insert()
	rfq.submit()

	sq = make_supplier_quotation_from_rfq(rfq.name, for_supplier=SUPPLIER)
	sq.reno_order = reno.name
	for row in sq.items:
		row.rate = row.rate or 30
	sq.insert()
	sq.submit()

	po = make_purchase_order(sq.name)
	po.reno_order = reno.name
	po.insert()
	po.submit()
	stock_before_pr = _bin(HARDWARE_ITEM, warehouse)

	pr = frappe.get_doc(make_purchase_receipt(po.name))
	pr.reno_order = reno.name
	pr.insert()
	pr.submit()
	stock_after_pr = _bin(HARDWARE_ITEM, warehouse)

	pi = frappe.get_doc(make_purchase_invoice(pr.name))
	pi.reno_order = reno.name
	pi.insert()
	pi.submit()

	return {
		"reno_order": reno.name,
		"material_request": mr_name,
		"request_for_quotation": rfq.name,
		"supplier_quotation": sq.name,
		"purchase_order": po.name,
		"purchase_receipt": pr.name,
		"purchase_invoice": pi.name,
		"stock_before_pr": stock_before_pr,
		"stock_after_pr": stock_after_pr,
		"sle_on_pr": frappe.db.count("Stock Ledger Entry", {"voucher_no": pr.name, "is_cancelled": 0}),
		"gl_on_pr": frappe.db.count("GL Entry", {"voucher_no": pr.name, "is_cancelled": 0}),
		"gl_on_pi": frappe.db.count("GL Entry", {"voucher_no": pi.name, "is_cancelled": 0}),
	}


def run_installation_api():
	"""Create a ready order and exercise the Point 6 Site Supervisor APIs."""
	from reno_order.api import add_installation_remarks, attach_site_photo, get_installation, update_installation_status
	from reno_order.reno_order.doctype.reno_order.test_reno_order import ensure_test_supervisor

	supervisor = ensure_test_supervisor()
	reno = make_reno_order(qty=1, rate=150, save=True)
	reno.db_set("assigned_to", supervisor)
	reno.submit()
	frappe.db.set_value(
		"Reno Order",
		reno.name,
		"status",
		"Ready for Installation",
		update_modified=False,
	)

	frappe.set_user(supervisor)
	try:
		status = update_installation_status(reno.name, "Installed")
		remarks = add_installation_remarks(reno.name, "Installation completed successfully.")
		frappe.form_dict["filedata"] = b"\x89PNG\r\n\x1a\n"
		try:
			photo = attach_site_photo(reno.name, filename="site.png")
		finally:
			frappe.form_dict.pop("filedata", None)
		info = get_installation(reno.name)
	finally:
		frappe.set_user("Administrator")

	return {
		"reno_order": reno.name,
		"supervisor": supervisor,
		"status": status["status"],
		"remarks": remarks["remarks"],
		"photo_url": photo["file_url"],
		"photos": len(info["photos"]),
	}


def run_crm_sync():
	"""Submit one Reno Order and sync it to the mock CRM for the Point 7 demo."""
	from reno_order.crm import crm_job_id, ensure_crm_defaults, run_crm_sync_job

	ensure_crm_defaults()
	settings = frappe.get_single("Reno Settings")
	if not settings.crm_enabled:
		settings.crm_enabled = 1
		settings.save(ignore_permissions=True)
		frappe.clear_cache()

	reno = make_reno_order(qty=1, rate=175, save=True)
	reno.submit()
	reno.reload()
	queued_status = reno.crm_sync_status
	# Tests and this demo do not wait for a worker. Run the same job the worker would run.
	if reno.crm_sync_status != "Synced":
		run_crm_sync_job(reno.name)
		reno.reload()

	logs = frappe.get_all(
		"Integration Request",
		{"reference_doctype": "Reno Order", "reference_docname": reno.name},
		pluck="name",
		order_by="creation desc",
		limit=1,
	)
	log_name = logs[0] if logs else None
	log = frappe.get_doc("Integration Request", log_name) if log_name else None

	from reno_order.crm import receive_crm_webhook, webhook_signature

	body = frappe.as_json(
		{"reno_order": reno.name, "crm_id": reno.crm_id, "comment": "CRM acknowledged the order."}
	)
	frappe.local.request = type(
		"Req",
		(),
		{"headers": {"X-Reno-Signature": webhook_signature(body)}, "get_data": lambda self, as_text=True: body},
	)()
	try:
		webhook = receive_crm_webhook()
	finally:
		frappe.local.request = None

	return {
		"reno_order": reno.name,
		"status_after_submit": queued_status,
		"crm_id": reno.crm_id,
		"crm_sync_status": reno.crm_sync_status,
		"job_id": crm_job_id(reno.name),
		"queue": "long",
		"integration_request": log_name,
		"request_headers": log.request_headers if log else None,
		"crm_base_url": settings.crm_base_url,
		"webhook": webhook,
	}


def run_monthly_value_report(seed_rows=20000):
	"""Seed volume, capture EXPLAIN before/after the covering index, then drop the seed."""
	from reno_order.reporting import (
		INDEX_NAME,
		ensure_date_status_index,
		explain_monthly_value,
		get_monthly_value_data,
	)
	from reno_order.reno_order.doctype.reno_order.test_reno_order import ensure_masters
	from reno_order.utils import get_company

	ensure_masters()
	ensure_date_status_index()
	seeded = _seed_perf_rows(seed_rows, get_company())
	try:
		before = explain_monthly_value(ignore_index=True)
		after = explain_monthly_value(ignore_index=False)
		data = get_monthly_value_data()
	finally:
		_delete_perf_rows()

	return {
		"index": INDEX_NAME,
		"seeded": seeded,
		"explain_before": before,
		"explain_after": after,
		"rows": len(data),
		"sample": data[:8],
	}


def _seed_perf_rows(target, company):
	from frappe.utils import add_months, now_datetime, today

	prefix = "RO-PERF-"
	existing = frappe.db.sql(
		"SELECT count(*) FROM `tabReno Order` WHERE name LIKE %s", f"{prefix}%"
	)[0][0]
	needed = max(0, int(target) - int(existing))
	if not needed:
		return existing

	statuses = ("Draft", "Confirmed", "In Production", "Installed", "Closed")
	now = now_datetime()
	batch = []
	created = 0
	start = int(existing) + 1
	for i in range(start, start + needed):
		# Spread over 24 months so the 12-month filter is selective.
		txn = add_months(today(), -(i % 24))
		status = statuses[i % len(statuses)]
		batch.append(
			(
				f"{prefix}{i:06d}",
				now,
				now,
				"Administrator",
				"Administrator",
				0,
				0,
				"RO-.YYYY.-",
				"Reno Test Customer",
				"Reno Test Customer",
				company,
				txn,
				"Standard",
				status,
				0,
				100,
				0,
				100,
				0,
				"Not Synced",
				"Pending",
			)
		)
		if len(batch) >= 500:
			_insert_perf_batch(batch)
			created += len(batch)
			batch = []
	if batch:
		_insert_perf_batch(batch)
		created += len(batch)
	if not frappe.flags.in_test:
		frappe.db.commit()
	return existing + created


def _insert_perf_batch(batch):
	placeholders = ",".join(["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(batch))
	values = [value for row in batch for value in row]
	frappe.db.sql(
		f"""
		INSERT INTO `tabReno Order` (
			name, creation, modified, owner, modified_by, docstatus, idx,
			naming_series, customer, customer_name, company, transaction_date,
			order_type, status, discount_percentage, total_amount, discount_amount,
			grand_total, is_overdue, crm_sync_status, processing_status
		) VALUES {placeholders}
		""",
		values,
	)


def _delete_perf_rows():
	frappe.db.sql("DELETE FROM `tabReno Order` WHERE name LIKE %s", "RO-PERF-%")
	if not frappe.flags.in_test:
		frappe.db.commit()


def run_mark_installed_button():
	"""Create a Ready order and run the same server method the Desk button calls."""
	from unittest.mock import patch

	from reno_order.reno_order.doctype.reno_order.test_reno_order import ensure_test_supervisor

	supervisor = ensure_test_supervisor()
	reno = make_reno_order(qty=1, rate=110, save=True)
	reno.db_set("assigned_to", supervisor)
	reno.submit()
	frappe.db.set_value("Reno Order", reno.name, "status", "Ready for Installation", update_modified=False)
	reno.reload()
	with patch("reno_order.crm.frappe.enqueue"):
		result = reno.mark_as_installed()
	reno.reload()
	return {
		"reno_order": reno.name,
		"assigned_to": supervisor,
		"status_before": "Ready for Installation",
		"status_after": reno.status,
		"api_result": result,
	}


def run_permission_matrix():
	"""Exercise Point 11 list and selling-field rules with the test users."""
	from reno_order.reno_order.doctype.reno_order.test_reno_order import (
		ensure_test_sales_user,
		ensure_test_supervisor,
	)

	sales = ensure_test_sales_user()
	supervisor = ensure_test_supervisor()
	hidden = make_reno_order(qty=1, rate=80, save=True)
	assigned = make_reno_order(qty=1, rate=80, save=True)
	assigned.db_set("assigned_to", supervisor)
	assigned.submit()
	frappe.db.set_value("Reno Order", hidden.name, "owner", sales, update_modified=False)

	frappe.set_user(sales)
	try:
		sales_names = {row.name for row in frappe.get_list("Reno Order", fields=["name"], limit_page_length=500)}
	finally:
		frappe.set_user("Administrator")

	frappe.set_user(supervisor)
	try:
		supervisor_names = {
			row.name for row in frappe.get_list("Reno Order", fields=["name"], limit_page_length=500)
		}
		blocked = False
		doc = frappe.get_doc("Reno Order", assigned.name)
		doc.discount_percentage = 25
		try:
			doc.save()
		except frappe.PermissionError:
			blocked = True
		doc.reload()
		if flt(doc.discount_percentage) != 25:
			blocked = True
	finally:
		frappe.set_user("Administrator")

	return {
		"sales_user": sales,
		"supervisor": supervisor,
		"sales_sees_own": hidden.name in sales_names,
		"sales_sees_supervisor_order": assigned.name in sales_names,
		"supervisor_sees_assigned": assigned.name in supervisor_names,
		"supervisor_sees_sales_order": hidden.name in supervisor_names,
		"supervisor_discount_blocked": blocked,
		"assigned_order": assigned.name,
	}


def run_order_type_backfill():
	"""Create blank / Custom rows and run the Point 9 Order Type patch."""
	from reno_order.patches.v1_0.backfill_reno_order_type import backfill_order_type, count_blank_order_types

	blank = make_reno_order(qty=1, rate=90, save=True)
	custom = make_reno_order(qty=1, rate=90, save=True)
	frappe.db.set_value("Reno Order", blank.name, "order_type", "", update_modified=False)
	frappe.db.set_value("Reno Order", custom.name, "order_type", "Custom", update_modified=False)
	blank_before = count_blank_order_types()
	result = backfill_order_type(batch_size=500)
	return {
		"blank_order": blank.name,
		"custom_order": custom.name,
		"blank_before": blank_before,
		"updated": result["updated"],
		"remaining": result["remaining"],
		"blank_after": frappe.db.get_value("Reno Order", blank.name, "order_type"),
		"custom_after": frappe.db.get_value("Reno Order", custom.name, "order_type"),
		"second_run": backfill_order_type(batch_size=500),
	}


def prepare_supervisor_ready_order():
	"""Create a submitted Reno Order assigned to the Site Supervisor, ready to install."""
	from reno_order.reno_order.doctype.reno_order.test_reno_order import ensure_test_supervisor

	supervisor = ensure_test_supervisor()
	reno = make_reno_order(qty=1, rate=120, save=True)
	reno.db_set("assigned_to", supervisor)
	reno.submit()
	frappe.db.set_value(
		"Reno Order",
		reno.name,
		"status",
		"Ready for Installation",
		update_modified=False,
	)
	return {"reno_order": reno.name, "supervisor": supervisor, "status": "Ready for Installation"}


def _set_stock_entry_accounts(stock_entry, company):
	expense_account = frappe.get_cached_value("Company", company, "default_expense_account")
	cost_center = frappe.get_cached_value("Company", company, "cost_center")
	for row in stock_entry.items:
		if expense_account and not row.expense_account:
			row.expense_account = expense_account
		if cost_center and not row.cost_center:
			row.cost_center = cost_center
	for row in stock_entry.get("additional_costs") or []:
		if expense_account and not row.expense_account:
			row.expense_account = expense_account


def _ensure_stock(item_code, warehouse, qty):
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
	from erpnext.stock.utils import get_stock_balance

	balance = flt(get_stock_balance(item_code, warehouse, today()))
	needed = flt(qty) - balance
	if needed > 0:
		make_stock_entry(
			item_code=item_code,
			qty=needed + 5,
			to_warehouse=warehouse,
			purpose="Material Receipt",
			rate=50,
		)


def run_installed_timeout_debug():
	"""Point 14: timeout-after-commit + retry must not create a second Delivery Note."""
	from unittest.mock import patch

	from reno_order.debug import investigate_installed_timeout
	from reno_order.reno_order.doctype.reno_order.reno_order import (
		create_downstream_delivery_note,
		installed_delivery_note_name,
	)

	reno = make_reno_order(qty=1, rate=150, save=True)
	reno.submit()
	so_name = reno.create_sales_order()
	frappe.db.set_value("Sales Order", so_name, "docstatus", 1, update_modified=False)
	frappe.db.set_value("Reno Order", reno.name, "status", "Ready for Installation", update_modified=False)
	reno.reload()

	with patch("reno_order.crm.frappe.enqueue"):
		first_click = reno.mark_as_installed()
		reno.reload()
		# Browser timed out; supervisor clicks again.
		retry_click = reno.mark_as_installed()

	first_dn = create_downstream_delivery_note(reno.name)
	retry_dn = create_downstream_delivery_note(reno.name)
	reno.reload()

	return {
		"reno_order": reno.name,
		"sales_order": so_name,
		"first_click": first_click,
		"retry_click": retry_click,
		"auto_dn_name": installed_delivery_note_name(reno.name),
		"first_job": first_dn,
		"retry_job": retry_dn,
		"delivery_note_count": frappe.db.count("Delivery Note", {"reno_order": reno.name}),
		"investigation": investigate_installed_timeout(reno.name),
	}


def _bin(item_code, warehouse):
	return frappe.db.get_value(
		"Bin",
		{"item_code": item_code, "warehouse": warehouse},
		["actual_qty", "reserved_qty"],
		as_dict=True,
	) or {}


def run_leave_allocation_debug():
	"""Point 13: mid-year joiner vs HRMS default proration."""
	from frappe.utils import getdate
	from hrms.hr.doctype.leave_policy_assignment.leave_policy_assignment import (
		calculate_pro_rated_leaves,
	)

	from reno_order.install import ensure_custom_fields
	from reno_order.leave import (
		ANNUAL_ALLOCATION,
		ANNUAL_LEAVE,
		EVENT_LEAVE_TYPES,
		ensure_leave_types,
		leaves_to_allocate,
	)

	ensure_custom_fields()
	ensure_leave_types()

	period_start = getdate("2026-01-01")
	period_end = getdate("2026-12-31")
	doj = getdate("2026-07-01")

	rows = []
	for name, entitlement in [(ANNUAL_LEAVE, ANNUAL_ALLOCATION), *EVENT_LEAVE_TYPES.items()]:
		hrms_default = calculate_pro_rated_leaves(
			entitlement, doj, period_start, period_end, is_earned_leave=False
		)
		allocated = leaves_to_allocate(entitlement, name, doj, period_start, period_end)
		rows.append(
			{
				"leave_type": name,
				"policy_entitlement": entitlement,
				"hrms_default": hrms_default,
				"after_fix": allocated,
				"correct": allocated == entitlement if name != ANNUAL_LEAVE else allocated < entitlement,
			}
		)

	return {
		"employee_doj": str(doj),
		"leave_period": f"{period_start} to {period_end}",
		"allocations": rows,
		"live": _try_live_leave_assignment(period_start, period_end, doj),
	}


def _try_live_leave_assignment(period_start, period_end, doj):
	from reno_order.leave import ANNUAL_ALLOCATION, ANNUAL_LEAVE, EVENT_LEAVE_TYPES
	from reno_order.utils import get_company

	company = get_company()
	if not company or not frappe.db.exists("DocType", "Leave Policy Assignment"):
		return {"created": False, "reason": "HRMS or company missing"}

	gender = frappe.db.get_value("Gender", {"name": ("in", ("Male", "Female"))}, "name") or "Male"
	if not frappe.db.exists("Gender", gender):
		frappe.get_doc({"doctype": "Gender", "gender": gender}).insert(ignore_permissions=True)

	employee_name = frappe.db.get_value(
		"Employee", {"employee_name": "Reno Mid Year Joiner", "company": company}, "name"
	)
	if not employee_name:
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": "Reno",
				"last_name": "Mid Year Joiner",
				"company": company,
				"gender": gender,
				"date_of_birth": "1990-06-15",
				"date_of_joining": doj,
				"status": "Active",
			}
		)
		employee.insert(ignore_permissions=True)
		employee_name = employee.name
	else:
		frappe.db.set_value("Employee", employee_name, "date_of_joining", doj)

	period = frappe.db.get_value(
		"Leave Period",
		{"company": company, "from_date": period_start, "to_date": period_end},
		"name",
	)
	if not period:
		period = frappe.get_doc(
			{
				"doctype": "Leave Period",
				"company": company,
				"from_date": period_start,
				"to_date": period_end,
				"is_active": 1,
			}
		).insert(ignore_permissions=True).name

	policy_title = "Reno Kitchen Staff Leave Policy"
	policy_name = frappe.db.get_value("Leave Policy", {"title": policy_title}, "name")
	if not policy_name:
		details = [{"leave_type": ANNUAL_LEAVE, "annual_allocation": ANNUAL_ALLOCATION}]
		details.extend(
			{"leave_type": name, "annual_allocation": days} for name, days in EVENT_LEAVE_TYPES.items()
		)
		policy = frappe.get_doc(
			{"doctype": "Leave Policy", "title": policy_title, "leave_policy_details": details}
		)
		policy.insert(ignore_permissions=True)
		policy.submit()
		policy_name = policy.name

	existing = frappe.db.get_value(
		"Leave Policy Assignment",
		{"employee": employee_name, "leave_policy": policy_name, "docstatus": 1},
		"name",
	)
	if existing:
		assignment_name = existing
	else:
		assignment = frappe.get_doc(
			{
				"doctype": "Leave Policy Assignment",
				"employee": employee_name,
				"assignment_based_on": "Leave Period",
				"leave_period": period,
				"leave_policy": policy_name,
				"carry_forward": 0,
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()
		assignment_name = assignment.name

	allocations = frappe.get_all(
		"Leave Allocation",
		filters={"leave_policy_assignment": assignment_name, "docstatus": 1},
		fields=["leave_type", "new_leaves_allocated", "from_date", "to_date", "name"],
	)
	return {
		"created": True,
		"employee": employee_name,
		"leave_period": period,
		"leave_policy": policy_name,
		"assignment": assignment_name,
		"allocations": allocations,
	}
