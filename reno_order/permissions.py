import frappe
from frappe import _
from frappe.utils import cint, flt

SELLING_PARENT_FIELDS = (
	"customer",
	"discount_percentage",
	"discount_amount",
	"total_amount",
	"grand_total",
)
SELLING_ITEM_FIELDS = ("item_code", "qty", "rate", "amount")
SELLING_OVERRIDE_ROLES = {"System Manager", "Sales Manager", "Sales User"}


def get_permission_query_conditions(user=None):
	"""Restrict Reno Order lists by role. System Manager is unrestricted."""
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return ""

	roles = set(frappe.get_roles(user))
	user_escaped = frappe.db.escape(user)
	conditions = []

	if "Sales Manager" in roles:
		team = get_team_users(user)
		if team is None:
			conditions.append("1=1")
		else:
			team_sql = ", ".join(frappe.db.escape(u) for u in team)
			conditions.append(
				f"(`tabReno Order`.owner in ({team_sql}) or ifnull(`tabReno Order`.assigned_to, '') in ({team_sql}))"
			)

	if "Sales User" in roles:
		conditions.append(
			f"(`tabReno Order`.owner = {user_escaped} or `tabReno Order`.assigned_to = {user_escaped})"
		)

	if "Site Supervisor" in roles:
		conditions.append(f"`tabReno Order`.assigned_to = {user_escaped}")

	if "Production User" in roles:
		conditions.append("`tabReno Order`.docstatus = 1")

	if "Accounts User" in roles:
		conditions.append("`tabReno Order`.docstatus >= 0")

	return f"({' or '.join(conditions)})" if conditions else "1=0"


def has_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return True

	roles = set(frappe.get_roles(user))
	owner = doc.get("owner")
	assigned = doc.get("assigned_to")

	if "Sales Manager" in roles:
		team = get_team_users(user)
		if team is None or owner in team or assigned in team:
			return True
	if "Sales User" in roles and (owner == user or assigned == user):
		return True
	if "Site Supervisor" in roles and assigned == user:
		return True
	if "Production User" in roles and cint(doc.get("docstatus")) == 1:
		return True
	if "Accounts User" in roles:
		return permission_type in (None, "read", "print", "email", "export", "report", "select")

	return False


def is_site_supervisor_only(user=None):
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	return "Site Supervisor" in roles and not roles.intersection(SELLING_OVERRIDE_ROLES)


def validate_site_supervisor_selling_fields(doc):
	"""Server-side: Site Supervisor may not change selling or financial fields."""
	if not is_site_supervisor_only():
		return

	previous = doc.get_doc_before_save()
	if not previous:
		if doc.is_new() or not doc.name or not frappe.db.exists(doc.doctype, doc.name):
			frappe.throw(_("Site Supervisor cannot create Reno Orders."), frappe.PermissionError)
		previous = frappe.get_doc(doc.doctype, doc.name)

	changed = []
	for fieldname in SELLING_PARENT_FIELDS:
		if _values_differ(doc.get(fieldname), previous.get(fieldname)):
			changed.append(fieldname)

	if _selling_items_changed(doc.get("items") or [], previous.get("items") or []):
		changed.append("items")

	if changed:
		frappe.throw(
			_("Site Supervisor cannot change selling or financial fields: {0}.").format(
				", ".join(changed)
			),
			frappe.PermissionError,
		)


def _selling_items_changed(current, previous):
	def fingerprint(rows):
		return [
			(
				row.get("name"),
				row.get("item_code"),
				flt(row.get("qty")),
				flt(row.get("rate")),
				flt(row.get("amount")),
			)
			for row in rows
		]

	return fingerprint(current) != fingerprint(previous)


def _values_differ(current, previous):
	if isinstance(current, (int, float)) or isinstance(previous, (int, float)):
		return flt(current) != flt(previous)
	return (current or "") != (previous or "")


def get_team_users(user):
	"""Sales Manager team = self + Employee reports.

	Returns None when the manager has no Employee record, which means
	unrestricted team visibility (broader access).
	"""
	if not frappe.db.exists("DocType", "Employee"):
		return None

	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")
	if not employee:
		return None

	team = {user}
	for user_id in frappe.get_all(
		"Employee",
		filters={"reports_to": employee, "status": "Active"},
		pluck="user_id",
	):
		if user_id:
			team.add(user_id)
	return team


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def supervisor_user_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search: enabled users who have the Site Supervisor role."""
	return frappe.db.sql(
		"""
		SELECT u.name, concat_ws(' ', u.first_name, u.last_name)
		FROM `tabUser` u
		INNER JOIN `tabHas Role` hr ON hr.parent = u.name AND hr.role = 'Site Supervisor'
		WHERE u.enabled = 1
			AND u.name != 'Guest'
			AND (u.name LIKE %(txt)s OR ifnull(u.full_name, '') LIKE %(txt)s)
		ORDER BY u.full_name
		LIMIT %(start)s, %(page_len)s
		""",
		{"txt": f"%{txt}%", "start": cint(start), "page_len": cint(page_len)},
	)
