"""Company and Stores warehouse used when a Reno Order row has no explicit value."""

import frappe


def get_company():
	"""Default company: global default, then the first Company on the site."""
	return frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {})


def get_warehouse(company=None):
	"""Prefer a non-group Stores warehouse for the company."""
	company = company or get_company()
	return (
		frappe.db.get_value("Warehouse", {"company": company, "name": ("like", "Stores%"), "is_group": 0}, "name")
		or frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0, "warehouse_type": ["is", "not set"]}, "name"
		)
		or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	)
