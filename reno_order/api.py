import os

import frappe
from frappe import _
from frappe.utils import now_datetime

from reno_order.constants import STATUS_TRANSITIONS


INSTALLATION_STATUSES = {"Installed"}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
WORKFLOW_ACTION_BY_STATUS = {
	"Confirmed": "Confirm",
	"In Production": "Start Production",
	"Ready for Installation": "Mark Ready for Installation",
	"Installed": "Mark Installed",
	"Closed": "Close",
	"Cancelled": "Cancel",
}


def _require_login():
	if frappe.session.user in (None, "Guest"):
		frappe.throw(_("Authentication is required."), frappe.AuthenticationError)


def _can_update_installation(doc, user=None):
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if "System Manager" in roles or "Sales Manager" in roles:
		return True
	return "Site Supervisor" in roles and doc.get("assigned_to") == user


def _get_writable_order(reno_order):
	_require_login()
	if not reno_order:
		frappe.throw(_("reno_order is required."))
	if not frappe.db.exists("Reno Order", reno_order):
		frappe.throw(_("Reno Order {0} was not found.").format(reno_order), frappe.DoesNotExistError)

	doc = frappe.get_doc("Reno Order", reno_order)
	if not frappe.has_permission("Reno Order", "write", doc=doc):
		frappe.throw(
			_("You do not have permission to update Reno Order {0}.").format(reno_order),
			frappe.PermissionError,
		)
	if not _can_update_installation(doc):
		frappe.throw(
			_("Only the assigned Site Supervisor can update installation details for this order."),
			frappe.PermissionError,
		)
	return doc


def _apply_status(doc, status):
	if status not in INSTALLATION_STATUSES:
		frappe.throw(
			_("This API only accepts installation status {0}.").format(", ".join(sorted(INSTALLATION_STATUSES)))
		)
	if doc.status == status:
		return doc

	allowed_roles = STATUS_TRANSITIONS.get(doc.status, {}).get(status)
	if not allowed_roles:
		frappe.throw(_("Cannot change status from {0} to {1}.").format(doc.status, status))

	roles = set(frappe.get_roles())
	if "System Manager" not in roles and not roles.intersection(allowed_roles):
		frappe.throw(
			_("You cannot move this Reno Order from {0} to {1}.").format(doc.status, status),
			frappe.PermissionError,
		)

	from frappe.model.workflow import apply_workflow, get_transitions

	action = WORKFLOW_ACTION_BY_STATUS.get(status)
	transitions = get_transitions(doc) if action else []
	if action and any(t.get("action") == action and t.get("next_state") == status for t in transitions):
		apply_workflow(doc, action)
	else:
		doc.status = status
		doc.save()
	return frappe.get_doc("Reno Order", doc.name)


@frappe.whitelist(methods=["POST"])
def update_installation_status(reno_order, status):
	"""Mobile / Desk: mark a ready order as Installed.

	Idempotent: a timeout-and-retry after the first request already committed
	returns the current status and any downstream Delivery Note instead of
	raising or creating a second document.
	"""
	writable = _get_writable_order(reno_order)
	already = writable.status == status
	doc = _apply_status(writable, status)
	return {
		"reno_order": doc.name,
		"status": doc.status,
		"already_installed": bool(already),
		"delivery_note": doc.delivery_note,
		"processing_status": doc.processing_status,
	}


@frappe.whitelist(methods=["POST"])
def add_installation_remarks(reno_order, remarks):
	"""Mobile: add installation remarks without changing selling fields."""
	if not remarks or not str(remarks).strip():
		frappe.throw(_("remarks is required."))

	doc = _get_writable_order(reno_order)
	note = str(remarks).strip()
	stamp = now_datetime()
	if doc.installation_remarks:
		doc.installation_remarks = f"{doc.installation_remarks}\n[{stamp}] {note}"
	else:
		doc.installation_remarks = f"[{stamp}] {note}"
	doc.save()
	return {"reno_order": doc.name, "remarks": doc.installation_remarks}


@frappe.whitelist(methods=["POST"])
def attach_site_photo(reno_order, filename=None):
	"""Mobile: attach a private site photo to the Reno Order."""
	doc = _get_writable_order(reno_order)
	content, filename = _read_upload(filename)
	extension = os.path.splitext(filename)[1].lower()
	if extension not in ALLOWED_IMAGE_EXTENSIONS:
		frappe.throw(_("Only image files can be attached ({0}).").format(", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))))

	from frappe.utils.file_manager import save_file

	file_doc = save_file(filename, content, "Reno Order", doc.name, is_private=1)
	return {
		"reno_order": doc.name,
		"file_name": file_doc.name,
		"file_url": file_doc.file_url,
	}


@frappe.whitelist()
def get_installation(reno_order):
	"""Mobile: load status, remarks and site photos for an assigned order."""
	_require_login()
	if not frappe.db.exists("Reno Order", reno_order):
		frappe.throw(_("Reno Order {0} was not found.").format(reno_order), frappe.DoesNotExistError)

	doc = frappe.get_doc("Reno Order", reno_order)
	if not frappe.has_permission("Reno Order", "read", doc=doc):
		frappe.throw(_("You do not have permission to read this Reno Order."), frappe.PermissionError)

	return {
		"reno_order": doc.name,
		"status": doc.status,
		"assigned_to": doc.assigned_to,
		"expected_installation_date": doc.expected_installation_date,
		"is_overdue": doc.is_overdue,
		"remarks": doc.installation_remarks,
		"photos": frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Reno Order", "attached_to_name": doc.name, "is_folder": 0},
			fields=["name", "file_name", "file_url", "is_private"],
			order_by="creation desc",
		),
	}


def _read_upload(filename):
	uploaded = None
	if getattr(frappe, "request", None) and frappe.request.files:
		uploaded = frappe.request.files.get("file") or frappe.request.files.get("site_photo")

	if uploaded:
		return uploaded.stream.read(), filename or uploaded.filename or "site-photo.jpg"

	filedata = frappe.form_dict.get("filedata") or frappe.local.form_dict.get("filedata")
	if not filedata:
		frappe.throw(_("Attach an image using the file field or filedata."))
	if not filename:
		frappe.throw(_("filename is required when sending filedata."))
	return filedata, filename
