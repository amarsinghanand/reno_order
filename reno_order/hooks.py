"""Frappe app hooks for Reno Order.

Wires row permissions, the daily overdue job, CRM/Installed queues, and
``reno_order`` copy-forward on standard ERPNext documents. Leave Policy
Assignment is subclassed here so event-based leave is not prorated.
"""

app_name = "reno_order"
app_title = "Reno Order"
app_publisher = "Savyant Assignment"
app_description = "Kitchen renovation workflow layered on standard ERPNext"
app_email = "developer@example.com"
app_license = "mit"
required_apps = ["erpnext", "hrms"]

override_doctype_class = {
	"Leave Policy Assignment": "reno_order.leave.RenoLeavePolicyAssignment",
}

after_install = "reno_order.install.after_install"
after_migrate = "reno_order.install.after_migrate"

permission_query_conditions = {
	"Reno Order": "reno_order.permissions.get_permission_query_conditions",
}

has_permission = {
	"Reno Order": "reno_order.permissions.has_permission",
}

scheduler_events = {
	"daily": [
		"reno_order.tasks.flag_overdue_installations",
	]
}

doc_events = {
	"Sales Order": {
		"on_submit": "reno_order.tasks.on_sales_order_submit",
		"on_update": "reno_order.integrations.link_downstream_to_reno_order",
	},
	"Delivery Note": {
		"validate": "reno_order.integrations.inherit_reno_order",
		"on_update": "reno_order.integrations.link_downstream_to_reno_order",
	},
	"Sales Invoice": {
		"validate": "reno_order.integrations.inherit_reno_order",
		"on_update": "reno_order.integrations.link_downstream_to_reno_order",
	},
	"Work Order": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Job Card": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Stock Entry": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Material Request": {
		"on_update": "reno_order.integrations.link_downstream_to_reno_order",
	},
	"Request for Quotation": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Supplier Quotation": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Purchase Order": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Purchase Receipt": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
	"Purchase Invoice": {
		"validate": "reno_order.integrations.inherit_reno_order",
	},
}

fixtures = [
	{
		"dt": "Custom Field",
		"filters": [["module", "=", "Reno Order"]],
	},
	{
		"dt": "Role",
		"filters": [["role_name", "in", ["Site Supervisor", "Production User"]]],
	},
]
