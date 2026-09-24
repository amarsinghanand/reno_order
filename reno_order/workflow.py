import frappe

from reno_order.constants import WORKFLOW_NAME

STATES = [
	{"name": "Draft", "style": "Inverse", "doc_status": "0", "allow_edit": "Sales User"},
	{"name": "Confirmed", "style": "Primary", "doc_status": "1", "allow_edit": "Sales Manager"},
	{"name": "In Production", "style": "Warning", "doc_status": "1", "allow_edit": "Production User"},
	{"name": "Ready for Installation", "style": "Info", "doc_status": "1", "allow_edit": "Site Supervisor"},
	{"name": "Installed", "style": "Success", "doc_status": "1", "allow_edit": "Sales Manager"},
	{"name": "Closed", "style": "Inverse", "doc_status": "1", "allow_edit": "Sales Manager"},
	{"name": "Cancelled", "style": "Danger", "doc_status": "2", "allow_edit": "Sales Manager"},
]

ACTIONS = [
	"Confirm",
	"Start Production",
	"Mark Ready for Installation",
	"Mark Installed",
	"Close",
	"Cancel",
]

TRANSITIONS = [
	("Draft", "Confirm", "Confirmed", "Sales User"),
	("Draft", "Confirm", "Confirmed", "Sales Manager"),
	("Draft", "Confirm", "Confirmed", "System Manager"),
	("Confirmed", "Start Production", "In Production", "Production User"),
	("Confirmed", "Start Production", "In Production", "Sales Manager"),
	("Confirmed", "Start Production", "In Production", "System Manager"),
	("Confirmed", "Cancel", "Cancelled", "Sales Manager"),
	("Confirmed", "Cancel", "Cancelled", "System Manager"),
	("In Production", "Mark Ready for Installation", "Ready for Installation", "Production User"),
	("In Production", "Mark Ready for Installation", "Ready for Installation", "Sales Manager"),
	("In Production", "Mark Ready for Installation", "Ready for Installation", "System Manager"),
	("In Production", "Cancel", "Cancelled", "Sales Manager"),
	("In Production", "Cancel", "Cancelled", "System Manager"),
	("Ready for Installation", "Mark Installed", "Installed", "Site Supervisor"),
	("Ready for Installation", "Mark Installed", "Installed", "Sales Manager"),
	("Ready for Installation", "Mark Installed", "Installed", "System Manager"),
	("Ready for Installation", "Cancel", "Cancelled", "Sales Manager"),
	("Ready for Installation", "Cancel", "Cancelled", "System Manager"),
	("Installed", "Close", "Closed", "Sales Manager"),
	("Installed", "Close", "Closed", "Accounts User"),
	("Installed", "Close", "Closed", "System Manager"),
]


def ensure_workflow():
	_ensure_states()
	_ensure_actions()
	_ensure_workflow_doc()


def _ensure_states():
	for state in STATES:
		if not frappe.db.exists("Workflow State", state["name"]):
			frappe.get_doc(
				{
					"doctype": "Workflow State",
					"workflow_state_name": state["name"],
					"style": state["style"],
				}
			).insert(ignore_permissions=True)
		else:
			frappe.db.set_value("Workflow State", state["name"], "style", state["style"])


def _ensure_actions():
	for action in ACTIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def _ensure_workflow_doc():
	states = [
		{
			"state": state["name"],
			"doc_status": state["doc_status"],
			"allow_edit": state["allow_edit"],
			"update_field": "status",
			"update_value": state["name"],
			"avoid_status_override": 1,
			"send_email": 0,
		}
		for state in STATES
	]
	transitions = [
		{
			"state": state,
			"action": action,
			"next_state": next_state,
			"allowed": role,
			"allow_self_approval": 1,
		}
		for state, action, next_state, role in TRANSITIONS
	]

	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		doc = frappe.get_doc("Workflow", WORKFLOW_NAME)
		doc.states = []
		doc.transitions = []
		for row in states:
			doc.append("states", row)
		for row in transitions:
			doc.append("transitions", row)
		doc.is_active = 1
		doc.override_status = 1
		doc.workflow_state_field = "status"
		doc.send_email_alert = 0
		doc.save(ignore_permissions=True)
		return

	frappe.get_doc(
		{
			"doctype": "Workflow",
			"workflow_name": WORKFLOW_NAME,
			"document_type": "Reno Order",
			"is_active": 1,
			"override_status": 1,
			"send_email_alert": 0,
			"workflow_state_field": "status",
			"states": states,
			"transitions": transitions,
		}
	).insert(ignore_permissions=True)
