import frappe


def get_company():
	return frappe.defaults.get_global_default("company") or frappe.db.get_value("Company", {})


def get_warehouse(company=None):
	company = company or get_company()
	return (
		frappe.db.get_value("Warehouse", {"company": company, "name": ("like", "Stores%"), "is_group": 0}, "name")
		or frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0, "warehouse_type": ["is", "not set"]}, "name"
		)
		or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	)
