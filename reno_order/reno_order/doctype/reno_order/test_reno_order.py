import frappe
from frappe.tests import UnitTestCase
from frappe.utils import add_days, flt, today

from reno_order.utils import get_company, get_warehouse


class TestRenoOrder(UnitTestCase):
	def test_totals_are_recalculated_on_save(self):
		doc = make_reno_order(qty=2, rate=100, discount_percentage=10, save=True)
		self.assertEqual(doc.total_amount, 200)
		self.assertEqual(doc.discount_amount, 20)
		self.assertEqual(doc.grand_total, 180)
		self.assertEqual(doc.items[0].amount, 200)

	def test_client_tampered_totals_are_ignored(self):
		doc = make_reno_order(qty=1, rate=50, save=False)
		doc.total_amount = 9999
		doc.discount_amount = 1
		doc.grand_total = 1
		doc.items[0].amount = 1
		doc.insert()
		self.assertEqual(doc.total_amount, 50)
		self.assertEqual(doc.discount_amount, 0)
		self.assertEqual(doc.grand_total, 50)
		self.assertEqual(doc.items[0].amount, 50)

	def test_negative_quantity_is_rejected(self):
		doc = make_reno_order(qty=-1, rate=10, save=False)
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_installation_date_before_order_date_is_rejected(self):
		doc = make_reno_order(save=False)
		doc.transaction_date = today()
		doc.expected_installation_date = add_days(today(), -1)
		self.assertRaises(frappe.ValidationError, doc.insert)

	def test_sales_user_cannot_submit_discount_above_threshold(self):
		from unittest.mock import patch

		sales = ensure_test_sales_user()
		set_discount_settings(threshold=10)
		doc = make_reno_order(discount_percentage=25, save=True)
		frappe.db.set_value("Reno Order", doc.name, "owner", sales, update_modified=False)
		frappe.set_user(sales)
		try:
			doc = frappe.get_doc("Reno Order", doc.name)
			with patch("reno_order.crm.frappe.enqueue"):
				self.assertRaises(frappe.PermissionError, doc.submit)
		finally:
			frappe.set_user("Administrator")

	def test_sales_user_can_submit_discount_within_threshold(self):
		from unittest.mock import patch

		sales = ensure_test_sales_user()
		set_discount_settings(threshold=10)
		doc = make_reno_order(discount_percentage=10, save=True)
		frappe.db.set_value("Reno Order", doc.name, "owner", sales, update_modified=False)
		frappe.set_user(sales)
		try:
			doc = frappe.get_doc("Reno Order", doc.name)
			with patch("reno_order.crm.frappe.enqueue"):
				doc.submit()
			self.assertEqual(doc.docstatus, 1)
			self.assertEqual(doc.status, "Confirmed")
		finally:
			frappe.set_user("Administrator")

	def test_approver_can_submit_discount_above_threshold(self):
		from unittest.mock import patch

		set_discount_settings(threshold=10)
		doc = make_reno_order(discount_percentage=25, save=True)
		with patch("reno_order.crm.frappe.enqueue"):
			doc.submit()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(flt(doc.discount_percentage), 25)

	def test_sales_order_is_created_from_reno_order(self):
		doc = make_reno_order(qty=3, rate=80, discount_percentage=10, save=True)
		self.assertRaises(frappe.ValidationError, doc.create_sales_order)
		doc.submit()
		so_name = doc.create_sales_order()
		so = frappe.get_doc("Sales Order", so_name)
		self.assertEqual(so.reno_order, doc.name)
		self.assertEqual(so.customer, doc.customer)
		self.assertEqual(so.company, doc.company)
		self.assertEqual(so.items[0].item_code, doc.items[0].item_code)
		self.assertEqual(flt(so.items[0].qty), 3)
		self.assertEqual(flt(so.items[0].rate), 80)
		doc.reload()
		self.assertEqual(doc.sales_order, so_name)

	def test_invalid_status_jump_is_rejected(self):
		doc = make_reno_order(save=True)
		doc.status = "Installed"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_overdue_installations_are_flagged(self):
		from reno_order.tasks import flag_overdue_installations

		doc = make_reno_order(save=True)
		doc.submit()
		frappe.db.set_value(
			"Reno Order",
			doc.name,
			{
				"expected_installation_date": add_days(today(), -2),
				"is_overdue": 0,
			},
			update_modified=False,
		)
		flag_overdue_installations()
		self.assertEqual(frappe.db.get_value("Reno Order", doc.name, "is_overdue"), 1)

	def test_installed_processing_is_idempotent_without_submitted_so(self):
		from reno_order.reno_order.doctype.reno_order.reno_order import create_downstream_delivery_note

		doc = make_reno_order(save=True)
		doc.submit()
		frappe.db.set_value("Reno Order", doc.name, "status", "Installed", update_modified=False)
		create_downstream_delivery_note(doc.name)
		doc.reload()
		self.assertEqual(doc.processing_status, "Awaiting Sales Order Submit")
		self.assertFalse(doc.delivery_note)
		create_downstream_delivery_note(doc.name)
		doc.reload()
		self.assertFalse(doc.delivery_note)

	def test_duplicate_sales_order_is_blocked(self):
		doc = make_reno_order(save=True)
		doc.submit()
		first = doc.create_sales_order()
		self.assertTrue(first)
		self.assertRaises(frappe.ValidationError, doc.create_sales_order)

	def test_create_sales_invoice_requires_submitted_delivery_note(self):
		doc = make_reno_order(save=True)
		doc.submit()
		self.assertRaises(frappe.ValidationError, doc.create_sales_invoice)

	def test_duplicate_sales_invoice_is_blocked(self):
		doc = make_reno_order(save=True)
		doc.submit()
		doc.get_existing_sales_invoice = lambda: "SI-ALREADY-EXISTS"
		self.assertRaises(frappe.ValidationError, doc.create_sales_invoice)

	def test_work_order_requires_a_bom_item(self):
		doc = make_reno_order(save=True)
		doc.submit()
		self.assertRaises(frappe.ValidationError, doc.create_work_orders)

	def test_work_order_is_created_once_for_kitchen_cabinet(self):
		from reno_order.manufacturing import FG_ITEM, ensure_manufacturing_masters

		ensure_manufacturing_masters()
		doc = make_reno_order(qty=1, rate=800, item_code=FG_ITEM, save=True)
		doc.submit()
		created = doc.create_work_orders()
		self.assertEqual(len(created), 1)
		wo = frappe.get_doc("Work Order", created[0])
		self.assertEqual(wo.reno_order, doc.name)
		self.assertEqual(wo.production_item, FG_ITEM)
		self.assertTrue(wo.operations)
		self.assertRaises(frappe.ValidationError, doc.create_work_orders)

	def test_material_request_skips_manufactured_items(self):
		from reno_order.manufacturing import FG_ITEM, ensure_manufacturing_masters

		ensure_manufacturing_masters()
		doc = make_reno_order(qty=1, rate=800, item_code=FG_ITEM, save=True)
		doc.submit()
		self.assertRaises(frappe.ValidationError, doc.create_material_request)

	def test_material_request_is_created_once_for_short_hardware(self):
		from erpnext.stock.utils import get_stock_balance
		from frappe.utils import today as nowdate

		from reno_order.buying import HARDWARE_ITEM, ensure_buying_masters
		from reno_order.utils import get_warehouse

		ensure_buying_masters()
		warehouse = get_warehouse()
		on_hand = flt(get_stock_balance(HARDWARE_ITEM, warehouse, nowdate()))
		doc = make_reno_order(qty=on_hand + 2, rate=30, item_code=HARDWARE_ITEM, save=True)
		doc.submit()
		mr_name = doc.create_material_request()
		mr = frappe.get_doc("Material Request", mr_name)
		self.assertEqual(mr.reno_order, doc.name)
		self.assertEqual(mr.material_request_type, "Purchase")
		self.assertEqual(mr.items[0].item_code, HARDWARE_ITEM)
		self.assertRaises(frappe.ValidationError, doc.create_material_request)

	def test_api_requires_authentication(self):
		from reno_order.api import update_installation_status

		doc = make_reno_order(save=True)
		doc.submit()
		frappe.set_user("Guest")
		try:
			self.assertRaises(
				(frappe.AuthenticationError, frappe.PermissionError),
				update_installation_status,
				doc.name,
				"Installed",
			)
		finally:
			frappe.set_user("Administrator")

	def test_api_rejects_invalid_status_jump(self):
		from reno_order.api import update_installation_status

		doc = make_reno_order(save=True)
		doc.submit()
		self.assertRaises(frappe.ValidationError, update_installation_status, doc.name, "Installed")

	def test_api_rejects_unassigned_supervisor(self):
		from reno_order.api import update_installation_status

		supervisor = ensure_test_supervisor()
		doc = make_reno_order(save=True)
		doc.db_set("assigned_to", "Administrator")
		doc.submit()
		frappe.db.set_value("Reno Order", doc.name, "status", "Ready for Installation", update_modified=False)
		frappe.set_user(supervisor)
		try:
			self.assertRaises(frappe.PermissionError, update_installation_status, doc.name, "Installed")
		finally:
			frappe.set_user("Administrator")

	def test_api_marks_installed_and_adds_remarks(self):
		from reno_order.api import add_installation_remarks, update_installation_status

		supervisor = ensure_test_supervisor()
		doc = make_reno_order(save=True)
		doc.db_set("assigned_to", supervisor)
		doc.submit()
		frappe.db.set_value("Reno Order", doc.name, "status", "Ready for Installation", update_modified=False)
		frappe.set_user(supervisor)
		try:
			result = update_installation_status(doc.name, "Installed")
			self.assertEqual(result["status"], "Installed")
			remarks = add_installation_remarks(doc.name, "Installation completed successfully.")
			self.assertIn("Installation completed successfully.", remarks["remarks"])
		finally:
			frappe.set_user("Administrator")

	def test_api_attaches_site_photo(self):
		from reno_order.api import attach_site_photo, get_installation

		doc = make_reno_order(save=True)
		doc.submit()
		frappe.form_dict["filedata"] = b"\x89PNG\r\n\x1a\n"
		try:
			attached = attach_site_photo(doc.name, filename="site.png")
		finally:
			frappe.form_dict.pop("filedata", None)
		self.assertTrue(attached["file_url"])
		info = get_installation(doc.name)
		self.assertTrue(info["photos"])

	def test_crm_submit_enqueues_long_queue_without_calling_api(self):
		from unittest.mock import patch

		from reno_order import crm
		from reno_order.crm import CRM_JOB_TIMEOUT, CRM_QUEUE, ensure_crm_defaults

		ensure_crm_defaults()
		doc = make_reno_order(save=True)
		with patch("reno_order.crm.frappe.enqueue") as enqueued, patch.object(crm, "_transport_post") as post:
			doc.submit()
		doc.reload()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.crm_sync_status, "Queued")
		post.assert_not_called()
		enqueued.assert_called_once()
		kwargs = enqueued.call_args.kwargs
		self.assertEqual(kwargs["queue"], CRM_QUEUE)
		self.assertEqual(kwargs["queue"], "long")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["job_id"], f"reno-crm-sync-{doc.name}")
		self.assertGreaterEqual(kwargs["timeout"], CRM_JOB_TIMEOUT)
		self.assertTrue(kwargs["enqueue_after_commit"])

	def test_crm_skips_duplicate_enqueue(self):
		from unittest.mock import patch

		from reno_order.crm import enqueue_crm_sync, ensure_crm_defaults

		ensure_crm_defaults()
		doc = make_reno_order(save=True)
		with patch("reno_order.crm.frappe.enqueue"):
			doc.submit()
		with (
			patch("reno_order.crm.is_job_enqueued", return_value=True),
			patch("reno_order.crm.frappe.enqueue") as enqueued,
		):
			result = enqueue_crm_sync(doc.name)
		enqueued.assert_not_called()
		self.assertTrue(result["duplicate"])
		self.assertEqual(result["status"], "Queued")

	def test_crm_job_requeues_retryable_failure(self):
		from unittest.mock import patch

		from reno_order.crm import ensure_crm_defaults, run_crm_sync_job

		ensure_crm_defaults()
		doc = make_reno_order(save=True)
		with patch("reno_order.crm.frappe.enqueue"):
			doc.submit()
		with (
			patch(
				"reno_order.crm.sync_reno_order_to_crm",
				return_value={"status": "Failed", "error": "CRM request timed out"},
			),
			patch("reno_order.crm.frappe.enqueue") as enqueued,
		):
			run_crm_sync_job(doc.name, attempt=1)
		enqueued.assert_called_once()
		self.assertEqual(enqueued.call_args.kwargs["attempt"], 2)
		self.assertEqual(enqueued.call_args.kwargs["job_id"], f"reno-crm-sync-{doc.name}:2")

	def test_crm_syncs_on_submit(self):
		from unittest.mock import patch

		from reno_order.crm import ensure_crm_defaults, run_crm_sync_job

		ensure_crm_defaults()
		doc = make_reno_order(save=True)
		with patch("reno_order.crm.frappe.enqueue"):
			doc.submit()
		doc.reload()
		self.assertEqual(doc.crm_sync_status, "Queued")
		run_crm_sync_job(doc.name)
		doc.reload()
		self.assertEqual(doc.crm_sync_status, "Synced")
		self.assertEqual(doc.crm_id, f"CRM-{doc.name}")
		log = frappe.get_all(
			"Integration Request",
			{"reference_doctype": "Reno Order", "reference_docname": doc.name},
			["request_headers", "status"],
		)
		self.assertTrue(log)
		self.assertEqual(log[0].status, "Completed")
		self.assertIn("Bearer ***", log[0].request_headers)
		self.assertNotIn(
			frappe.get_single("Reno Settings").get_password("crm_api_token"),
			log[0].request_headers,
		)

	def test_crm_disabled_skips_sync(self):
		settings = frappe.get_single("Reno Settings")
		settings.crm_enabled = 0
		settings.save(ignore_permissions=True)
		frappe.clear_cache()
		try:
			doc = make_reno_order(save=True)
			doc.submit()
			doc.reload()
			self.assertNotEqual(doc.crm_sync_status, "Synced")
			self.assertNotEqual(doc.crm_sync_status, "Queued")
			self.assertFalse(doc.crm_id)
		finally:
			settings.crm_enabled = 1
			settings.save(ignore_permissions=True)
			frappe.clear_cache()

	def test_crm_retries_then_succeeds(self):
		from unittest.mock import patch

		from reno_order import crm
		from reno_order.crm import CRMRetryableError, ensure_crm_defaults, run_crm_sync_job

		ensure_crm_defaults()
		settings = frappe.get_single("Reno Settings")
		settings.crm_max_retries = 2
		settings.save(ignore_permissions=True)
		frappe.clear_cache()
		calls = {"n": 0}

		def flaky(url, headers, payload, timeout):
			calls["n"] += 1
			if calls["n"] < 3:
				raise CRMRetryableError("CRM returned HTTP 503")
			return {"crm_id": f"CRM-RETRY-{payload['reno_order']}", "status": "accepted"}

		doc = make_reno_order(save=True)
		with patch("reno_order.crm.frappe.enqueue"):
			doc.submit()
		with patch.object(crm, "_transport_post", side_effect=flaky), patch.object(crm, "sleep", return_value=None):
			run_crm_sync_job(doc.name)
		doc.reload()
		self.assertEqual(calls["n"], 3)
		self.assertEqual(doc.crm_sync_status, "Synced")
		self.assertTrue(doc.crm_id.startswith("CRM-RETRY-"))

	def test_crm_timeout_is_retried(self):
		from unittest.mock import patch

		from reno_order import crm
		from reno_order.crm import CRM_JOB_MAX_ATTEMPTS, CRMTimeoutError, ensure_crm_defaults, run_crm_sync_job

		ensure_crm_defaults()
		doc = make_reno_order(save=True)
		with patch("reno_order.crm.frappe.enqueue"):
			doc.submit()
		with (
			patch.object(crm, "_transport_post", side_effect=CRMTimeoutError("CRM request timed out")),
			patch.object(crm, "sleep", return_value=None),
			patch("reno_order.crm.frappe.enqueue"),
		):
			run_crm_sync_job(doc.name, attempt=CRM_JOB_MAX_ATTEMPTS)
		doc.reload()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.crm_sync_status, "Failed")
		self.assertIn("timed out", doc.crm_last_error)

	def test_crm_webhook_rejects_bad_signature(self):
		from reno_order.crm import receive_crm_webhook

		doc = make_reno_order(save=True)
		doc.submit()
		body = frappe.as_json({"reno_order": doc.name, "event": "updated"})
		frappe.local.request = type(
			"Req",
			(),
			{"headers": {"X-Reno-Signature": "sha256=deadbeef"}, "get_data": lambda self, as_text=True: body},
		)()
		try:
			self.assertRaises(frappe.AuthenticationError, receive_crm_webhook)
		finally:
			frappe.local.request = None

	def test_crm_webhook_updates_order(self):
		from reno_order.crm import ensure_crm_defaults, receive_crm_webhook, webhook_signature

		ensure_crm_defaults()
		doc = make_reno_order(save=True)
		doc.submit()
		body = frappe.as_json({"reno_order": doc.name, "crm_id": "CRM-WEBHOOK", "comment": "Lead updated"})
		frappe.local.request = type(
			"Req",
			(),
			{
				"headers": {"X-Reno-Signature": webhook_signature(body)},
				"get_data": lambda self, as_text=True: body,
			},
		)()
		try:
			result = receive_crm_webhook()
		finally:
			frappe.local.request = None
		self.assertEqual(result["status"], "accepted")
		doc.reload()
		self.assertEqual(doc.crm_id, "CRM-WEBHOOK")

	def test_order_type_patch_backfills_only_blanks(self):
		from reno_order.patches.v1_0.backfill_reno_order_type import backfill_order_type

		blank = make_reno_order(save=True)
		whitespace = make_reno_order(save=True)
		custom = make_reno_order(save=True)
		premium = make_reno_order(save=True)
		frappe.db.set_value("Reno Order", blank.name, "order_type", "", update_modified=False)
		frappe.db.set_value("Reno Order", whitespace.name, "order_type", "   ", update_modified=False)
		frappe.db.set_value("Reno Order", custom.name, "order_type", "Custom", update_modified=False)
		frappe.db.set_value("Reno Order", premium.name, "order_type", "Premium", update_modified=False)

		first = backfill_order_type(batch_size=1)
		self.assertGreaterEqual(first["updated"], 2)
		self.assertEqual(frappe.db.get_value("Reno Order", blank.name, "order_type"), "Standard")
		self.assertEqual(frappe.db.get_value("Reno Order", whitespace.name, "order_type"), "Standard")
		self.assertEqual(frappe.db.get_value("Reno Order", custom.name, "order_type"), "Custom")
		self.assertEqual(frappe.db.get_value("Reno Order", premium.name, "order_type"), "Premium")

		second = backfill_order_type(batch_size=1)
		self.assertEqual(second["updated"], 0)
		self.assertEqual(second["remaining"], 0)
		self.assertEqual(frappe.db.get_value("Reno Order", custom.name, "order_type"), "Custom")

	def test_monthly_value_report_groups_by_month_and_status(self):
		from reno_order.reno_order.report.monthly_reno_order_value.monthly_reno_order_value import execute

		doc = make_reno_order(qty=2, rate=50, save=True)
		columns, data, _message, chart = execute(
			{"from_date": add_days(today(), -1), "to_date": today(), "company": doc.company}
		)
		self.assertTrue(columns)
		self.assertTrue(data)
		self.assertTrue(any(int(row.order_count) >= 1 for row in data))
		self.assertTrue(any(row.status == "Draft" for row in data))
		self.assertTrue(chart)

	def test_monthly_value_index_exists(self):
		from reno_order.reporting import ensure_date_status_index, has_date_status_index

		ensure_date_status_index()
		self.assertTrue(has_date_status_index())

	def test_monthly_value_explain_without_index_is_full_scan(self):
		from reno_order.reporting import explain_monthly_value

		plan = explain_monthly_value(ignore_index=True)
		self.assertEqual(plan[0].get("type"), "ALL")
		self.assertFalse(plan[0].get("key"))

	def test_sales_user_sees_own_and_assigned_orders_only(self):
		sales = ensure_test_sales_user()
		hidden = make_reno_order(save=True)
		own = make_reno_order(save=True)
		assigned = make_reno_order(save=True)
		frappe.db.set_value("Reno Order", own.name, "owner", sales, update_modified=False)
		frappe.db.set_value("Reno Order", assigned.name, "assigned_to", sales, update_modified=False)
		frappe.set_user(sales)
		try:
			names = {row.name for row in frappe.get_list("Reno Order", fields=["name"], limit_page_length=500)}
			self.assertIn(own.name, names)
			self.assertIn(assigned.name, names)
			self.assertNotIn(hidden.name, names)
		finally:
			frappe.set_user("Administrator")

	def test_site_supervisor_sees_assigned_orders_only(self):
		supervisor = ensure_test_supervisor()
		hidden = make_reno_order(save=True)
		assigned = make_reno_order(save=True)
		assigned.db_set("assigned_to", supervisor)
		frappe.set_user(supervisor)
		try:
			names = {row.name for row in frappe.get_list("Reno Order", fields=["name"], limit_page_length=500)}
			self.assertIn(assigned.name, names)
			self.assertNotIn(hidden.name, names)
		finally:
			frappe.set_user("Administrator")

	def test_site_supervisor_cannot_change_selling_fields(self):
		from reno_order.permissions import validate_site_supervisor_selling_fields

		supervisor = ensure_test_supervisor()
		doc = make_reno_order(save=True)
		doc.db_set("assigned_to", supervisor)
		doc.submit()
		original_rate = flt(doc.items[0].rate)
		original_discount = flt(doc.discount_percentage)
		original_total = flt(doc.grand_total)
		frappe.set_user(supervisor)
		try:
			doc.reload()
			doc.discount_percentage = 40
			self.assertRaises(frappe.PermissionError, validate_site_supervisor_selling_fields, doc)
			try:
				doc.save()
			except frappe.PermissionError:
				pass
			doc.reload()
			self.assertEqual(flt(doc.discount_percentage), original_discount)

			doc.items[0].rate = 999
			self.assertRaises(frappe.PermissionError, validate_site_supervisor_selling_fields, doc)
			try:
				doc.save()
			except frappe.PermissionError:
				pass
			doc.reload()
			self.assertEqual(flt(doc.items[0].rate), original_rate)

			doc.grand_total = 1
			self.assertRaises(frappe.PermissionError, validate_site_supervisor_selling_fields, doc)
			doc.reload()
			self.assertEqual(flt(doc.grand_total), original_total)

			doc.installation_remarks = "Installation in progress."
			doc.save()
			doc.reload()
			self.assertIn("Installation in progress.", doc.installation_remarks)
		finally:
			frappe.set_user("Administrator")

	def test_mark_as_installed_requires_ready_status(self):
		doc = make_reno_order(save=True)
		doc.submit()
		self.assertRaises(frappe.ValidationError, doc.mark_as_installed)

	def test_mark_as_installed_from_ready(self):
		from unittest.mock import patch

		doc = make_reno_order(save=True)
		doc.submit()
		frappe.db.set_value("Reno Order", doc.name, "status", "Ready for Installation", update_modified=False)
		doc.reload()
		with patch("reno_order.crm.frappe.enqueue"):
			result = doc.mark_as_installed()
		self.assertEqual(result["status"], "Installed")
		self.assertFalse(result["already_installed"])
		doc.reload()
		self.assertEqual(doc.status, "Installed")

	def test_mark_as_installed_retry_after_timeout_is_idempotent(self):
		from unittest.mock import patch

		doc = make_reno_order(save=True)
		doc.submit()
		frappe.db.set_value("Reno Order", doc.name, "status", "Ready for Installation", update_modified=False)
		doc.reload()
		with patch("reno_order.crm.frappe.enqueue"):
			first = doc.mark_as_installed()
			doc.reload()
			second = doc.mark_as_installed()
		self.assertEqual(first["status"], "Installed")
		self.assertEqual(second["status"], "Installed")
		self.assertTrue(second["already_installed"])

	def test_installed_job_creates_only_one_delivery_note_on_retry(self):
		from reno_order.reno_order.doctype.reno_order.reno_order import (
			create_downstream_delivery_note,
			installed_delivery_note_name,
		)

		doc = make_reno_order(save=True)
		doc.submit()
		so_name = doc.create_sales_order()
		frappe.db.set_value("Sales Order", so_name, "docstatus", 1, update_modified=False)
		frappe.db.set_value("Reno Order", doc.name, "status", "Installed", update_modified=False)

		first = create_downstream_delivery_note(doc.name)
		second = create_downstream_delivery_note(doc.name)
		self.assertTrue(first)
		self.assertEqual(first, second)
		self.assertEqual(first, installed_delivery_note_name(doc.name))
		self.assertEqual(
			frappe.db.count("Delivery Note", {"reno_order": doc.name, "docstatus": ["<", 2]}),
			1,
		)
		doc.reload()
		self.assertEqual(doc.delivery_note, first)
		self.assertEqual(doc.processing_status, "Completed")

	def test_supervisor_user_query_returns_supervisors(self):
		from reno_order.permissions import supervisor_user_query

		supervisor = ensure_test_supervisor()
		rows = supervisor_user_query("User", "reno.supervisor", "name", 0, 20, {})
		names = {row[0] for row in rows}
		self.assertIn(supervisor, names)

	def test_cancel_is_blocked_when_sales_order_is_submitted(self):
		doc = make_reno_order(save=True)
		doc.submit()
		so_name = doc.create_sales_order()
		frappe.db.set_value("Sales Order", so_name, "docstatus", 1, update_modified=False)
		doc.reload()
		self.assertRaises(frappe.ValidationError, doc.cancel)


