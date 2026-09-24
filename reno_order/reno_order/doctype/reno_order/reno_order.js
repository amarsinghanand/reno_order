// Reno Order form. UX only: filters, buttons, and friendly messages.
// Totals, permissions, and Mark as Installed are enforced again on the server.

frappe.ui.form.on("Reno Order", {
	setup(frm) {
		frm.set_query("customer_address", () => ({
			filters: {
				link_doctype: "Customer",
				link_name: frm.doc.customer,
			},
		}));
		frm.set_query("contact_person", () => ({
			filters: {
				link_doctype: "Customer",
				link_name: frm.doc.customer,
			},
		}));
		frm.set_query("warehouse", "items", () => ({
			filters: { company: frm.doc.company, is_group: 0 },
		}));
		frm.set_query("project", () => ({
			filters: { company: frm.doc.company },
		}));
		frm.set_query("assigned_to", () => ({
			query: "reno_order.permissions.supervisor_user_query",
		}));
	},

	refresh(frm) {
		apply_conditional_display(frm);
		apply_supervisor_field_locks(frm);
		add_status_actions(frm);
		add_downstream_buttons(frm);
		show_friendly_status(frm);
	},

	validate(frm) {
		if (!validate_installation_date(frm, true)) {
			frappe.validated = false;
		}
		if (!validate_item_quantities(frm, true)) {
			frappe.validated = false;
		}
	},

	customer(frm) {
		frm.set_value("customer_address", null);
		frm.set_value("contact_person", null);
		if (!frm.doc.customer) {
			return;
		}
		frappe.db.get_value("Customer", frm.doc.customer, ["customer_name", "customer_type"], (r) => {
			if (!r) {
				return;
			}
			if (r.customer_name) {
				frm.set_value("customer_name", r.customer_name);
			}
			frm.set_intro(
				__("Customer {0} ({1}) will be billed on the Sales Order.", [
					r.customer_name || frm.doc.customer,
					r.customer_type || __("Customer"),
				]),
				"blue"
			);
		});
	},

	company(frm) {
		(frm.doc.items || []).forEach((row) => {
			if (row.warehouse) {
				frappe.model.set_value(row.doctype, row.name, "warehouse", null);
			}
		});
	},

	discount_percentage(frm) {
		recalculate_totals(frm);
		warn_discount_threshold(frm);
	},

	expected_installation_date(frm) {
		validate_installation_date(frm, false);
	},

	transaction_date(frm) {
		validate_installation_date(frm, false);
	},
});

frappe.ui.form.on("Reno Order Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) {
			return;
		}
		frappe.db.get_value(
			"Item",
			row.item_code,
			["item_name", "description", "stock_uom", "standard_rate"],
			(item) => {
				if (!item) {
					return;
				}
				if (item.description) {
					frappe.model.set_value(cdt, cdn, "description", item.description);
				}
				if (item.stock_uom) {
					frappe.model.set_value(cdt, cdn, "uom", item.stock_uom);
				}
				if (!flt(row.rate) && flt(item.standard_rate)) {
					frappe.model.set_value(cdt, cdn, "rate", item.standard_rate);
				}
			}
		);
	},
	qty(frm, cdt, cdn) {
		recalculate_row(frm, cdt, cdn);
	},
	rate(frm, cdt, cdn) {
		recalculate_row(frm, cdt, cdn);
	},
	items_remove(frm) {
		recalculate_totals(frm);
	},
});

