import frappe
from frappe.utils import flt, today

from reno_order.manufacturing import get_default_bom
from reno_order.utils import get_company, get_warehouse


HARDWARE_ITEM = "Hardware"
SUPPLIER = "Reno Hardware Supplier"


def ensure_buying_masters():
	"""Supplier + Hardware item used by the Point 5 procurement demo."""
	from reno_order.manufacturing import ensure_manufacturing_masters

	ensure_manufacturing_masters()
	company = get_company()
	if not frappe.db.exists("Supplier", SUPPLIER):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": SUPPLIER,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0})
				or frappe.db.get_value("Supplier Group", {})
				or "All Supplier Groups",
				"supplier_type": "Company",
				"country": frappe.db.get_single_value("System Settings", "country") or "India",
			}
		).insert(ignore_permissions=True)
	return {"supplier": SUPPLIER, "item": HARDWARE_ITEM, "company": company}


def get_shortfall_items(doc):
	"""Reno Order stock lines that have no BOM and not enough warehouse qty."""
	from erpnext.stock.utils import get_stock_balance

	short_items = []
	for row in doc.items:
		if get_default_bom(row.item_code):
			continue
		if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		warehouse = row.warehouse or get_warehouse(doc.company)
		balance = flt(get_stock_balance(row.item_code, warehouse, today()))
		short_qty = flt(row.qty) - balance
		if short_qty > 0:
			short_items.append(
				{
					"item_code": row.item_code,
					"qty": short_qty,
					"warehouse": warehouse,
					"uom": row.uom or "Nos",
					"rate": row.rate,
					"schedule_date": doc.expected_installation_date or today(),
				}
			)
	return short_items
