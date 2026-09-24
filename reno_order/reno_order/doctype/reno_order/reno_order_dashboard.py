"""Desk Connections: order-to-cash, manufacturing, and buying documents."""

from frappe import _


def get_data():
	"""Documents listed on the Reno Order form that share field ``reno_order``."""
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
