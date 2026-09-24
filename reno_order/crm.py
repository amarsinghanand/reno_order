"""Outbound CRM sync and inbound webhook.

Submit only enqueues a long-queue job. The worker POSTs the order, writes an
Integration Request, and retries timeouts / 429 / 5xx. Tokens live in Reno
Settings Password fields. ``mock://reno-crm`` stays in-process so Desk never
HTTP-calls the same Gunicorn worker.
"""

import hashlib
import hmac
import json
import time

import frappe
from frappe import _
from frappe.utils import now_datetime
from frappe.utils.background_jobs import is_job_enqueued

SERVICE_NAME = "Reno CRM"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
CRM_QUEUE = "long"
CRM_JOB_TIMEOUT = 120
CRM_JOB_MAX_ATTEMPTS = 3
RETRYABLE_ERROR_MARKERS = ("timed out", "http 429", "http 500", "http 502", "http 503", "http 504", "connection failed")
sleep = time.sleep


class CRMError(Exception):
	pass


class CRMRetryableError(CRMError):
	pass


class CRMTimeoutError(CRMRetryableError):
	pass


def ensure_crm_defaults():
	"""First install: mock URL, timeouts, and generated (encrypted) tokens."""
	if not frappe.db.exists("DocType", "Reno Settings"):
		return

	settings = frappe.get_single("Reno Settings")
	changed = False
	first_setup = not settings.get_password("crm_api_token", raise_exception=False)
	if not settings.crm_base_url:
		settings.crm_base_url = "mock://reno-crm"
		changed = True
	if not settings.crm_timeout_seconds:
		settings.crm_timeout_seconds = 5
		changed = True
	if settings.crm_max_retries in (None, ""):
		settings.crm_max_retries = 2
		changed = True
	if first_setup:
		settings.crm_api_token = frappe.generate_hash(length=24)
		changed = True
	if not settings.get_password("crm_webhook_secret", raise_exception=False):
		settings.crm_webhook_secret = frappe.generate_hash(length=24)
		changed = True
	if first_setup:
		settings.crm_enabled = 1
		changed = True
	if changed:
		settings.save(ignore_permissions=True)
		frappe.clear_cache()


def get_crm_settings():
	"""Read CRM flags and decrypt Password fields for the current site."""
	settings = frappe.get_single("Reno Settings")
	return {
		"enabled": bool(settings.crm_enabled),
		"base_url": (settings.crm_base_url or "").rstrip("/"),
		"timeout": int(settings.crm_timeout_seconds or 5),
		"max_retries": int(settings.crm_max_retries or 0),
		"api_token": settings.get_password("crm_api_token", raise_exception=False),
		"webhook_secret": settings.get_password("crm_webhook_secret", raise_exception=False),
	}


def sync_on_submit(doc):
	"""Queue CRM sync after submit. The user does not wait for the remote API."""
	settings = get_crm_settings()
	if not settings["enabled"]:
		return
	enqueue_crm_sync(doc.name, force=0, attempt=1)


def crm_job_id(reno_order, attempt=1):
	attempt = int(attempt or 1)
	if attempt <= 1:
		return f"reno-crm-sync-{reno_order}"
	return f"reno-crm-sync-{reno_order}:{attempt}"


