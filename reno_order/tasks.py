"""Scheduled and queued jobs for Reno Order.

Daily: mark open orders overdue when the install date has passed.
On Installed (and when the linked Sales Order is later submitted): create at
most one draft Delivery Note on a worker, not on the form save.
"""

import frappe
from frappe.utils import getdate, nowdate

from reno_order.constants import CLOSED_STATUSES


def flag_overdue_installations():
	"""Daily job: flag Reno Orders whose installation date has passed."""
	today = nowdate()
	open_orders = frappe.get_all(
		"Reno Order",
		filters={
			"status": ["not in", CLOSED_STATUSES],
			"docstatus": ["<", 2],
			"expected_installation_date": ["is", "set"],
		},
		fields=["name", "expected_installation_date", "is_overdue"],
	)

	for order in open_orders:
		should_flag = getdate(order.expected_installation_date) < getdate(today)
		if should_flag and not order.is_overdue:
			frappe.db.set_value("Reno Order", order.name, "is_overdue", 1, update_modified=False)
		elif not should_flag and order.is_overdue:
			frappe.db.set_value("Reno Order", order.name, "is_overdue", 0, update_modified=False)


def process_installed_order(reno_order: str):
	"""Create or reuse the downstream Delivery Note without blocking the UI save."""
	from reno_order.reno_order.doctype.reno_order.reno_order import create_downstream_delivery_note

	create_downstream_delivery_note(reno_order)


def on_sales_order_submit(doc, method=None):
	"""Retry Installed processing once the linked Sales Order is submitted."""
	reno_order = doc.get("reno_order")
	if not reno_order:
		return

	status, delivery_note = frappe.db.get_value("Reno Order", reno_order, ["status", "delivery_note"])
	if status != "Installed" or delivery_note:
		return

	frappe.enqueue(
		"reno_order.tasks.process_installed_order",
		reno_order=reno_order,
		queue="default",
		enqueue_after_commit=True,
		deduplicate=True,
		job_id=f"reno-installed-{reno_order}",
	)
