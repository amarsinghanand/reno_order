import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_ROLES = ("Site Supervisor", "Production User")

CUSTOM_FIELDS = {
	"Sales Order": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "customer",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Delivery Note": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "customer",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Sales Invoice": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "customer",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Work Order": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "sales_order",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Job Card": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "work_order",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Stock Entry": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "work_order",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Material Request": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "company",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Request for Quotation": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "company",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Supplier Quotation": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "supplier",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Purchase Order": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "supplier",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Purchase Receipt": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "supplier",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	"Purchase Invoice": [
		{
			"fieldname": "reno_order",
			"label": "Reno Order",
			"fieldtype": "Link",
			"options": "Reno Order",
			"insert_after": "supplier",
			"read_only": 1,
			"no_copy": 1,
			"module": "Reno Order",
			"search_index": 1,
		}
	],
	# ERPNext 16.35 party.py filters Contact.is_billing_contact, which is not
	# on Frappe 16.35 Contact. Add it as a custom field so standard Selling works.
	"Contact": [
		{
			"fieldname": "is_billing_contact",
			"label": "Is Billing Contact",
			"fieldtype": "Check",
			"insert_after": "is_primary_contact",
			"default": "0",
			"module": "Reno Order",
		}
	],
	# Event-based leave (Maternity / Paternity / Marriage) must not be
	# prorated by joining date. HRMS has no standard flag for this.
	"Leave Type": [
		{
			"fieldname": "is_event_based",
			"label": "Event Based (Do Not Prorate)",
			"fieldtype": "Check",
			"insert_after": "is_optional_leave",
			"default": "0",
			"description": "Grant the full policy entitlement even if the employee joined mid-period. Use for Maternity, Paternity and Marriage leave.",
			"module": "Reno Order",
		}
	],
}


def after_install():
	ensure_roles()
	ensure_custom_fields()
	ensure_default_settings()
	from reno_order.workflow import ensure_workflow
	from reno_order.buying import ensure_buying_masters
	from reno_order.manufacturing import ensure_manufacturing_masters

	ensure_workflow()
	ensure_manufacturing_masters()
	ensure_buying_masters()
	from reno_order.leave import ensure_leave_types

	ensure_leave_types()


def after_migrate():
	ensure_roles()
	ensure_custom_fields()
	ensure_default_settings()
	from reno_order.workflow import ensure_workflow
	from reno_order.buying import ensure_buying_masters
	from reno_order.manufacturing import ensure_manufacturing_masters
	from reno_order.leave import ensure_leave_types

	ensure_workflow()
	ensure_manufacturing_masters()
	ensure_buying_masters()
	ensure_leave_types()


def ensure_roles():
	for role_name in CUSTOM_ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def ensure_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, update=True)


def ensure_default_settings():
	if not frappe.db.exists("DocType", "Reno Settings"):
		return

	settings = frappe.get_single("Reno Settings")
	changed = False
	if settings.discount_approval_threshold in (None, ""):
		settings.discount_approval_threshold = 10
		changed = True
	if not settings.discount_approver_role and frappe.db.exists("Role", "Sales Manager"):
		settings.discount_approver_role = "Sales Manager"
		changed = True
	if changed:
		settings.save(ignore_permissions=True)

	from reno_order.crm import ensure_crm_defaults

	ensure_crm_defaults()
