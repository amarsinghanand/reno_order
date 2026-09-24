"""Reno Order document: totals, workflow, and links to standard ERPNext.

Financial amounts are always recalculated on the server. Status changes follow
STATUS_TRANSITIONS. Downstream Sales Order / Delivery Note / Invoice / Work
Order / Material Request are created once and linked both ways.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, nowdate

from reno_order.constants import CLOSED_STATUSES, STATUS_TRANSITIONS


class RenoOrder(Document):
	"""Kitchen renovation order. Submit starts Confirmed; Installed is queued."""

	def validate(self):
		"""Defaults, business rules, role checks, then overwrite client totals."""
		self.set_missing_defaults()
		self.validate_business_rules()
		self.validate_status_transition()
		from reno_order.permissions import validate_site_supervisor_selling_fields

		validate_site_supervisor_selling_fields(self)
		self.calculate_totals()
		self.refresh_overdue_flag()

	def before_submit(self):
		"""High discounts need the approver role. First submit leaves Draft."""
		self.validate_discount_authority()
		if self.status == "Draft":
			self.status = "Confirmed"

	def on_submit(self):
		"""Queue CRM sync after commit so the user is not waiting on HTTP."""
		from reno_order.crm import sync_on_submit

		sync_on_submit(self)

	def on_update(self):
		"""If status just became Installed, enqueue one Delivery Note job."""
		self.enqueue_installed_processing()

	def before_cancel(self):
		"""Block cancel while a submitted SO / DN / SI / Work Order still exists."""
		self.validate_no_submitted_downstream()

	def on_cancel(self):
		self.status = "Cancelled"
		self.is_overdue = 0

	def set_missing_defaults(self):
		"""Company, currency, Draft, Standard order type, and today's date."""
		if not self.company:
			self.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
				"Global Defaults", "default_company"
			)
		if self.company and not self.currency:
			self.currency = frappe.get_cached_value("Company", self.company, "default_currency")
		if not self.status:
			self.status = "Draft"
		if not self.order_type:
			self.order_type = "Standard"
		if not self.processing_status:
			self.processing_status = "Pending"
		if not self.crm_sync_status:
			self.crm_sync_status = "Not Synced"
		if not self.transaction_date:
			self.transaction_date = getdate()

	def after_insert(self):
		"""New orders default Assigned To to the owner so Sales User list filters work."""
		if not self.assigned_to:
			self.db_set("assigned_to", self.owner, update_modified=False)

	def validate_business_rules(self):
		"""No empty items, no negative qty/rate/discount, install date not before order date."""
		if not self.items:
			frappe.throw(_("Please add at least one item."))

		if flt(self.discount_percentage) < 0:
			frappe.throw(_("Discount % cannot be negative."))
		if flt(self.discount_percentage) > 100:
			frappe.throw(_("Discount % cannot exceed 100."))

		if self.expected_installation_date and self.transaction_date:
			if getdate(self.expected_installation_date) < getdate(self.transaction_date):
				frappe.throw(_("Expected Installation Date cannot be before Transaction Date."))

		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(_("Row {0}: Quantity cannot be zero or negative.").format(row.idx))
			if flt(row.rate) < 0:
				frappe.throw(_("Row {0}: Rate cannot be negative.").format(row.idx))

	def validate_status_transition(self):
		"""Reject skipped statuses and roles that the workflow would not allow."""
		if self.flags.get("ignore_status_transition") or self.is_new():
			return

		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return

		allowed_roles = STATUS_TRANSITIONS.get(previous.status, {}).get(self.status)
		if not allowed_roles:
			frappe.throw(_("Cannot change status from {0} to {1}.").format(previous.status, self.status))

		roles = set(frappe.get_roles(frappe.session.user))
		if "System Manager" not in roles and not roles.intersection(allowed_roles):
			frappe.throw(
				_("You cannot move this Reno Order from {0} to {1}.").format(previous.status, self.status),
				frappe.PermissionError,
			)

	def refresh_overdue_flag(self):
		"""Overdue when the install date is past and the order is not Installed/Closed/Cancelled."""
		if self.status in CLOSED_STATUSES or not self.expected_installation_date:
			self.is_overdue = 0
			return
		self.is_overdue = 1 if getdate(self.expected_installation_date) < getdate(nowdate()) else 0

	def enqueue_installed_processing(self):
		"""One RQ job per order. The form request must not create the Delivery Note."""
		if self.status != "Installed" or self.delivery_note or self.flags.get("in_installed_job"):
			return

		try:
			self.db_set("processing_status", "Queued", update_modified=False)
			frappe.enqueue(
				"reno_order.tasks.process_installed_order",
				reno_order=self.name,
				queue="default",
				enqueue_after_commit=True,
				deduplicate=True,
				job_id=f"reno-installed-{self.name}",
			)
		except Exception:
			self.db_set("processing_status", "Failed", update_modified=False)
			frappe.log_error(title=f"Reno Order enqueue failed: {self.name}")

	def calculate_totals(self):
		"""Always recompute financial fields. Client/API values are ignored."""
		total = 0.0
		for row in self.items:
			row.amount = flt(flt(row.qty) * flt(row.rate), self.precision("amount", row))
			total += flt(row.amount)

		self.total_amount = flt(total, self.precision("total_amount"))
		self.discount_amount = flt(
			self.total_amount * flt(self.discount_percentage) / 100.0,
			self.precision("discount_amount"),
		)
		self.grand_total = flt(self.total_amount - self.discount_amount, self.precision("grand_total"))

	def validate_discount_authority(self):
		"""Submit above Reno Settings threshold requires the approver role (or System Manager)."""
		settings = frappe.get_cached_doc("Reno Settings")
		threshold = flt(settings.discount_approval_threshold)
		approver_role = settings.discount_approver_role or "Sales Manager"

		if flt(self.discount_percentage) <= threshold:
			return

		roles = frappe.get_roles(frappe.session.user)
		if approver_role in roles or "System Manager" in roles:
			return

		frappe.throw(
			_(
				"Discount of {0}% exceeds the approval threshold of {1}%. Role {2} is required to submit."
			).format(
					flt(self.discount_percentage, 2),
					flt(threshold, 2),
					approver_role,
				),
				frappe.PermissionError,
			)

	def validate_no_submitted_downstream(self):
		"""Cancel in reverse of posting: SI → DN → SO → Reno Order."""
		blockers = []
		for doctype, name in (
			("Sales Invoice", self.get_existing_sales_invoice()),
			("Delivery Note", self.get_existing_delivery_note()),
			("Sales Order", self.get_existing_sales_order()),
		):
			if name and frappe.db.get_value(doctype, name, "docstatus") == 1:
				blockers.append(f"{doctype} {name}")
		for work_order in self.get_existing_work_orders():
			if frappe.db.get_value("Work Order", work_order, "docstatus") == 1:
				blockers.append(f"Work Order {work_order}")
		if blockers:
			frappe.throw(
				_("Cancel these submitted documents first: {0}").format(", ".join(blockers))
			)

	def get_existing_sales_order(self):
		return self._existing_open_link("Sales Order", self.sales_order)

	def get_existing_delivery_note(self):
		return self._existing_open_link("Delivery Note", self.delivery_note)

	def get_existing_sales_invoice(self):
		return self._existing_open_link("Sales Invoice", self.sales_invoice)

	def get_existing_material_request(self):
		return self._existing_open_link("Material Request", self.material_request)

	def get_existing_work_orders(self):
		if not frappe.get_meta("Work Order").has_field("reno_order"):
			return []
		return frappe.get_all(
			"Work Order",
			{"reno_order": self.name, "docstatus": ["<", 2]},
			pluck="name",
			order_by="creation",
		)

	def _existing_open_link(self, doctype, linked_name):
		"""Open (draft or submitted) document for this Reno Order; cancelled does not count."""
		if linked_name and frappe.db.exists(doctype, linked_name):
			if frappe.db.get_value(doctype, linked_name, "docstatus") < 2:
				return linked_name
		if frappe.get_meta(doctype).has_field("reno_order"):
			return frappe.db.get_value(doctype, {"reno_order": self.name, "docstatus": ["<", 2]}, "name")
		return None

	@frappe.whitelist()
	def create_sales_order(self):
		"""Create one standard ERPNext Sales Order from this Reno Order."""
		self.check_permission("submit")

		if self.docstatus != 1:
			frappe.throw(_("Submit the Reno Order before creating a Sales Order."))

		existing = self.get_existing_sales_order()
		if existing:
			frappe.throw(_("Sales Order {0} already exists for this Reno Order.").format(existing))

		if not frappe.has_permission("Sales Order", "create"):
			frappe.throw(_("You do not have permission to create a Sales Order."), frappe.PermissionError)

		sales_order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": self.customer,
				"company": self.company,
				"transaction_date": self.transaction_date,
				"delivery_date": self.expected_installation_date or self.transaction_date,
				"order_type": "Sales",
				"project": self.project,
				"customer_address": self.customer_address,
				"contact_person": self.contact_person,
				"currency": self.currency,
				"additional_discount_percentage": flt(self.discount_percentage),
				"reno_order": self.name,
				"items": [
					{
						"item_code": row.item_code,
						"item_name": frappe.get_cached_value("Item", row.item_code, "item_name"),
						"description": row.description,
						"qty": row.qty,
						"uom": row.uom,
						"rate": row.rate,
						"warehouse": row.warehouse,
						"delivery_date": self.expected_installation_date or self.transaction_date,
					}
					for row in self.items
				],
			}
		)

		if self.sales_person and frappe.db.exists("DocType", "Sales Team"):
			sales_order.append("sales_team", {"sales_person": self.sales_person, "allocated_percentage": 100})

		sales_order.flags.ignore_pricing_rule = True
		sales_order.insert()

		self.db_set("sales_order", sales_order.name)
		return sales_order.name

	@frappe.whitelist()
	def create_delivery_note(self):
		"""Create one standard Delivery Note from the linked submitted Sales Order."""
		self.check_permission("submit")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Reno Order before creating a Delivery Note."))

		existing = self.get_existing_delivery_note()
		if existing:
			frappe.throw(_("Delivery Note {0} already exists for this Reno Order.").format(existing))

		sales_order = self.get_existing_sales_order()
		if not sales_order or frappe.db.get_value("Sales Order", sales_order, "docstatus") != 1:
			frappe.throw(_("Submit the Sales Order before creating a Delivery Note."))

		if not frappe.has_permission("Delivery Note", "create"):
			frappe.throw(_("You do not have permission to create a Delivery Note."), frappe.PermissionError)

		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

		delivery_note = make_delivery_note(sales_order)
		if frappe.get_meta("Delivery Note").has_field("reno_order"):
			delivery_note.reno_order = self.name
		delivery_note.insert()
		self.db_set("delivery_note", delivery_note.name)
		return delivery_note.name

	@frappe.whitelist()
	def create_sales_invoice(self):
		"""Create one Sales Invoice from the submitted Delivery Note."""
		self.check_permission("submit")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Reno Order before creating a Sales Invoice."))

		existing = self.get_existing_sales_invoice()
		if existing:
			frappe.throw(_("Sales Invoice {0} already exists for this Reno Order.").format(existing))

		delivery_note = self.get_existing_delivery_note()
		if not delivery_note or frappe.db.get_value("Delivery Note", delivery_note, "docstatus") != 1:
			frappe.throw(_("Submit the Delivery Note before creating a Sales Invoice."))

		if not frappe.has_permission("Sales Invoice", "create"):
			frappe.throw(_("You do not have permission to create a Sales Invoice."), frappe.PermissionError)

		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

		sales_invoice = make_sales_invoice(delivery_note)
		if frappe.get_meta("Sales Invoice").has_field("reno_order"):
			sales_invoice.reno_order = self.name
		sales_invoice.insert()
		self.db_set("sales_invoice", sales_invoice.name)
		return sales_invoice.name

	@frappe.whitelist()
	def create_work_orders(self):
		"""Create one standard Work Order per Reno Order item that has a BOM."""
		self.check_permission("submit")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Reno Order before creating a Work Order."))

		if not frappe.has_permission("Work Order", "create"):
			frappe.throw(_("You do not have permission to create a Work Order."), frappe.PermissionError)

		from reno_order.manufacturing import get_default_bom, get_open_work_order
		from reno_order.utils import get_warehouse

		created = []
		already = []
		sales_order = self.get_existing_sales_order()
		so_submitted = sales_order and frappe.db.get_value("Sales Order", sales_order, "docstatus") == 1
		warehouses = {
			"source": get_warehouse(self.company),
			"wip": frappe.get_cached_value("Company", self.company, "default_wip_warehouse"),
			"fg": frappe.get_cached_value("Company", self.company, "default_fg_warehouse"),
		}

		for row in self.items:
			bom = get_default_bom(row.item_code)
			if not bom:
				continue

			existing = get_open_work_order(self.name, row.item_code)
			if existing:
				already.append(existing)
				continue

			work_order = frappe.get_doc(
				{
					"doctype": "Work Order",
					"production_item": row.item_code,
					"bom_no": bom,
					"qty": row.qty,
					"company": self.company,
					"reno_order": self.name,
					"project": self.project,
					"fg_warehouse": warehouses["fg"] or row.warehouse,
					"wip_warehouse": warehouses["wip"] or warehouses["source"],
					"source_warehouse": warehouses["source"],
					"transfer_material_against": "Work Order",
					"use_multi_level_bom": 0,
				}
			)
			if so_submitted:
				work_order.sales_order = sales_order
				work_order.sales_order_item = frappe.db.get_value(
					"Sales Order Item",
					{"parent": sales_order, "item_code": row.item_code},
					"name",
				)
			work_order.get_items_and_operations_from_bom()
			for required in work_order.get("required_items") or []:
				if not required.source_warehouse:
					required.source_warehouse = warehouses["source"]
			work_order.insert()
			created.append(work_order.name)

		if not created and not already:
			frappe.throw(_("No Reno Order item has an active default BOM."))
		if not created and already:
			frappe.throw(_("Work Order {0} already exists for this Reno Order.").format(", ".join(already)))
		return created

	@frappe.whitelist()
	def create_material_request(self):
		"""Create one Purchase Material Request for out-of-stock Reno Order items."""
		self.check_permission("submit")
		if self.docstatus != 1:
			frappe.throw(_("Submit the Reno Order before creating a Material Request."))

		existing = self.get_existing_material_request()
		if existing:
			frappe.throw(_("Material Request {0} already exists for this Reno Order.").format(existing))

		if not frappe.has_permission("Material Request", "create"):
			frappe.throw(_("You do not have permission to create a Material Request."), frappe.PermissionError)

		from reno_order.buying import get_shortfall_items

		short_items = get_shortfall_items(self)
		if not short_items:
			frappe.throw(_("Every Reno Order item either has a BOM or is already in stock."))

		material_request = frappe.get_doc(
			{
				"doctype": "Material Request",
				"material_request_type": "Purchase",
				"company": self.company,
				"transaction_date": self.transaction_date,
				"schedule_date": self.expected_installation_date or self.transaction_date,
				"reno_order": self.name,
				"items": short_items,
			}
		)
		material_request.insert()
		self.db_set("material_request", material_request.name)
		return material_request.name

	@frappe.whitelist()
	def sync_to_crm(self, force=1):
		"""Queue a CRM sync job. The form save / button does not wait for the API."""
		from reno_order.crm import enqueue_crm_sync

		self.check_permission("write")
		return enqueue_crm_sync(self.name, force=force)

	@frappe.whitelist()
	def mark_as_installed(self):
		"""Desk button. Server re-checks role, assignment and the Ready → Installed transition."""
		from reno_order.api import update_installation_status

		return update_installation_status(self.name, "Installed")


