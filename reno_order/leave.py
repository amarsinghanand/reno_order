import frappe
from frappe.utils import cint, flt, getdate

from hrms.hr.doctype.leave_policy_assignment.leave_policy_assignment import (
	LeavePolicyAssignment,
	calculate_pro_rated_leaves,
)


ANNUAL_LEAVE = "Annual Leave"
EVENT_LEAVE_TYPES = {
	"Maternity Leave": 90,
	"Paternity Leave": 15,
	"Marriage Leave": 5,
}
ANNUAL_ALLOCATION = 12


def is_event_based_leave(leave_type_name):
	"""True when Leave Type is marked as a fixed entitlement (do not prorate)."""
	if not leave_type_name or not frappe.db.has_column("Leave Type", "is_event_based"):
		return False
	return cint(frappe.db.get_value("Leave Type", leave_type_name, "is_event_based"))


def leaves_to_allocate(annual_allocation, leave_type_name, date_of_joining, effective_from, effective_to):
	"""Leaves to grant for a policy row.

	Annual / accrual-style types stay on HRMS day-based proration.
	Event-based types keep the full policy entitlement even if the employee
	joined mid-period.
	"""
	annual_allocation = flt(annual_allocation)
	if is_event_based_leave(leave_type_name):
		return annual_allocation
	return calculate_pro_rated_leaves(
		annual_allocation,
		date_of_joining,
		effective_from,
		effective_to,
		is_earned_leave=False,
	)


class RenoLeavePolicyAssignment(LeavePolicyAssignment):
	"""HRMS always prorates non-earned, non-compensatory leave.

	That is correct for Annual Leave. It is wrong for event-based
	entitlements (Maternity / Paternity / Marriage), which are granted
	in full when the event occurs, not earned over the year.
	"""

	def get_new_leaves(self, annual_allocation, leave_details, date_of_joining):
		if is_event_based_leave(leave_details.name):
			from frappe.model.meta import get_field_precision

			precision = get_field_precision(
				frappe.get_meta("Leave Allocation").get_field("new_leaves_allocated")
			)
			return flt(annual_allocation, precision)
		return super().get_new_leaves(annual_allocation, leave_details, date_of_joining)


def ensure_leave_types():
	"""Idempotent Leave Type + policy masters used by the Point 13 demo."""
	if not frappe.db.exists("DocType", "Leave Type"):
		return

	_ensure_leave_type(ANNUAL_LEAVE, ANNUAL_ALLOCATION, event_based=False)
	for name, days in EVENT_LEAVE_TYPES.items():
		_ensure_leave_type(name, days, event_based=True)


def _ensure_leave_type(name, max_leaves, event_based):
	if frappe.db.exists("Leave Type", name):
		updates = {}
		if frappe.db.has_column("Leave Type", "is_event_based"):
			updates["is_event_based"] = 1 if event_based else 0
		current_max = flt(frappe.db.get_value("Leave Type", name, "max_leaves_allowed"))
		if current_max and current_max < max_leaves:
			updates["max_leaves_allowed"] = max_leaves
		if updates:
			frappe.db.set_value("Leave Type", name, updates)
		return frappe.get_doc("Leave Type", name)

	doc = frappe.get_doc(
		{
			"doctype": "Leave Type",
			"leave_type_name": name,
			"max_leaves_allowed": max_leaves,
			"include_holiday": 0,
		}
	)
	if frappe.db.has_column("Leave Type", "is_event_based"):
		doc.is_event_based = 1 if event_based else 0
	doc.insert(ignore_permissions=True)
	return doc
