// Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

var constConfidenceLvl = {"84%": 1, "90%":1.26, "95%":1.65, "99%": 2.33}

frappe.ui.form.on('Production Plan', {
	setup: function(frm) {
		frm.custom_make_buttons = {
			'Work Order': 'Work Order',
			'Material Request': 'Material Request',
		};

		frm.fields_dict['po_items'].grid.get_field('warehouse').get_query = function(doc) {
			return {
				filters: {
					company: doc.company
				}
			}
		}

		frm.set_query('for_warehouse', function(doc) {
			return {
				filters: {
					company: doc.company
				}
			}
		});

		frm.fields_dict['po_items'].grid.get_field('item_code').get_query = function(doc) {
			return {
				query: "erpnext.controllers.queries.item_query",
				filters:{
					'is_stock_item': 1,
				}
			}
		}

		frm.fields_dict['po_items'].grid.get_field('bom_no').get_query = function(doc, cdt, cdn) {
			var d = locals[cdt][cdn];
			if (d.item_code) {
				return {
					query: "erpnext.controllers.queries.bom",
					filters:{'item': cstr(d.item_code)}
				}
			} else frappe.msgprint(__("Please enter Item first"));
		}

		frm.fields_dict['mr_items'].grid.get_field('warehouse').get_query = function(doc) {
			return {
				filters: {
					company: doc.company
				}
			}
		}
	},

	refresh: function(frm) {
		if (frm.doc.docstatus === 1) {
			frm.trigger("show_progress");
		}

		if (frm.doc.docstatus === 1 && frm.doc.po_items
			&& frm.doc.status != 'Completed') {
			frm.add_custom_button(__("Work Order"), ()=> {
				frm.trigger("make_work_order");
			}, __('Create'));
		}

		if (frm.doc.docstatus === 1 && frm.doc.mr_items
			&& !in_list(['Material Requested', 'Completed'], frm.doc.status)) {
			frm.add_custom_button(__("Material Request"), ()=> {
				frm.trigger("make_material_request");
			}, __('Create'));
		}

		frm.page.set_inner_btn_group_as_primary(__('Create'));
		frm.trigger("material_requirement");

		const projected_qty_formula = ` <table class="table table-bordered" style="background-color: #f9f9f9;">
			<tr><td style="padding-left:25px">
				<div>
				<h3 style="text-decoration: underline;">
					<a href = "https://erpnext.com/docs/user/manual/en/stock/projected-quantity">
						${__("Projected Quantity Formula")}
					</a>
				</h3>
					<div>
						<h3 style="font-size: 13px">
							(Actual Qty + Planned Qty + Requested Qty + Ordered Qty) - (Reserved Qty + Reserved for Production + Reserved for Subcontract)
						</h3>
					</div>
					<br>
					<div>
						<ul>
							<li>
								${__("Actual Qty: Quantity available in the warehouse.")}
							</li>
							<li>
								${__("Planned Qty: Quantity, for which, Work Order has been raised, but is pending to be manufactured.")}
							</li>
							<li>
								${__('Requested Qty: Quantity requested for purchase, but not ordered.')}
							</li>
							<li>
								${__('Ordered Qty: Quantity ordered for purchase, but not received.')}
							</li>
							<li>
								${__("Reserved Qty: Quantity ordered for sale, but not delivered.")}
							</li>
							<li>
								${__('Reserved Qty for Production: Raw materials quantity to make manufacturing items.')}
							</li>
							<li>
								${__('Reserved Qty for Subcontract: Raw materials quantity to make subcontracted items.')}
							</li>
						</ul>
					</div>
				</div>
			</td></tr>
		</table>`;

		set_field_options("projected_qty_formula", projected_qty_formula);
	},

	make_work_order: function(frm) {
		frappe.call({
			method: "make_work_order",
			freeze: true,
			doc: frm.doc,
			callback: function() {
				frm.reload_doc();
			}
		});
	},

	make_material_request: function(frm) {

		frappe.confirm(__("Do you want to submit the material request"),
			function() {
				frm.events.create_material_request(frm, 1);
			},
			function() {
				frm.events.create_material_request(frm, 0);
			}
		);
	},

	create_material_request: function(frm, submit) {
		frm.doc.submit_material_request = submit;

		frappe.call({
			method: "make_material_request",
			freeze: true,
			doc: frm.doc,
			callback: function(r) {
				frm.reload_doc();
			}
		});
	},

	get_sales_orders: function(frm) {
		frappe.call({
			method: "get_open_sales_orders",
			freeze: true,
			doc: frm.doc,
			callback: function(r) {
				refresh_field("sales_orders");
			}
		});
	},

	get_material_request: function(frm) {
		frappe.call({
			method: "get_pending_material_requests",
			freeze: true,
			doc: frm.doc,
			callback: function() {
				refresh_field('material_requests');
			}
		});
	},

	get_items: function(frm) {
		frappe.call({
			method: "get_items",
			freeze: true,
			doc: frm.doc,
			callback: function() {
				refresh_field('po_items');
			}
		});
	},

	get_items_for_mr: function(frm) {
		const set_fields = ['actual_qty', 'item_code','item_name', 'description', 'uom',
			'min_order_qty', 'quantity', 'sales_order', 'warehouse', 'projected_qty', 'safety_qty', 'lead_time_days', 'consumed_daily', 'material_request_type'];
		frappe.call({
			method: "erpnext.manufacturing.doctype.production_plan.production_plan.get_items_for_material_requests",
			freeze: true,
			args: {doc: frm.doc},
			callback: function(r) {
				if(r.message) {
					frm.set_value('mr_items', []);
					$.each(r.message, function(i, d) {
						var item = frm.add_child('mr_items');
						for (let key in d) {
							if (d[key] && in_list(set_fields, key)) {
								item[key] = d[key];
							}
						}
					});
				}
				refresh_field('mr_items');
			}
		});

		// frappe.call({
		// 	method: "erpnext.manufacturing.doctype.production_plan.production_plan.get_safety_qty",
		// 	freeze: true,
		// 	args: {doc: frm.doc},
		// 	callback: function(r) {
		// 		let {qty, std_qty} = r.message;
		// 		let safety_qty = qty + std_qty * frm.doc.confidence_level
		// 		frappe.model.set_value(cdt, cdn, 'safety_qty', safety_qty);
		// 	}
		// })
	},

	for_warehouse: function(frm) {
		if (frm.doc.mr_items) {
			frm.trigger("get_items_for_mr");
		}
	},

	download_materials_required: function(frm) {
		let get_template_url = 'erpnext.manufacturing.doctype.production_plan.production_plan.download_raw_materials';
		open_url_post(frappe.request.url, { cmd: get_template_url, doc: frm.doc });
	},

	show_progress: function(frm) {
		var bars = [];
		var message = '';
		var title = '';

		// produced qty
		let item_wise_qty = {};
		frm.doc.po_items.forEach((data) => {
			if(!item_wise_qty[data.item_code]) {
				item_wise_qty[data.item_code] = data.produced_qty;
			} else {
				item_wise_qty[data.item_code] += data.produced_qty;
			}
		})

		if (item_wise_qty) {
			for (var key in item_wise_qty) {
				title += __('Item {0}: {1} qty produced. ', [key, item_wise_qty[key]]);
			}
		}

		bars.push({
			'title': title,
			'width': (frm.doc.total_produced_qty / frm.doc.total_planned_qty * 100) + '%',
			'progress_class': 'progress-bar-success'
		});
		if (bars[0].width == '0%') {
			bars[0].width = '0.5%';
		}
		message = title;
		frm.dashboard.add_progress(__('Status'), bars, message);
	},
});