@frappe.whitelist()
def create_sales_order(name):
	doc = frappe.get_doc("Reno Order", name)
	return doc.create_sales_order()


@frappe.whitelist()
def create_delivery_note(name):
	doc = frappe.get_doc("Reno Order", name)
	return doc.create_delivery_note()


@frappe.whitelist()
def create_sales_invoice(name):
	doc = frappe.get_doc("Reno Order", name)
	return doc.create_sales_invoice()


@frappe.whitelist()
def create_work_orders(name):
	doc = frappe.get_doc("Reno Order", name)
	return doc.create_work_orders()


@frappe.whitelist()
def create_material_request(name):
	doc = frappe.get_doc("Reno Order", name)
	return doc.create_material_request()


def installed_delivery_note_name(reno_order: str) -> str:
	"""Stable name so a timed-out retry cannot insert a second Delivery Note."""
	return f"RDN-{reno_order}"


def _link_existing_delivery_note(doc, existing):
	doc.db_set(
		{
			"delivery_note": existing,
			"processing_status": "Completed",
		},
		update_modified=False,
	)
	return existing


def create_downstream_delivery_note(reno_order: str):
	"""Create at most one Delivery Note for an Installed Reno Order.

	Safe under timeout-and-retry and under two overlapping jobs: row lock, then a
	deterministic document name. The HTTP Mark-as-Installed request never waits
	for this work (see enqueue_installed_processing).
	"""
	frappe.db.sql("select name from `tabReno Order` where name=%s for update", reno_order)
	doc = frappe.get_doc("Reno Order", reno_order)
	if doc.status != "Installed":
		return

	existing = doc.get_existing_delivery_note()
	if existing:
		return _link_existing_delivery_note(doc, existing)

	proposed = installed_delivery_note_name(doc.name)
	if frappe.db.exists("Delivery Note", proposed):
		return _link_existing_delivery_note(doc, proposed)

	try:
		sales_order = doc.get_existing_sales_order()
		if not sales_order:
			doc.db_set("processing_status", "Awaiting Sales Order Submit", update_modified=False)
			return

		so_status = frappe.db.get_value("Sales Order", sales_order, "docstatus")
		if so_status != 1:
			doc.db_set("processing_status", "Awaiting Sales Order Submit", update_modified=False)
			return

		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

		doc.db_set("processing_status", "In Progress", update_modified=False)

		delivery_note = make_delivery_note(sales_order)
		if frappe.get_meta("Delivery Note").has_field("reno_order"):
			delivery_note.reno_order = doc.name
		# System job: the user who marked Installed may not have Stock rights.
		# set_name keeps retries on RDN-<reno_order> instead of the MAT-DN series.
		delivery_note.flags.ignore_permissions = True
		delivery_note.insert(ignore_permissions=True, set_name=proposed)
		return _link_existing_delivery_note(doc, delivery_note.name)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		existing = doc.get_existing_delivery_note() or (
			proposed if frappe.db.exists("Delivery Note", proposed) else None
		)
		if existing:
			return _link_existing_delivery_note(doc, existing)
		raise
	except Exception:
		doc.db_set("processing_status", "Failed", update_modified=False)
		frappe.log_error(title=f"Reno Order installed processing failed: {doc.name}")
		raise

