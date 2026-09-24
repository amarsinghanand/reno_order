import frappe

from reno_order.reno_order.doctype.reno_order.reno_order import installed_delivery_note_name


def investigate_installed_timeout(reno_order=None):
	"""What to inspect when Mark as Installed hangs, then errors, but a DN exists.

	Call from bench: `bench --site savyant.localhost execute \\
	    reno_order.debug.investigate_installed_timeout --kwargs "{'reno_order': 'RO-...'}"`
	"""
	result = {
		"how_to_investigate": [
			"Desk → Error Log: titles 'Reno Order installed processing failed' / 'enqueue failed'.",
			"Desk → RQ Job: job_id reno-installed-<name>. Status, traceback, started/ended.",
			"bench logs: logs/web.log (504 / Request Timeout), logs/worker.log (job start/fail).",
			"nginx/gunicorn timeout vs worker still running: error on the browser, commit already done.",
			"SELECT name, docstatus, reno_order FROM `tabDelivery Note` WHERE reno_order=%s.",
			"Reno Order.processing_status, delivery_note, status — already Installed after a 'failed' click.",
		],
		"suspected_root_cause": (
			"The HTTP request that marks Installed commits (status + enqueue) before the "
			"browser gets a response. A proxy/worker timeout then shows an error. The "
			"queued job, or a retry/double-click, still creates the Delivery Note. Without "
			"a row lock and a deterministic DN name, two retries can insert two DNs."
		),
	}

	if not reno_order:
		return result

	if not frappe.db.exists("Reno Order", reno_order):
		result["reno_order"] = reno_order
		result["found"] = False
		return result

	order = frappe.db.get_value(
		"Reno Order",
		reno_order,
		["name", "status", "processing_status", "delivery_note", "sales_order"],
		as_dict=True,
	)
	delivery_notes = frappe.get_all(
		"Delivery Note",
		filters={"reno_order": reno_order},
		fields=["name", "docstatus", "creation", "modified"],
		order_by="creation asc",
	)
	error_logs = []
	if frappe.db.exists("DocType", "Error Log"):
		error_logs = frappe.get_all(
			"Error Log",
			filters={"method": ("like", f"%{reno_order}%")},
			fields=["name", "method", "creation"],
			order_by="creation desc",
			limit=5,
		)

	rq_jobs = []
	if frappe.db.exists("DocType", "RQ Job"):
		rq_jobs = frappe.get_all(
			"RQ Job",
			filters={"job_name": ("like", f"%reno-installed-{reno_order}%")},
			fields=["name", "job_name", "status", "creation"],
			order_by="creation desc",
			limit=5,
		)

	result.update(
		{
			"found": True,
			"reno_order": order,
			"expected_auto_dn_name": installed_delivery_note_name(reno_order),
			"delivery_notes": delivery_notes,
			"duplicate_dns": len(delivery_notes) > 1,
			"error_logs": error_logs,
			"rq_jobs": rq_jobs,
		}
	)
	return result