def enqueue_crm_sync(reno_order, force=0, attempt=1):
	"""Create one long-queue job. Deduplicated while a job for this attempt is already running."""
	force = frappe.parse_json(force) if isinstance(force, str) else force
	attempt = int(attempt or 1)
	job_id = crm_job_id(reno_order, attempt)
	logger = frappe.logger("reno_crm")

	if is_job_enqueued(job_id):
		logger.info("CRM job %s already queued; skipping duplicate", job_id)
		return {
			"reno_order": reno_order,
			"status": "Queued",
			"job_id": job_id,
			"queue": CRM_QUEUE,
			"duplicate": True,
		}

	frappe.db.set_value(
		"Reno Order",
		reno_order,
		{"crm_sync_status": "Queued", "crm_last_error": ""},
		update_modified=False,
	)
	frappe.enqueue(
		"reno_order.crm.run_crm_sync_job",
		queue=CRM_QUEUE,
		timeout=CRM_JOB_TIMEOUT,
		enqueue_after_commit=True,
		deduplicate=True,
		job_id=job_id,
		reno_order=reno_order,
		force=force,
		attempt=attempt,
	)
	logger.info(
		"CRM job enqueued on %s for %s (attempt %s/%s, timeout %ss)",
		CRM_QUEUE,
		reno_order,
		attempt,
		CRM_JOB_MAX_ATTEMPTS,
		CRM_JOB_TIMEOUT,
	)
	return {
		"reno_order": reno_order,
		"status": "Queued",
		"job_id": job_id,
		"queue": CRM_QUEUE,
		"attempt": attempt,
	}


def run_crm_sync_job(reno_order, force=0, attempt=1):
	"""Worker entry: run the CRM call, log, and re-queue retryable failures."""
	attempt = int(attempt or 1)
	logger = frappe.logger("reno_crm")
	logger.info("CRM job started for %s (attempt %s)", reno_order, attempt)
	try:
		result = sync_reno_order_to_crm(reno_order, force=force, raise_on_error=False)
	except Exception as exc:
		frappe.log_error(title=f"Reno CRM job crashed: {reno_order}", message=str(exc)[:180])
		if frappe.db.exists("Reno Order", reno_order):
			frappe.db.set_value(
				"Reno Order",
				reno_order,
				{"crm_sync_status": "Failed", "crm_last_error": str(exc)[:180]},
				update_modified=False,
			)
		logger.error("CRM job crashed for %s: %s", reno_order, type(exc).__name__)
		raise

	if result.get("status") == "Failed" and _should_requeue_job(result, attempt):
		logger.warning("CRM job will retry %s (attempt %s failed: %s)", reno_order, attempt, result.get("error"))
		enqueue_crm_sync(reno_order, force=force, attempt=attempt + 1)
	else:
		logger.info("CRM job finished for %s: %s", reno_order, result.get("status"))
	return result


def _should_requeue_job(result, attempt):
	if attempt >= CRM_JOB_MAX_ATTEMPTS:
		return False
	error = (result.get("error") or "").lower()
	return any(marker in error for marker in RETRYABLE_ERROR_MARKERS)


@frappe.whitelist()
def sync_reno_order_to_crm(reno_order, force=0, raise_on_error=1):
	"""Push one Reno Order to the external CRM."""
	force = frappe.parse_json(force) if isinstance(force, str) else force
	raise_on_error = frappe.parse_json(raise_on_error) if isinstance(raise_on_error, str) else raise_on_error

	doc = frappe.get_doc("Reno Order", reno_order)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("Submit the Reno Order before syncing it to the CRM."))

	settings = get_crm_settings()
	if not settings["enabled"]:
		frappe.throw(_("Enable External CRM in Reno Settings first."))
	if not settings["api_token"]:
		frappe.throw(_("Set the CRM API Token in Reno Settings. It is stored encrypted."))
	if not settings["base_url"]:
		frappe.throw(_("Set the CRM Base URL in Reno Settings."))

	if doc.crm_sync_status == "Synced" and doc.crm_id and not force:
		return {"reno_order": doc.name, "crm_id": doc.crm_id, "status": "Synced"}

	payload = _order_payload(doc)
	headers = {
		"Authorization": f"Bearer {settings['api_token']}",
		"Content-Type": "application/json",
		"Accept": "application/json",
	}
	url = _upsert_url(settings["base_url"])
	logger = frappe.logger("reno_crm")
	logger.info("CRM sync started for %s", doc.name)

	try:
		response = request_with_retry(
			url,
			headers,
			payload,
			timeout=settings["timeout"],
			max_retries=settings["max_retries"],
		)
	except Exception as exc:
		_record_failure(doc, url, headers, payload, exc)
		logger.error("CRM sync failed for %s: %s", doc.name, type(exc).__name__)
		if raise_on_error:
			frappe.throw(_("CRM sync failed: {0}").format(_safe_error(exc)))
		return {"reno_order": doc.name, "status": "Failed", "error": _safe_error(exc)}

	crm_id = response.get("crm_id")
	if not crm_id:
		exc = CRMError("CRM response did not include crm_id")
		_record_failure(doc, url, headers, payload, exc, output=response)
		if raise_on_error:
			frappe.throw(_("CRM sync failed: the remote response had no crm_id."))
		return {"reno_order": doc.name, "status": "Failed", "error": "missing crm_id"}

	_record_success(doc, url, headers, payload, response)
	logger.info("CRM sync completed for %s as %s", doc.name, crm_id)
	return {"reno_order": doc.name, "crm_id": crm_id, "status": "Synced"}


