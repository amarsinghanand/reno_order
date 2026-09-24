import frappe

from reno_order.reno_order.doctype.reno_order.test_reno_order import ensure_masters, make_reno_order


def run():
	ensure_masters()
	settings = frappe.get_single("Reno Settings")
	print("settings", settings.discount_approval_threshold, settings.discount_approver_role)
	print("so custom field", frappe.get_meta("Sales Order").has_field("reno_order"))

	doc = make_reno_order(qty=2, rate=150, discount_percentage=5, save=True)
	print("saved", doc.name, doc.total_amount, doc.discount_amount, doc.grand_total, doc.status)
	doc.submit()
	print("submitted", doc.status, doc.docstatus)
	so = doc.create_sales_order()
	print("so created", so)
	doc.reload()
	print("linked", doc.sales_order)
	try:
		doc.create_sales_order()
		print("DUPLICATE FAIL")
	except Exception as e:
		print("duplicate blocked:", str(e).splitlines()[0])

	user = "reno.sales@example.com"
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": "Reno",
				"last_name": "Sales",
				"send_welcome_email": 0,
				"roles": [{"role": "Sales User"}],
			}
		).insert(ignore_permissions=True)

	high = make_reno_order(qty=1, rate=100, discount_percentage=25, save=True)
	ok = make_reno_order(qty=1, rate=80, discount_percentage=5, save=True)
	frappe.set_user(user)
	try:
		ok.submit()
		print("sales user submitted within threshold:", ok.name)
	except Exception as e:
		print("sales user within-threshold submit failed:", type(e).__name__, str(e)[:200])
	try:
		high.submit()
		print("DISCOUNT GATE FAIL")
	except Exception as e:
		print("discount blocked for Sales User:", type(e).__name__, (str(e) or "PermissionError")[:200])
	finally:
		frappe.set_user("Administrator")

	high.reload()
	high.submit()
	print("discount allowed for Administrator:", high.name, high.docstatus)
	return "ok"
