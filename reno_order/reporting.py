import frappe
from frappe.utils import add_months, getdate, today

INDEX_NAME = "idx_reno_order_date_status"
REPORT_SQL = """
SELECT
	DATE_FORMAT(transaction_date, '%%Y-%%m') AS month,
	status,
	SUM(grand_total) AS order_value,
	COUNT(*) AS order_count
FROM `tabReno Order`
WHERE transaction_date >= %(from_date)s
	AND transaction_date <= %(to_date)s
	AND docstatus < 2
	{extra}
GROUP BY DATE_FORMAT(transaction_date, '%%Y-%%m'), status
ORDER BY month, status
"""


def default_from_date():
	return add_months(getdate(today()), -12)


def report_params(filters=None):
	filters = frappe._dict(filters or {})
	from_date = getdate(filters.get("from_date") or default_from_date())
	to_date = getdate(filters.get("to_date") or today())
	if from_date > to_date:
		frappe.throw(frappe._("From Date cannot be after To Date."))

	extra = []
	params = {"from_date": from_date, "to_date": to_date}
	if filters.get("company"):
		extra.append("AND company = %(company)s")
		params["company"] = filters.company
	if filters.get("status"):
		extra.append("AND status = %(status)s")
		params["status"] = filters.status
	return extra, params


def monthly_value_sql(filters=None, ignore_index=False):
	extra, params = report_params(filters)
	sql = REPORT_SQL.format(extra="\n\t".join(extra))
	if ignore_index:
		sql = sql.replace(
			"FROM `tabReno Order`",
			f"FROM `tabReno Order` IGNORE INDEX (`{INDEX_NAME}`)",
		)
	return sql, params


def get_monthly_value_data(filters=None):
	sql, params = monthly_value_sql(filters)
	return frappe.db.sql(sql, params, as_dict=True)


def explain_monthly_value(filters=None, ignore_index=False):
	sql, params = monthly_value_sql(filters, ignore_index=ignore_index)
	return frappe.db.sql(f"EXPLAIN {sql}", params, as_dict=True)


def has_date_status_index():
	return bool(
		frappe.db.sql(
			"""
			SHOW INDEX FROM `tabReno Order`
			WHERE Key_name = %s
			""",
			INDEX_NAME,
		)
	)


def ensure_date_status_index():
	if has_date_status_index():
		return False
	try:
		frappe.db.sql_ddl(
			f"""
			ALTER TABLE `tabReno Order`
			ADD INDEX `{INDEX_NAME}` (transaction_date, status, docstatus, grand_total),
			ALGORITHM=INPLACE, LOCK=NONE
			"""
		)
	except Exception:
		frappe.db.sql_ddl(
			f"""
			ALTER TABLE `tabReno Order`
			ADD INDEX `{INDEX_NAME}` (transaction_date, status, docstatus, grand_total)
			"""
		)
	return True
