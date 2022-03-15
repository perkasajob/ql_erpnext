# -*- coding: utf-8 -*-
# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, erpnext, json
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, nowdate
from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

class EmployeeAdvanceOverPayment(frappe.ValidationError):
	pass

class EmployeeAdvance(Document):
	def onload(self):
		self.get("__onload").make_payment_via_journal_entry = frappe.db.get_single_value('Accounts Settings',
			'make_payment_via_journal_entry')

		ea = frappe.db.sql('''
			select ea.name
			from `tabEmployee Advance` ea
			where not exists (
				select * from `tabExpense Claim Advance` eca where eca.employee_advance = ea.name
			) and ea.employee=%s
			''', (self.employee))
		if len(ea) > 3 and self.workflow_state == "Received CSD":
			frappe.msgprint( ", ".join('<a href="./desk#Form/Employee%20Advance/{0}">{0}</a>'.format(a[0]) for a in ea),"Still have {} Expense Claim pending".format(len(ea)))


	def validate(self):
		self.set_status()
		self.validate_employee_advance_account()
		# self.calculate_detail_values()

	def on_cancel(self):
		self.set_status()

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
		if self.docstatus == 1:
			if self.claimed_amount and flt(self.claimed_amount) == flt(self.paid_amount):
				self.status = "Claimed"
			elif self.paid_amount and self.advance_amount == flt(self.paid_amount):
				self.status = "Paid"
			else:
				self.status = "Unpaid"
		elif self.docstatus == 2:
			self.status = "Cancelled"

	def validate_employee_advance_account(self):
		company_currency = erpnext.get_company_currency(self.company)
		if (self.advance_account and
			company_currency != frappe.db.get_value('Account', self.advance_account, 'account_currency')):
			frappe.throw(_("Advance account currency should be same as company currency {0}")
				.format(company_currency))

	def set_total_advance_paid(self):
		paid_amount = frappe.db.sql("""
			select ifnull(sum(debit_in_account_currency), 0) as paid_amount
			from `tabGL Entry`
			where against_voucher_type = 'Employee Advance'
				and against_voucher = %s
				and party_type = 'Employee'
				and party = %s
		""", (self.name, self.employee), as_dict=1)[0].paid_amount

		return_amount = frappe.db.sql("""
			select name, ifnull(sum(credit_in_account_currency), 0) as return_amount
			from `tabGL Entry`
			where against_voucher_type = 'Employee Advance'
				and voucher_type != 'Expense Claim'
				and against_voucher = %s
				and party_type = 'Employee'
				and party = %s
		""", (self.name, self.employee), as_dict=1)[0].return_amount

		if flt(paid_amount) > self.advance_amount:
			frappe.throw(_("Row {0}# Paid Amount cannot be greater than requested advance amount"),
				EmployeeAdvanceOverPayment)

		if flt(return_amount) > self.paid_amount - self.claimed_amount:
			frappe.throw(_("Return amount cannot be greater unclaimed amount"))

		self.db_set("paid_amount", paid_amount)
		self.db_set("return_amount", return_amount)
		self.set_status()
		frappe.db.set_value("Employee Advance", self.name , "status", self.status)


	def update_claimed_amount(self):
		claimed_amount = frappe.db.sql("""
			SELECT sum(ifnull(allocated_amount, 0))
			FROM `tabExpense Claim Advance` eca, `tabExpense Claim` ec
			WHERE
				eca.employee_advance = %s
				AND ec.approval_status="Approved"
				AND ec.name = eca.parent
				AND ec.docstatus=1
				AND eca.allocated_amount > 0
		""", self.name)[0][0] or 0

		frappe.db.set_value("Employee Advance", self.name, "claimed_amount", flt(claimed_amount))
		self.reload()
		self.set_status()
		frappe.db.set_value("Employee Advance", self.name, "status", self.status)

	def calculate_detail_values(self):
		if not self.advance_amount:
			self.advance_amount = 0
			for item in self.get("details"):
				self.advance_amount = self.advance_amount + item.amount

