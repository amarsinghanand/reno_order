import frappe
from reno_order.utils import get_company, get_warehouse


FG_ITEM = "Kitchen Cabinet"
RM_ITEMS = (
	("Plywood", 2, 40),
	("Laminate", 2, 25),
	("Adhesive", 1, 15),
	("Hardware", 1, 30),
)
OPERATIONS = (
	("Cutting", "Cutting Bench", 30),
	("Assembly", "Assembly Bench", 45),
	("Finishing", "Finishing Booth", 20),
)


def ensure_manufacturing_masters():
	"""Idempotent Kitchen Cabinet BOM / operations / workstations for Point 4."""
	company = get_company()
	if not company:
		return

	warehouses = _ensure_company_warehouses(company)
	_ensure_items(company)
	_ensure_workstations()
	_ensure_operations()
	_ensure_bom(company)
	_relax_capacity_planning()
	return warehouses


def _ensure_company_warehouses(company):
	stores = get_warehouse() or frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	wip = frappe.db.get_value("Warehouse", {"company": company, "name": ("like", "Work In Progress%")}, "name") or stores
	fg = frappe.db.get_value("Warehouse", {"company": company, "name": ("like", "Finished Goods%")}, "name") or stores
	inventory_account = frappe.get_cached_value("Company", company, "default_inventory_account")
	if inventory_account:
		for warehouse in (stores, wip, fg):
			if warehouse and not frappe.db.get_value("Warehouse", warehouse, "account"):
				frappe.db.set_value("Warehouse", warehouse, "account", inventory_account)

	company_doc = frappe.get_doc("Company", company)
	changed = False
	if not company_doc.default_wip_warehouse:
		company_doc.default_wip_warehouse = wip
		changed = True
	if not company_doc.default_fg_warehouse:
		company_doc.default_fg_warehouse = fg
		changed = True
	if changed:
		company_doc.save(ignore_permissions=True)

	return {"stores": stores, "wip": wip, "fg": fg}


def _ensure_items(company):
	item_group = frappe.db.get_value("Item Group", {"is_group": 0}) or "Products"
	for item_code, _qty, rate in RM_ITEMS:
		_ensure_item(item_code, item_group, rate, manufactured=False)
	_ensure_item(FG_ITEM, item_group, 800, manufactured=True)
	expense_account = frappe.get_cached_value("Company", company, "default_expense_account")
	if expense_account:
		for item_code, _qty, _rate in (*RM_ITEMS, (FG_ITEM, 1, 800)):
			if not frappe.db.get_value("Item Default", {"parent": item_code, "company": company}, "expense_account"):
				item = frappe.get_doc("Item", item_code)
				if not any(d.company == company for d in item.get("item_defaults") or []):
					item.append("item_defaults", {"company": company, "expense_account": expense_account})
					item.save(ignore_permissions=True)


def _ensure_item(item_code, item_group, rate, manufactured):
	if frappe.db.exists("Item", item_code):
		return
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": item_group,
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"include_item_in_manufacturing": 1,
			"default_material_request_type": "Manufacture" if manufactured else "Purchase",
			"standard_rate": rate,
			"valuation_rate": rate,
		}
	).insert(ignore_permissions=True)


def _ensure_workstations():
	for _operation, workstation, minutes in OPERATIONS:
		if frappe.db.exists("Workstation", workstation):
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Workstation",
				"workstation_name": workstation,
				"production_capacity": 1,
				"hour_rate": 100,
				"working_hours": [{"start_time": "08:00:00", "end_time": "18:00:00"}],
			}
		)
		doc.insert(ignore_permissions=True)


def _ensure_operations():
	for operation, workstation, _minutes in OPERATIONS:
		if frappe.db.exists("Operation", operation):
			continue
		frappe.get_doc(
			{
				"doctype": "Operation",
				"name": operation,
				"workstation": workstation,
				"description": f"{operation} for kitchen cabinet production",
			}
		).insert(ignore_permissions=True)


def _ensure_bom(company):
	existing = frappe.db.get_value(
		"BOM",
		{"item": FG_ITEM, "is_active": 1, "is_default": 1, "docstatus": 1},
		"name",
	)
	if existing:
		return existing

	bom = frappe.get_doc(
		{
			"doctype": "BOM",
			"item": FG_ITEM,
			"quantity": 1,
			"company": company,
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"is_active": 1,
			"is_default": 1,
			"with_operations": 1,
			"transfer_material_against": "Work Order",
			"items": [{"item_code": item_code, "qty": qty} for item_code, qty, _rate in RM_ITEMS],
			"operations": [
				{
					"operation": operation,
					"workstation": workstation,
					"time_in_mins": minutes,
					"hour_rate": 100,
				}
				for operation, workstation, minutes in OPERATIONS
			],
		}
	)
	bom.insert(ignore_permissions=True)
	bom.submit()
	return bom.name


def _relax_capacity_planning():
	"""Job Cards should create without a full workstation calendar on this demo site."""
	if not frappe.db.exists("DocType", "Manufacturing Settings"):
		return
	if frappe.db.get_single_value("Manufacturing Settings", "disable_capacity_planning"):
		return
	settings = frappe.get_single("Manufacturing Settings")
	settings.disable_capacity_planning = 1
	settings.save(ignore_permissions=True)


def get_default_bom(item_code):
	return frappe.db.get_value(
		"BOM",
		{"item": item_code, "is_active": 1, "is_default": 1, "docstatus": 1},
		"name",
	)


def get_open_work_order(reno_order, item_code):
	return frappe.db.get_value(
		"Work Order",
		{"reno_order": reno_order, "production_item": item_code, "docstatus": ["<", 2]},
		"name",
	)