frappe.ui.form.on("Production Plan Item", {
	item_code: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.item_code) {
			frappe.call({
				method: "erpnext.manufacturing.doctype.production_plan.production_plan.get_item_data",
				args: {
					item_code: row.item_code
				},
				callback: function(r) {
					for (let key in r.message) {
						frappe.model.set_value(cdt, cdn, key, r.message[key]);
					}
				}
			});
		}
	}
});

frappe.ui.form.on("Material Request Plan Item", {
	warehouse: function(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.warehouse && row.item_code && frm.doc.company) {
			frappe.call({
				method: "erpnext.manufacturing.doctype.production_plan.production_plan.get_bin_details",
				args: {
					row: row,
					company: frm.doc.company,
					for_warehouse: row.warehouse
				},
				callback: function(r) {
					let {projected_qty, actual_qty, safety_qty} = r.message;

					frappe.model.set_value(cdt, cdn, 'projected_qty', projected_qty);
					frappe.model.set_value(cdt, cdn, 'actual_qty', actual_qty);
					// frappe.model.set_value(cdt, cdn, 'safety_qty', safety_qty);
				}
			})
			// frappe.call({
			// 	method: "erpnext.manufacturing.doctype.production_plan.production_plan.get_safety_qty",
			// 	args: {
			// 		row: row,
			// 		company: frm.doc.company,
			// 		for_warehouse: row.warehouse
			// 	},
			// 	callback: function(r) {
			// 		let {qty, std_qty} = r.message;
			// 		let safety_qty = qty + std_qty * frm.doc.confidence_level
			// 		frappe.model.set_value(cdt, cdn, 'safety_qty', safety_qty);
			// 	}
			// })
		}
	}
});
