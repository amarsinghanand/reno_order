import frappe
from frappe.model.document import Document
from frappe.utils import flt


class RenoSettings(Document):
	def validate(self):
		if flt(self.discount_approval_threshold) < 0:
			frappe.throw(frappe._("Discount Approval Threshold cannot be negative."))
		if flt(self.discount_approval_threshold) > 100:
			frappe.throw(frappe._("Discount Approval Threshold cannot exceed 100%."))
		if self.crm_timeout_seconds not in (None, "") and int(self.crm_timeout_seconds) < 1:
			frappe.throw(frappe._("CRM Timeout must be at least 1 second."))
		if self.crm_max_retries not in (None, "") and int(self.crm_max_retries) < 0:
			frappe.throw(frappe._("CRM Max Retries cannot be negative."))
