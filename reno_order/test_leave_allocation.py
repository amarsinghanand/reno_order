import frappe
from frappe.tests import UnitTestCase
from frappe.utils import getdate

from hrms.hr.doctype.leave_policy_assignment.leave_policy_assignment import calculate_pro_rated_leaves

from reno_order.install import ensure_custom_fields
from reno_order.leave import (
	ANNUAL_ALLOCATION,
	ANNUAL_LEAVE,
	EVENT_LEAVE_TYPES,
	RenoLeavePolicyAssignment,
	ensure_leave_types,
	is_event_based_leave,
	leaves_to_allocate,
)


PERIOD_START = getdate("2026-01-01")
PERIOD_END = getdate("2026-12-31")
MID_YEAR_DOJ = getdate("2026-07-01")


class TestLeaveAllocation(UnitTestCase):
	def setUp(self):
		ensure_custom_fields()
		ensure_leave_types()

	def test_event_based_flag_on_leave_types(self):
		self.assertFalse(is_event_based_leave(ANNUAL_LEAVE))
		for name in EVENT_LEAVE_TYPES:
			self.assertTrue(is_event_based_leave(name), name)

	def test_hrms_prorates_event_leave_for_mid_year_joiner(self):
		"""Document the HRMS bug: Maternity is treated like Annual Leave."""
		hrms_maternity = calculate_pro_rated_leaves(
			EVENT_LEAVE_TYPES["Maternity Leave"],
			MID_YEAR_DOJ,
			PERIOD_START,
			PERIOD_END,
			is_earned_leave=False,
		)
		self.assertLess(hrms_maternity, EVENT_LEAVE_TYPES["Maternity Leave"])
		self.assertNotEqual(hrms_maternity, EVENT_LEAVE_TYPES["Maternity Leave"])

	def test_mid_year_annual_leave_is_prorated(self):
		allocated = leaves_to_allocate(
			ANNUAL_ALLOCATION, ANNUAL_LEAVE, MID_YEAR_DOJ, PERIOD_START, PERIOD_END
		)
		expected = calculate_pro_rated_leaves(
			ANNUAL_ALLOCATION, MID_YEAR_DOJ, PERIOD_START, PERIOD_END, is_earned_leave=False
		)
		self.assertEqual(allocated, expected)
		self.assertLess(allocated, ANNUAL_ALLOCATION)

	def test_mid_year_event_leaves_keep_full_entitlement(self):
		for name, days in EVENT_LEAVE_TYPES.items():
			allocated = leaves_to_allocate(days, name, MID_YEAR_DOJ, PERIOD_START, PERIOD_END)
			self.assertEqual(allocated, days, name)

	def test_full_year_joiner_gets_full_annual_leave(self):
		allocated = leaves_to_allocate(
			ANNUAL_ALLOCATION, ANNUAL_LEAVE, PERIOD_START, PERIOD_START, PERIOD_END
		)
		self.assertEqual(allocated, ANNUAL_ALLOCATION)

	def test_overridden_assignment_class_is_used(self):
		assignment = frappe.new_doc("Leave Policy Assignment")
		self.assertIsInstance(assignment, RenoLeavePolicyAssignment)

	def test_get_new_leaves_skips_proration_for_event_types(self):
		assignment = frappe.new_doc("Leave Policy Assignment")
		assignment.effective_from = PERIOD_START
		assignment.effective_to = PERIOD_END

		maternity = frappe._dict(
			name="Maternity Leave",
			is_compensatory=0,
			is_earned_leave=0,
			earned_leave_frequency=None,
		)
		self.assertEqual(assignment.get_new_leaves(90, maternity, MID_YEAR_DOJ), 90)

		annual = frappe._dict(
			name=ANNUAL_LEAVE,
			is_compensatory=0,
			is_earned_leave=0,
			earned_leave_frequency=None,
		)
		self.assertEqual(
			assignment.get_new_leaves(12, annual, MID_YEAR_DOJ),
			calculate_pro_rated_leaves(12, MID_YEAR_DOJ, PERIOD_START, PERIOD_END),
		)
