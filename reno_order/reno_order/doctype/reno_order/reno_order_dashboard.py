from frappe import _


def get_data():
	return {
		"fieldname": "reno_order",
		"transactions": [
			{
				"label": _("Order to Cash"),
				"items": ["Sales Order", "Delivery Note", "Sales Invoice"],
			},
			{
				"label": _("Manufacturing"),
				"items": ["Work Order", "Job Card", "Stock Entry"],
			},
			{
				"label": _("Buying"),
				"items": [
					"Material Request",
					"Request for Quotation",
					"Supplier Quotation",
					"Purchase Order",
					"Purchase Receipt",
					"Purchase Invoice",
				],
			},
		],
	}
