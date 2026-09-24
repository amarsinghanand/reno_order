CLOSED_STATUSES = ("Installed", "Closed", "Cancelled")

STATUS_TRANSITIONS = {
	"Draft": {
		"Confirmed": ("Sales User", "Sales Manager", "System Manager"),
	},
	"Confirmed": {
		"In Production": ("Production User", "Sales Manager", "System Manager"),
		"Cancelled": ("Sales Manager", "System Manager"),
	},
	"In Production": {
		"Ready for Installation": ("Production User", "Sales Manager", "System Manager"),
		"Cancelled": ("Sales Manager", "System Manager"),
	},
	"Ready for Installation": {
		"Installed": ("Site Supervisor", "Sales Manager", "System Manager"),
		"Cancelled": ("Sales Manager", "System Manager"),
	},
	"Installed": {
		"Closed": ("Sales Manager", "Accounts User", "System Manager"),
	},
}

WORKFLOW_NAME = "Reno Order Workflow"