@frappe.whitelist()
def get_pending_amount(employee, posting_date):
	employee_due_amount = frappe.get_all("Employee Advance", \
		filters = {"employee":employee, "docstatus":1, "posting_date":("<=", posting_date)}, \
		fields = ["advance_amount", "paid_amount"])
	return sum([(emp.advance_amount - emp.paid_amount) for emp in employee_due_amount])

@frappe.whitelist()
def make_bank_entry(dt, dn):
	doc = frappe.get_doc(dt, dn)
	payment_account = get_default_bank_cash_account(doc.company, account_type="Cash",
		mode_of_payment=doc.mode_of_payment)

	je = frappe.new_doc("Journal Entry")
	je.posting_date = nowdate()
	je.voucher_type = 'Bank Entry'
	je.company = doc.company
	je.remark = 'Payment against Employee Advance: ' + dn + '\n' + doc.purpose
	cost_center = doc.cost_center if doc.cost_center else erpnext.get_default_cost_center(doc.company)

	je.append("accounts", {
		"account": doc.advance_account,
		"debit_in_account_currency": flt(doc.advance_amount),
		"reference_type": "Employee Advance",
		"reference_name": doc.name,
		"party_type": "Employee",
		"cost_center": cost_center,
		"party": doc.employee,
		"is_advance": "Yes"
	})

	je.append("accounts", {
		"account": payment_account.account,
		"cost_center": cost_center,
		"credit_in_account_currency": flt(doc.advance_amount),
		"account_currency": payment_account.account_currency,
		"account_type": payment_account.account_type
	})

	return je.as_dict()

@frappe.whitelist()
def make_return_entry(employee, company, employee_advance_name,
		return_amount, advance_account, mode_of_payment=None):
	return_account = get_default_bank_cash_account(company, account_type='Cash', mode_of_payment = mode_of_payment)

	mode_of_payment_type = ''
	if mode_of_payment:
		mode_of_payment_type = frappe.get_cached_value('Mode of Payment', mode_of_payment, 'type')
		if mode_of_payment_type not in ["Cash", "Bank"]:
			# if mode of payment is General then it unset the type
			mode_of_payment_type = None

	je = frappe.new_doc('Journal Entry')
	je.posting_date = nowdate()
	# if mode of payment is Bank then voucher type is Bank Entry
	je.voucher_type = '{} Entry'.format(mode_of_payment_type) if mode_of_payment_type else 'Cash Entry'
	je.company = company
	je.remark = 'Return against Employee Advance: ' + employee_advance_name

	je.append('accounts', {
		'account': advance_account,
		'credit_in_account_currency': return_amount,
		'reference_type': 'Employee Advance',
		'reference_name': employee_advance_name,
		'party_type': 'Employee',
		'party': employee,
		'is_advance': 'Yes'
	})

	je.append("accounts", {
		"account": return_account.account,
		"debit_in_account_currency": return_amount,
		"account_currency": return_account.account_currency,
		"account_type": return_account.account_type
	})

	return je.as_dict()


@frappe.whitelist(allow_guest=True)
def set_workflow(expense_claim):
	ec = frappe.get_doc("Expense Claim", expense_claim)
	for ea in ec.advances:
		ea = frappe.get_doc("Employee Advance", ea.employee_advance)

		if ea.workflow_state == "Booked" :
			if (ea.paid_amount - ea.claimed_amount - ea.return_amount) > 1:
				ea.db_set("workflow_state", "Return Required", commit=True)
			else:
				ea.db_set("workflow_state", "Finished", commit=True)

	return ea

def update_employee_advance_return(employee_advance):
	ea = frappe.get_doc("Employee Advance", employee_advance)

	if ea.workflow_state == "Return Required" and (ea.paid_amount - ea.claimed_amount - ea.return_amount) < 1 :
		ea.db_set("workflow_state", "Finished", commit=True)


@frappe.whitelist()
def get_employee_by_email(email=None):
	if(email):
		return frappe.db.get_list('Employee',
			filters={
				'user_id': email,
				'status': 'Active'
			},
			fields=['name','employee_name', 'status', 'department', 'grade', 'designation']
		)

	return {"error":"no user email"}