function add_downstream_buttons(frm) {
	// Create SO/DN/SI/WO/MR and View links. Each server method blocks duplicates.
	if (frm.doc.docstatus !== 1 || is_supervisor_only()) {
		return;
	}

	if (!frm.doc.sales_order) {
		add_create_button(frm, __("Create Sales Order"), "create_sales_order", __("Creating Sales Order"));
	}
	if (frm.doc.sales_order && !frm.doc.delivery_note) {
		add_create_button(frm, __("Create Delivery Note"), "create_delivery_note", __("Creating Delivery Note"));
	}
	if (frm.doc.delivery_note && !frm.doc.sales_invoice) {
		add_create_button(frm, __("Create Sales Invoice"), "create_sales_invoice", __("Creating Sales Invoice"));
	}
	if (!frm.doc.material_request) {
		add_create_button(frm, __("Create Material Request"), "create_material_request", __("Creating Material Request"));
	}

	frm.add_custom_button(__("Sync to CRM"), () => {
		frappe.call({
			method: "sync_to_crm",
			doc: frm.doc,
			args: { force: 1 },
			freeze: true,
			freeze_message: __("Queueing CRM sync"),
			callback(r) {
				const queued = r.message || {};
				frappe.show_alert({
					message: queued.duplicate
						? __("CRM sync is already queued")
						: __("CRM sync queued on the {0} worker", [queued.queue || "long"]),
					indicator: "blue",
				});
				frm.reload_doc();
			},
		});
	});

	frm.add_custom_button(__("CRM Requests"), () => {
		frappe.set_route("List", "Integration Request", {
			reference_doctype: "Reno Order",
			reference_docname: frm.doc.name,
		});
	}, __("View"));

	frm.add_custom_button(__("Create Work Order"), () => {
		frappe.call({
			method: "create_work_orders",
			doc: frm.doc,
			freeze: true,
			freeze_message: __("Creating Work Order"),
			callback(r) {
				frm.reload_doc();
				const names = r.message || [];
				if (names.length === 1) {
					frappe.set_route("Form", "Work Order", names[0]);
				} else if (names.length) {
					frappe.msgprint(__("Work Orders created: {0}", [names.join(", ")]));
				}
			},
		});
	});

	if (frm.doc.sales_order) {
		frm.add_custom_button(__("Sales Order"), () => {
			frappe.set_route("Form", "Sales Order", frm.doc.sales_order);
		}, __("View"));
	}
	if (frm.doc.delivery_note) {
		frm.add_custom_button(__("Delivery Note"), () => {
			frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note);
		}, __("View"));
	}
	if (frm.doc.sales_invoice) {
		frm.add_custom_button(__("Sales Invoice"), () => {
			frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice);
		}, __("View"));
	}
	if (frm.doc.material_request) {
		frm.add_custom_button(__("Material Request"), () => {
			frappe.set_route("Form", "Material Request", frm.doc.material_request);
		}, __("View"));
	}
}

function add_status_actions(frm) {
	// Ready + assigned supervisor (or SM). Server still validates the transition.
	if (!can_show_mark_installed(frm)) {
		return;
	}
	frm.add_custom_button(__("Mark as Installed"), () => {
		if (frm._marking_installed) {
			return;
		}
		frappe.confirm(
			__("Mark this Reno Order as Installed? The server will re-check your role, assignment and the allowed status transition."),
			() => {
				frm._marking_installed = true;
				frappe.call({
					method: "mark_as_installed",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Marking Installed"),
					callback(r) {
						frm.reload_doc();
						const already = r.message && r.message.already_installed;
						frappe.show_alert({
							message: already
								? __("Already Installed. Downstream: {0}", [r.message.delivery_note || r.message.processing_status || __("queued")])
								: __("Status is now {0}", [(r.message && r.message.status) || "Installed"]),
							indicator: already ? "orange" : "green",
						});
					},
					error() {
						// Timeout after commit: the worker may already have saved Installed / created the DN.
						frm.reload_doc().then(() => {
							if (frm.doc.status === "Installed") {
								frappe.show_alert({
									message: __(
										"The order is Installed. The first request likely timed out after save. Downstream: {0}",
										[frm.doc.delivery_note || frm.doc.processing_status || __("queued")]
									),
									indicator: "orange",
								});
							}
						});
					},
					always() {
						frm._marking_installed = false;
					},
				});
			}
		);
	}).addClass("btn-primary");
}

function can_show_mark_installed(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 1) {
		return false;
	}
	if (frm.doc.status !== "Ready for Installation") {
		return false;
	}
	if (frappe.user.has_role("System Manager") || frappe.user.has_role("Sales Manager")) {
		return true;
	}
	return frappe.user.has_role("Site Supervisor") && frm.doc.assigned_to === frappe.session.user;
}

function apply_conditional_display(frm) {
	const installation = ["Ready for Installation", "Installed", "Closed"].includes(frm.doc.status);
	frm.toggle_display("section_installation", frm.doc.docstatus === 1 || installation);
	frm.toggle_display("section_crm", frm.doc.docstatus === 1);
	frm.set_df_property("items", "cannot_add_rows", is_supervisor_only());
}

