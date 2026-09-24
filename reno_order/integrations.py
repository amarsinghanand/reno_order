import frappe
from frappe import _


DOWNSTREAM_LINK_FIELDS = {
	"Sales Order": "sales_order",
	"Delivery Note": "delivery_note",
	"Sales Invoice": "sales_invoice",
	"Material Request": "material_request",
}


def inherit_reno_order(doc, method=None):
	"""Copy Reno Order from the previous selling document when ERPNext Create is used."""
	if doc.get("reno_order") or not frappe.get_meta(doc.doctype).has_field("reno_order"):
		return

	source_doctype, source_name = _source_document(doc)
	if not source_name or not frappe.get_meta(source_doctype).has_field("reno_order"):
		return

	reno_order = frappe.db.get_value(source_doctype, source_name, "reno_order")
	if reno_order:
		doc.reno_order = reno_order


def link_downstream_to_reno_order(doc, method=None):
	"""Keep Reno Order.sales_order / delivery_note / sales_invoice in sync."""
	reno_order = doc.get("reno_order")
	fieldname = DOWNSTREAM_LINK_FIELDS.get(doc.doctype)
	if not reno_order or not fieldname or not frappe.db.exists("Reno Order", reno_order):
		return

	current = frappe.db.get_value("Reno Order", reno_order, fieldname)
	if current and current != doc.name and _is_open(doc.doctype, current):
		return

	frappe.db.set_value("Reno Order", reno_order, fieldname, doc.name, update_modified=False)


def _source_document(doc):
	if doc.doctype == "Delivery Note":
		name = _first_item_value(doc, "against_sales_order")
		return "Sales Order", name
	if doc.doctype == "Sales Invoice":
		delivery_note = doc.get("delivery_note") or _first_item_value(doc, "delivery_note")
		if delivery_note:
			return "Delivery Note", delivery_note
		sales_order = _first_item_value(doc, "sales_order")
		return "Sales Order", sales_order
	if doc.doctype == "Work Order":
		return "Sales Order", doc.get("sales_order")
	if doc.doctype == "Job Card":
		return "Work Order", doc.get("work_order")
	if doc.doctype == "Stock Entry":
		return "Work Order", doc.get("work_order")
	if doc.doctype == "Request for Quotation":
		return "Material Request", _first_item_value(doc, "material_request")
	if doc.doctype == "Supplier Quotation":
		return _first_buying_source(
			doc,
			(
				("Request for Quotation", "request_for_quotation"),
				("Material Request", "material_request"),
			),
		)
	if doc.doctype == "Purchase Order":
		return _first_buying_source(
			doc,
			(
				("Supplier Quotation", "supplier_quotation"),
				("Material Request", "material_request"),
			),
		)
	if doc.doctype == "Purchase Receipt":
		return "Purchase Order", doc.get("purchase_order") or _first_item_value(doc, "purchase_order")
	if doc.doctype == "Purchase Invoice":
		return _first_buying_source(
			doc,
			(
				("Purchase Receipt", "purchase_receipt"),
				("Purchase Order", "purchase_order"),
			),
		)
	return None, None


def _first_buying_source(doc, candidates):
	for doctype, fieldname in candidates:
		name = doc.get(fieldname) or _first_item_value(doc, fieldname)
		if name:
			return doctype, name
	return None, None


def _first_item_value(doc, fieldname):
	for row in doc.get("items") or []:
		if row.get(fieldname):
			return row.get(fieldname)
	return None


def _is_open(doctype, name):
	return bool(name) and frappe.db.exists(doctype, name) and frappe.db.get_value(doctype, name, "docstatus") < 2