@frappe.whitelist()
def create_employee_advance_dm(employee, purpose, advance_amount, is_upc=0, approver_1="", approver_2="", advance_account=None, details=None, itinerary=[], costings=[], description=None, purpose_of_travel=None, cost_center=None):
	travel_request = None
	if not employee:
		return {"error": "employee not defined"}
	if not approver_1:
		return {"error": "Approver 1 is required"}

	if is_upc:
		travel_request = create_travel_request(employee, approver_1, itinerary, costings, description, purpose_of_travel, cost_center)
		travel_request.insert(ignore_permissions=True)
		travel_request.db_set("workflow_state", "Submitted")
		frappe.db.commit()

	doc = create_employee_advance(employee, purpose, travel_request, advance_amount, approver_1, approver_2, advance_account, details, cost_center)
	doc.insert(ignore_permissions=True)
	doc.db_set("workflow_state", "Submitted")
	frappe.db.commit()

	return {'employee_advance' : doc, 'travel_request': travel_request}

@frappe.whitelist()
def create_employee_advance_spv(employee, purpose, advance_amount, is_upc=0, approver_1="", approver_2="", advance_account=None, details=None, itinerary=[], costings=[], description=None, purpose_of_travel=None, cost_center=None):
	travel_request = None
	if not employee:
		return {"error": "employee not defined"}

	if is_upc:
		travel_request = create_travel_request(employee, approver_1, itinerary, costings, description, purpose_of_travel, cost_center)
		travel_request.insert(ignore_permissions=True)
		travel_request.db_set("workflow_state", "Approved")
		frappe.db.commit()

	doc = create_employee_advance(employee, purpose, travel_request, advance_amount, approver_1, approver_2, advance_account, details, cost_center)
	doc.insert(ignore_permissions=True)
	if not approver_2:
		doc.db_set("workflow_state", "Received CSD")
	else:
		doc.db_set("workflow_state", "Approved 1")

	frappe.db.commit()
	return {'employee_advance' : doc, 'travel_request': travel_request}

def create_employee_advance(employee, purpose, travel_request=None, advance_amount=0, approver_1="", approver_2="", advance_account=None, details=None, cost_center=None ):
	doc = frappe.new_doc("Employee Advance")
	doc.employee = employee
	doc.purpose = purpose
	if travel_request:
		doc.travel_request = travel_request.name
	doc.advance_amount = advance_amount
	doc.approver_1 = approver_1
	doc.approver_2 = approver_2
	doc.advance_account = advance_account
	doc.cost_center = cost_center
	for detail in json.loads(details):
		doc.append('details', detail)
	return doc

@frappe.whitelist()
def get_employee_advance_approved_list(username):
	employee = frappe.db.get_list("Employee", filters={"user_id" : username})

	if len(employee) == 0:
		return {"error": "Employee not found"}

	return get_employee_advance_list(username, condition= " and ea.docstatus=1")


@frappe.whitelist()
def get_employee_advance_list(username, condition= ""):
	employee = frappe.db.get_list("Employee", filters={"user_id" : username})

	if len(employee) == 0:
		return {"error": "Employee not found"}

	ea = frappe.db.sql('''
		select ea.name, ea.purpose, ea.approver_1, ea.approver_2, ea.approver_3, ea.advance_amount, ea.paid_amount, ea.posting_date, ea.travel_request, ea.workflow_state, ea.status
		from `tabEmployee Advance` ea
		where not exists (
			select * from `tabExpense Claim Advance` eca where eca.employee_advance = ea.name
		) and ea.employee=%s {condition}
		group by ea.name
	'''.format(condition=condition), (employee[0].name), as_dict = 1)

	for e in ea:
		e.itinerary = []
		if e.travel_request :
			e.itinerary =  frappe.db.get_all('Travel Itinerary',
				filters={ 'parent': e.travel_request },
				fields=['travel_to','travel_from', 'arrival_date', 'departure_date']
			)
		e.details = frappe.db.get_all('Employee Advance Detail',
			filters={ 'parent': e.name},
			fields=['expense_date','expense_type', 'description', 'amount']
			)
	return ea


