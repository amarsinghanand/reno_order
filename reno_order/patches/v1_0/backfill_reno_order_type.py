"""Backfill Reno Order.order_type = Standard on existing blank rows.

Safe for ~50,000 live records: batched SQL, no document load, no overwrite of
Custom / Premium / Standard, idempotent, short row locks with a commit per batch.
"""

import frappe

DEFAULT_ORDER_TYPE = "Standard"
BATCH_SIZE = 1000


def execute():
	return backfill_order_type()


def backfill_order_type(batch_size=BATCH_SIZE):
	if not frappe.db.table_exists("Reno Order"):
		return {"updated": 0, "batches": 0, "remaining": 0}
	if not frappe.db.has_column("Reno Order", "order_type"):
		return {"updated": 0, "batches": 0, "remaining": 0}

	batch_size = max(1, int(batch_size))
	logger = frappe.logger("reno_patch")
	updated = 0
	batches = 0

	while True:
		names = frappe.db.sql_list(
			"""
			SELECT name
			FROM `tabReno Order`
			WHERE trim(ifnull(order_type, '')) = ''
			ORDER BY name
			LIMIT %s
			""",
			batch_size,
		)
		if not names:
			break

		frappe.db.sql(
			"""
			UPDATE `tabReno Order`
			SET order_type = %(order_type)s
			WHERE name IN %(names)s
				AND trim(ifnull(order_type, '')) = ''
			""",
			{"order_type": DEFAULT_ORDER_TYPE, "names": names},
		)
		# Release InnoDB row locks so Desk / other writes are not blocked for the
		# whole 50k-row run. Skip in tests so UnitTestCase can still roll back.
		if not getattr(frappe.flags, "in_test", False):
			frappe.db.commit()
		updated += len(names)
		batches += 1
		logger.info("Order Type backfill: %s rows in %s batches", updated, batches)

	remaining = count_blank_order_types()
	logger.info(
		"Order Type backfill finished: updated=%s batches=%s remaining=%s",
		updated,
		batches,
		remaining,
	)
	return {"updated": updated, "batches": batches, "remaining": remaining}


def count_blank_order_types():
	if not frappe.db.table_exists("Reno Order") or not frappe.db.has_column("Reno Order", "order_type"):
		return 0
	return frappe.db.sql(
		"""
		SELECT count(*)
		FROM `tabReno Order`
		WHERE trim(ifnull(order_type, '')) = ''
		"""
	)[0][0]