def make_reno_order(qty=1, rate=100, discount_percentage=0, save=True, item_code="Reno Test Item"):
	ensure_masters()
	doc = frappe.get_doc(
		{
			"doctype": "Reno Order",
			"customer": "Reno Test Customer",
			"company": get_company(),
			"transaction_date": today(),
			"expected_installation_date": today(),
			"order_type": "Standard",
			"status": "Draft",
			"discount_percentage": discount_percentage,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"uom": "Nos",
					"rate": rate,
					"warehouse": get_warehouse(),
				}
			],
		}
	)
	if save:
		doc.insert()
	return doc


def ensure_masters():
	company = get_company()
	if not frappe.db.exists("Customer", "Reno Test Customer"):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Reno Test Customer",
				"customer_type": "Company",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}) or "Commercial",
				"territory": frappe.db.get_value("Territory", {"is_group": 0}) or "India",
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Item", "Reno Test Item"):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "Reno Test Item",
				"item_name": "Reno Test Item",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}) or "Products",
				"stock_uom": "Nos",
				"is_stock_item": 1,
				"include_item_in_manufacturing": 0,
				"standard_rate": 100,
			}
		).insert(ignore_permissions=True)


def ensure_test_supervisor():
	return ensure_test_user("reno.supervisor@example.com", "Site Supervisor", "Supervisor")


def ensure_test_sales_user():
	return ensure_test_user("reno.sales@example.com", "Sales User", "Sales")


def set_discount_settings(threshold=10, role="Sales Manager"):
	settings = frappe.get_single("Reno Settings")
	settings.discount_approval_threshold = threshold
	settings.discount_approver_role = role
	settings.save(ignore_permissions=True)
	frappe.clear_cache()


def ensure_test_user(email, role, last_name):
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
		if not any(row.role == role for row in user.roles):
			user.append("roles", {"role": role})
			user.save(ignore_permissions=True)
		return email
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Reno",
			"last_name": last_name,
			"send_welcome_email": 0,
			"user_type": "System User",
		}
	)
	user.append("roles", {"role": role})
	user.insert(ignore_permissions=True)
	return email
