"""Script Report: order value and count by month and status."""

import frappe
from frappe import _

from reno_order.reporting import get_monthly_value_data


def execute(filters=None):
	"""Standard Frappe report entry: columns, data, message, chart."""
	columns = get_columns()
	data = get_monthly_value_data(filters)
	return columns, data, None, get_chart(data)


def get_columns():
	return [
		{"fieldname": "month", "label": _("Month"), "fieldtype": "Data", "width": 120},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "order_value",
			"label": _("Order Value"),
			"fieldtype": "Currency",
			"width": 140,
		},
		{"fieldname": "order_count", "label": _("Orders"), "fieldtype": "Int", "width": 100},
	]


def get_chart(data):
	months = []
	totals = []
	for row in data:
		if row.month not in months:
			months.append(row.month)
			totals.append(0)
		totals[months.index(row.month)] += float(row.order_value or 0)
	if not months:
		return None
	return {
		"data": {"labels": months, "datasets": [{"name": _("Order Value"), "values": totals}]},
		"type": "bar",
	}
