"""Add a covering index for Monthly Reno Order Value (last 12 months by status)."""

from reno_order.reporting import ensure_date_status_index


def execute():
	ensure_date_status_index()
