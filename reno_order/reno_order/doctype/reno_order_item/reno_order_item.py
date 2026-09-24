"""Child row of Reno Order. Amount is set on the parent in calculate_totals()."""

from frappe.model.document import Document


class RenoOrderItem(Document):
	"""Item, qty, rate, warehouse. No extra business rules live here."""

	pass