def request_with_retry(url, headers, payload, timeout, max_retries):
	attempts = max(0, int(max_retries)) + 1
	last_error = None
	for attempt in range(attempts):
		try:
			return _transport_post(url, headers, payload, timeout)
		except (CRMRetryableError, CRMTimeoutError) as exc:
			last_error = exc
			if attempt >= attempts - 1:
				break
			frappe.logger("reno_crm").warning(
				"CRM retry %s/%s for %s: %s",
				attempt + 1,
				attempts,
				url,
				type(exc).__name__,
			)
			sleep(min(2**attempt, 4))
	raise last_error


def _transport_post(url, headers, payload, timeout):
	if url.startswith("mock://"):
		return _call_mock(headers, payload)
	return _call_http(url, headers, payload, timeout)


def _call_http(url, headers, payload, timeout):
	import requests

	try:
		response = requests.post(url, json=payload, headers=headers, timeout=timeout)
	except requests.Timeout as exc:
		raise CRMTimeoutError("CRM request timed out") from exc
	except requests.ConnectionError as exc:
		raise CRMRetryableError("CRM connection failed") from exc

	return _parse_response(response.status_code, response.text)


def _call_mock(headers, payload):
	from reno_order.crm_mock import handle_upsert

	status_code, body = handle_upsert(headers, payload)
	return _parse_response(status_code, json.dumps(body))


def _parse_response(status_code, text):
	if status_code in RETRYABLE_STATUS:
		raise CRMRetryableError(f"CRM returned HTTP {status_code}")
	if status_code == 401 or status_code == 403:
		raise CRMError("CRM authentication failed")
	if status_code >= 400:
		raise CRMError(f"CRM returned HTTP {status_code}")

	try:
		data = json.loads(text or "{}")
	except json.JSONDecodeError as exc:
		raise CRMError("CRM returned invalid JSON") from exc
	if not isinstance(data, dict):
		raise CRMError("CRM returned an unexpected payload")
	return data


def _upsert_url(base_url):
	if base_url.startswith("mock://"):
		return base_url
	if base_url.endswith("/upsert_order"):
		return base_url
	return f"{base_url}/api/method/reno_order.crm_mock.upsert_order"


def _order_payload(doc):
	return {
		"reno_order": doc.name,
		"customer": doc.customer,
		"customer_name": doc.customer_name,
		"status": doc.status,
		"grand_total": doc.grand_total,
		"currency": doc.currency,
		"expected_installation_date": str(doc.expected_installation_date or ""),
		"company": doc.company,
		"items": [
			{"item_code": row.item_code, "qty": row.qty, "rate": row.rate} for row in doc.items
		],
	}


def _redacted_headers(headers):
	safe = dict(headers or {})
	if safe.get("Authorization"):
		safe["Authorization"] = "Bearer ***"
	return safe


def _safe_error(exc):
	return str(exc)[:180]


