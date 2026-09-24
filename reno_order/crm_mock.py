"""In-process / HTTP mock of an external CRM. Not a real third-party host."""

import frappe
from frappe import _


def handle_upsert(headers, payload):
	"""Accept a Bearer token and return CRM-<reno_order>. Same contract as a remote API."""
	token = _bearer_token(headers)
	expected = frappe.get_single("Reno Settings").get_password("crm_api_token", raise_exception=False)
	if not expected or token != expected:
		return 401, {"error": "unauthorized"}

	reno_order = (payload or {}).get("reno_order")
	if not reno_order:
		return 400, {"error": "reno_order is required"}

	crm_id = f"CRM-{reno_order}"
	return 200, {"crm_id": crm_id, "status": "accepted"}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def upsert_order():
	"""HTTP face of the mock CRM. Same auth and payload as a remote service."""
	headers = {"Authorization": frappe.get_request_header("Authorization")}
	payload = frappe.request.get_json(silent=True) if getattr(frappe, "request", None) else None
	if not payload:
		payload = {k: v for k, v in frappe.form_dict.items() if k != "cmd"}

	status_code, body = handle_upsert(headers, payload)
	if status_code == 401:
		frappe.throw(_("CRM authentication failed."), frappe.AuthenticationError)
	if status_code >= 400:
		frappe.throw(_(body.get("error") or "CRM request failed."))
	return body


def _bearer_token(headers):
	value = (headers or {}).get("Authorization") or ""
	if value.lower().startswith("bearer "):
		return value.split(" ", 1)[1].strip()
	return ""