function apply_supervisor_field_locks(frm) {
	// Hide selling controls. Server permlevel + validate_site_supervisor_selling_fields still apply.
	if (!is_supervisor_only()) {
		return;
	}
	["customer", "discount_percentage", "discount_amount", "total_amount", "grand_total"].forEach((field) => {
		frm.set_df_property(field, "read_only", 1);
	});
	if (frm.fields_dict.items) {
		["item_code", "qty", "rate", "amount"].forEach((field) => {
			frm.fields_dict.items.grid.update_docfield_property(field, "read_only", 1);
		});
	}
}

function show_friendly_status(frm) {
	// Headline only. Does not change status or permissions.
	if (frm.doc.is_overdue) {
		frm.dashboard.set_headline_alert(
			__("Installation is overdue. Expected date: {0}", [frm.doc.expected_installation_date]),
			"red"
		);
	} else if (frm.doc.status === "Ready for Installation") {
		frm.dashboard.set_headline_alert(__("Site is ready. Use Mark as Installed when work is complete."), "blue");
	} else if (frm.doc.status === "Installed") {
		frm.dashboard.set_headline_alert(__("Installation is complete."), "green");
	}

	if (frm.doc.crm_sync_status === "Queued") {
		frm.set_intro(__("CRM sync is queued on the long worker."), "blue");
	} else if (frm.doc.crm_sync_status === "Failed" && frm.doc.crm_last_error) {
		frm.set_intro(__("CRM sync failed: {0}", [frm.doc.crm_last_error]), "orange");
	}

	warn_discount_threshold(frm);
}

function warn_discount_threshold(frm) {
	if (frm.doc.docstatus !== 0 || !flt(frm.doc.discount_percentage)) {
		return;
	}
	frappe.db.get_single_value("Reno Settings", "discount_approval_threshold").then((threshold) => {
		if (flt(frm.doc.discount_percentage) > flt(threshold)) {
			frm.dashboard.set_headline_alert(
				__(
					"Discount of {0}% is above the {1}% approval threshold. A Sales Manager must submit this order.",
					[flt(frm.doc.discount_percentage), flt(threshold)]
				),
				"orange"
			);
		}
	});
}

function validate_installation_date(frm, from_validate) {
	if (!frm.doc.expected_installation_date || !frm.doc.transaction_date) {
		return true;
	}
	if (frm.doc.expected_installation_date >= frm.doc.transaction_date) {
		return true;
	}
	const message = __("Expected Installation Date cannot be before the Transaction Date.");
	if (from_validate) {
		frappe.msgprint({ title: __("Installation date"), message, indicator: "red" });
	} else {
		frappe.show_alert({ message, indicator: "red" });
	}
	return false;
}

function validate_item_quantities(frm, from_validate) {
	const bad = (frm.doc.items || []).filter((row) => flt(row.qty) <= 0);
	if (!bad.length) {
		return true;
	}
	const message = __("Each item needs a quantity greater than zero. Check row {0}.", [bad[0].idx]);
	if (from_validate) {
		frappe.msgprint({ title: __("Quantity"), message, indicator: "red" });
	}
	return false;
}

function add_create_button(frm, label, method, freeze_message) {
	frm.add_custom_button(label, () => {
		frappe.call({
			method,
			doc: frm.doc,
			freeze: true,
			freeze_message,
			callback(r) {
				if (r.message) {
					frm.reload_doc();
					const routes = {
						create_sales_order: "Sales Order",
						create_delivery_note: "Delivery Note",
						create_sales_invoice: "Sales Invoice",
						create_material_request: "Material Request",
					};
					frappe.set_route("Form", routes[method], r.message);
				}
			},
		});
	});
}

function recalculate_row(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	frappe.model.set_value(cdt, cdn, "amount", flt(row.qty) * flt(row.rate));
	recalculate_totals(frm);
}

function is_supervisor_only() {
	return (
		frappe.user.has_role("Site Supervisor") &&
		!frappe.user.has_role("Sales User") &&
		!frappe.user.has_role("Sales Manager") &&
		!frappe.user.has_role("System Manager")
	);
}

function recalculate_totals(frm) {
	let total = 0;
	(frm.doc.items || []).forEach((row) => {
		total += flt(row.amount);
	});
	const discount_amount = (total * flt(frm.doc.discount_percentage)) / 100;
	frm.set_value("total_amount", total);
	frm.set_value("discount_amount", discount_amount);
	frm.set_value("grand_total", total - discount_amount);
}