def _record_success(doc, url, headers, payload, response):
	_write_integration_request(url, headers, payload, output=response, status="Completed", reference=doc.name)
	doc.db_set(
		{
			"crm_id": response.get("crm_id"),
			"crm_sync_status": "Synced",
			"crm_last_synced": now_datetime(),
			"crm_last_error": "",
		},
		update_modified=False,
	)


def _record_failure(doc, url, headers, payload, exc, output=None):
	_write_integration_request(
		url,
		headers,
		payload,
		output=output,
		error=_safe_error(exc),
		status="Failed",
		reference=doc.name,
	)
	frappe.log_error(title=f"Reno CRM sync failed: {doc.name}", message=_safe_error(exc))
	doc.db_set(
		{
			"crm_sync_status": "Failed",
			"crm_last_error": _safe_error(exc),
		},
		update_modified=False,
	)


def _write_integration_request(url, headers, payload, output=None, error=None, status="Completed", reference=None):
	log = frappe.get_doc(
		{
			"doctype": "Integration Request",
			"integration_request_service": SERVICE_NAME,
			"is_remote_request": 1,
			"status": status,
			"url": url,
			"request_headers": frappe.as_json(_redacted_headers(headers), indent=1),
			"data": frappe.as_json(payload, indent=1),
			"output": frappe.as_json(output, indent=1) if output is not None else None,
			"error": error,
			"reference_doctype": "Reno Order",
			"reference_docname": reference,
		}
	)
	log.insert(ignore_permissions=True)
	return log


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive_crm_webhook():
	"""Inbound CRM webhook. Authenticated with HMAC-SHA256 of the raw body."""
	raw = ""
	request = getattr(frappe.local, "request", None)
	if request is not None:
		raw = request.get_data(as_text=True) or ""
	if not raw:
		raw = frappe.as_json({k: v for k, v in frappe.form_dict.items() if k != "cmd"})

	settings = get_crm_settings()
	secret = settings["webhook_secret"]
	signature = _request_header("X-Reno-Signature")
	if not secret or not _valid_signature(secret, raw, signature):
		frappe.throw(_("Invalid CRM webhook signature."), frappe.AuthenticationError)

	try:
		data = json.loads(raw) if raw else {}
	except json.JSONDecodeError:
		frappe.throw(_("CRM webhook body must be JSON."))

	reno_order = data.get("reno_order")
	if not reno_order or not frappe.db.exists("Reno Order", reno_order):
		frappe.throw(_("Reno Order {0} was not found.").format(reno_order), frappe.DoesNotExistError)

	values = {}
	if data.get("crm_id"):
		values["crm_id"] = data["crm_id"]
		values["crm_sync_status"] = "Synced"
		values["crm_last_synced"] = now_datetime()
		values["crm_last_error"] = ""
	if values:
		frappe.db.set_value("Reno Order", reno_order, values, update_modified=False)

	comment = data.get("comment") or data.get("event") or "CRM webhook received"
	frappe.get_doc("Reno Order", reno_order).add_comment("Comment", comment)

	frappe.logger("reno_crm").info("CRM webhook accepted for %s", reno_order)
	return {"reno_order": reno_order, "status": "accepted"}


def webhook_signature(body, secret=None):
	secret = secret or get_crm_settings()["webhook_secret"]
	digest = hmac.new((secret or "").encode(), body.encode(), hashlib.sha256).hexdigest()
	return f"sha256={digest}"


def _valid_signature(secret, raw, signature):
	if not signature:
		return False
	expected = webhook_signature(raw, secret)
	return hmac.compare_digest(expected, signature)


def _request_header(name):
	request = getattr(frappe.local, "request", None)
	if request is not None and getattr(request, "headers", None):
		return request.headers.get(name) or request.headers.get(name.lower())
	try:
		return frappe.get_request_header(name)
	except RuntimeError:
		return None
