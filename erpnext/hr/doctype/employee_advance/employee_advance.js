// Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on('Employee Advance', {
	setup: function(frm) {
		frm.add_fetch("employee", "company", "company");
		frm.add_fetch("company", "default_employee_advance_account", "advance_account");

		frm.set_query("employee", function() {
			return {
				filters: {
					"status": "Active"
				}
			};
		});

		frm.set_query("advance_account", function() {
			return {
				filters: {
					"root_type": "Asset",
					"is_group": 0,
					"company": frm.doc.company
				}
			};
		});

		// if(frm.doc.workflow_state == "Received CSD")
		// frappe.call({
		// 	method: "erpnext.hr.doctype.employee_advance.employee_advance.get_pending_advances",
		// 	args: {
		// 		employee: frm.doc.employee
		// 	},
		// 	callback: function(r) {
		// 		if(r.message){
		// 			let html=`Still have ${r.message.length} Expense Claim pending :`
		// 			r.message.forEach(e => {
		// 				html += `<a href="./desk#Form/Employee%20Advance/${e}">${e},&nbsp</a>`
		// 			});
		// 			frm.set_intro(html)
		// 		}
		// 	}
		// });
	},

	refresh: function(frm) {
		if (frm.doc.docstatus===1
			&& (flt(frm.doc.paid_amount) < flt(frm.doc.advance_amount))
			&& frappe.model.can_create("Payment Entry")) {
			// frm.add_custom_button(__('Payment'),
			// 	function() { frm.events.make_payment_entry(frm); }, __('Create'));
			frm.add_custom_button(__('Journal'),
				function() { frm.events.make_journal_entry(frm); }, __('Create'));
		}
		else if (
			frm.doc.docstatus === 1
			&& flt(frm.doc.claimed_amount) < flt(frm.doc.paid_amount) - flt(frm.doc.return_amount)
			&& frappe.model.can_create("Expense Claim")
		) {
			frm.add_custom_button(
				__("Expense Claim"),
				function() {
					frm.events.make_expense_claim(frm);
				},
				__('Create')
			);
		}

		if (frm.doc.docstatus === 1
			&& (flt(frm.doc.claimed_amount) + flt(frm.doc.return_amount) < flt(frm.doc.paid_amount))
			&& frappe.model.can_create("Journal Entry")) {

			frm.add_custom_button(__("Return"),  function() {
				frm.trigger('make_return_entry');
			}, __('Create'));
		}
	},

	make_payment_entry: function(frm) {
		var method = "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry";
		if(frm.doc.__onload && frm.doc.__onload.make_payment_via_journal_entry) {
			method = "erpnext.hr.doctype.employee_advance.employee_advance.make_bank_entry"
		}
		return frappe.call({
			method: method,
			args: {
				"dt": frm.doc.doctype,
				"dn": frm.doc.name
			},
			callback: function(r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	},

	make_journal_entry: function(frm) {
		var	method = "erpnext.hr.doctype.employee_advance.employee_advance.make_bank_entry"
		return frappe.call({
			method: method,
			args: {
				"dt": frm.doc.doctype,
				"dn": frm.doc.name
			},
			callback: function(r) {
				var doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	},

	make_expense_claim: function(frm) {
		return frappe.call({
			method: "erpnext.hr.doctype.expense_claim.expense_claim.get_expense_claim",
			args: {
				"employee_name": frm.doc.employee,
				"company": frm.doc.company,
				"employee_advance_name": frm.doc.name,
				"posting_date": frm.doc.posting_date,
				"paid_amount": frm.doc.paid_amount,
				"claimed_amount": frm.doc.claimed_amount,
				"travel_request": frm.doc.travel_request,
				"approver_1": frm.doc.approver_1,
				"approver_2": frm.doc.approver_2,
				"approver_3": frm.doc.approver_3,
			},
			callback: function(r) {
				const doclist = frappe.model.sync(r.message);
				frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
			}
		});
	},

	make_return_entry: function(frm) {
		frappe.call({
			method: 'erpnext.hr.doctype.employee_advance.employee_advance.make_return_entry',
			args: {
				'employee': frm.doc.employee,
				'company': frm.doc.company,
				'employee_advance_name': frm.doc.name,
				'return_amount': flt(frm.doc.paid_amount - frm.doc.claimed_amount),
				'advance_account': frm.doc.advance_account,
				'mode_of_payment': frm.doc.mode_of_payment
			},
			callback: function(r) {
				const doclist = frappe.model.sync(r.message);
				frappe.set_route('Form', doclist[0].doctype, doclist[0].name);
			}
		});
	},

	employee: function (frm) {
		if (frm.doc.employee) {
			return frappe.call({
				method: "erpnext.hr.doctype.employee_advance.employee_advance.get_pending_amount",
				args: {
					"employee": frm.doc.employee,
					"posting_date": frm.doc.posting_date
				},
				callback: function(r) {
					frm.set_value("pending_amount",r.message);
				}
			});
		}
	},
	after_workflow_action(frm){
		let department = frm.doc.department.split(" - ")
		if(department[0] == "Marketing"){
			frappe.db.get_single_value('QL Settings', 'mkt_email_api')
			.then(mkt_email_api => {
				frappe.db.get_value('Employee', cur_frm.doc.employee, 'user_id')
				.then(r => {
					// let next_state = frappe.get_children(frappe.workflow.workflows[frm.doctype], "transitions", {
					// 	state:frm.doc.workflow_state, action:"Approve"})[0].next_state

					let user_email = r.message.user_id
					var subject = `Employee Advance ${frm.doc.name}: ${frm.doc.workflow_state}`
					let message = `<table><tr><td>Employee Advance :</td><td><b>${frm.doc.name}</b></td></tr><tr><td>Status : </td><td>${frm.doc.workflow_state}</td></tr><tr><td>Amount: </td><td>${frm.doc.advance_amount}</td></tr><tr><td>Purpose: </td><td>${frm.doc.purpose.replaceAll('\n','<br>')}</td></tr><table>)}</p>`
					var xhttp = new XMLHttpRequest();
					xhttp.open("GET", `${mkt_email_api}?emailAddress=${user_email}&subject=${subject}&message=${message}`, true);
					xhttp.send();
				})
			})
				// frappe.db.set_value(frm.doc.doctype, frm.doc.name, 'verifier', frappe.user.full_name())
		}

	}
});


frappe.ui.form.on('Employee Advance Detail', {
	amount: function(frm){
		if(!frm.doc.fixed_amount){
			var total = frm.doc.details.reduce((tot, detail) => {
				return tot + detail.amount
			}, 0)
			frm.set_value("advance_amount", total)
		}
	}
});