@frappe.whitelist()
def get_employee_advance_all(username, condition= ""):
	employee = frappe.db.get_list("Employee", filters={"user_id" : username})

	if len(employee) == 0:
		return {"error": "Employee not found"}

	ea = frappe.db.sql('''
		select ea.name, ea.purpose, ea.approver_1, ea.approver_2, ea.approver_3, ea.advance_amount, ea.paid_amount, ea.posting_date, ea.travel_request, ea.workflow_state, ea.status
		from `tabEmployee Advance` ea
		where ea.employee=%s {condition} and posting_date > DATE_SUB(CURDATE(), INTERVAL 3 MONTH)
	'''.format(condition=condition), (employee[0].name), as_dict = 1)

	for e in ea:
		e.itinerary = []
		if e.travel_request :
			e.itinerary =  frappe.db.get_all('Travel Itinerary',
				filters={ 'parent': e.travel_request },
				fields=['travel_to','travel_from', 'arrival_date', 'departure_date']
			)
		e.details = frappe.db.get_all('Employee Advance Detail',
			filters={ 'parent': e.name},
			fields=['expense_date','expense_type', 'description', 'amount']
			)
	return ea

@frappe.whitelist()
def test_employee_advance_list(username, condition= ""):
	employee = frappe.db.get_list("Employee", filters={"user_id" : username})

	if len(employee) == 0:
		return {"error": "Employee not found"}

	ea = frappe.db.sql('''
		select ea.name, ea.purpose, ea.approver_1, ea.approver_2, ea.approver_3, ea.advance_amount, ea.paid_amount, ea.posting_date, ea.travel_request, ea.workflow_state, ea.status
		from `tabEmployee Advance` ea
		where not exists (
			select * from `tabExpense Claim Advance` eca where eca.employee_advance = ea.name
		) and ea.employee=%s {condition}
		group by ea.name
	'''.format(condition=condition), (employee[0].name), as_dict = 1)

	for e in ea:
		e.itinerary = []
		if e.travel_request :
			e.itinerary =  frappe.db.get_all('Travel Itinerary',
				filters={ 'parent': e.travel_request },
				fields=['travel_to','travel_from', 'arrival_date', 'departure_date']
			)
		e.details = frappe.db.get_all('Employee Advance Detail',
			filters={ 'parent': e.name},
			fields=['expense_date','expense_type', 'description', 'amount']
			)
	return ea


def create_travel_request(employee, approver_1, itinerary=[], costings=[], description=None, purpose_of_travel=None, cost_center=None):
	if not itinerary:
		return None

	company = frappe.db.get_single_value('Global Defaults', 'default_company')
	print(company)
	default_payable_account = frappe.get_cached_value('Company',  company,  "default_payable_account")
	print(default_payable_account)
	company_abbr = frappe.get_cached_value('Company',  company,  "abbr")
	default_cost_center = "MKT - " + company_abbr
	print(default_cost_center)

	travel_request = frappe.new_doc('Travel Request')
	travel_request.employee = employee
	travel_request.approver_1 = approver_1
	travel_request.travel_type = "Domestic"
	travel_request.travel_funding = "Require Full Funding"
	travel_request.purpose_of_travel = purpose_of_travel
	travel_request.description = description
	travel_request.cost_center = cost_center
	# travel_request.payable_account = default_payable_account
	travel_request.cost_center = cost_center
	for itin in json.loads(itinerary):
		print(itin)
		travel_request.append("itinerary", itin)
	for costing in json.loads(costings):
		print(costing)
		travel_request.append("costings", costing)

	return travel_request

@frappe.whitelist()
def get_pending_advances(employee):
	return frappe.db.sql('''
			select ea.name
			from `tabEmployee Advance` ea
			where not exists (
				select * from `tabExpense Claim Advance` eca where eca.employee_advance = ea.name
			) and ea.employee=%s
			''', (employee))