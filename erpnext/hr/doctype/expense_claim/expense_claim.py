# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
from cmath import nan
from pickle import FALSE
from pydoc import doc
import frappe, erpnext, json
from frappe import _
from frappe.utils import get_fullname, flt, cstr, cint
from frappe.model.document import Document
from erpnext.hr.utils import set_employee_name
from erpnext.accounts.party import get_party_account
from erpnext.accounts.general_ledger import make_gl_entries
from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account
from erpnext.controllers.accounts_controller import AccountsController
from frappe.utils.csvutils import getlink
from erpnext.accounts.utils import get_account_currency

class InvalidExpenseApproverError(frappe.ValidationError): pass
class ExpenseApproverIdentityError(frappe.ValidationError): pass

class ExpenseClaim(AccountsController):
	def onload(self):
		# self.get("__onload").make_payment_via_journal_entry = frappe.db.get_single_value('Accounts Settings',
		# 	'make_payment_via_journal_entry')
		self.get("__onload").make_payment_via_journal_entry = 1

	def validate(self):
		self.calculate_total_amount()
		self.validate_advances()
		self.validate_sanctioned_amount()
		set_employee_name(self)
		self.set_expense_account(validate=True)
		self.set_payable_account()
		self.set_cost_center()
		self.calculate_taxes()
		self.set_status()
		if self.task and not self.project:
			self.project = frappe.db.get_value("Task", self.task, "project")

	def set_status(self):
		self.status = {
			"0": "Draft",
			"1": "Submitted",
			"2": "Cancelled"
		}[cstr(self.docstatus or 0)]

		paid_amount = flt(self.total_amount_reimbursed) + flt(self.total_advance_amount)
		precision = self.precision("grand_total")
		if (self.is_paid or (flt(self.total_sanctioned_amount) > 0
			and flt(flt(self.total_sanctioned_amount) + flt(self.total_taxes_and_charges), precision) ==  flt(paid_amount, precision))) \
			and self.docstatus == 1 and self.approval_status == 'Approved':
				self.status = "Paid"
		elif flt(self.grand_total) > 0 and self.docstatus == 1 and self.approval_status == 'Approved':
			self.status = "Unpaid"
		elif self.docstatus == 1 and self.approval_status == 'Rejected':
			self.status = 'Rejected'

	def set_payable_account(self):
		if not self.payable_account and not self.is_paid:
			self.payable_account = frappe.get_cached_value('Company', self.company, 'default_expense_claim_payable_account')

	def set_cost_center(self):
		if not self.cost_center:
			self.cost_center = frappe.get_cached_value('Company', self.company, 'cost_center')

	def on_submit(self):
		if self.approval_status=="Draft":
			frappe.throw(_("""Approval Status must be 'Approved' or 'Rejected'"""))

		self.update_task_and_project()
		self.make_gl_entries()

		if self.is_paid:
			update_reimbursed_amount(self)

		self.set_status()
		self.update_claimed_amount_in_employee_advance()

	def on_cancel(self):
		self.update_task_and_project()
		if self.payable_account:
			self.make_gl_entries(cancel=True)

		if self.is_paid:
			update_reimbursed_amount(self)

		self.set_status()
		self.update_claimed_amount_in_employee_advance()

	def update_claimed_amount_in_employee_advance(self):
		for d in self.get("advances"):
			frappe.get_doc("Employee Advance", d.employee_advance).update_claimed_amount()

	def update_task_and_project(self):
		if self.task:
			self.update_task()
		elif self.project:
			frappe.get_doc("Project", self.project).update_project()

	def make_gl_entries(self, cancel=False):
		if flt(self.total_sanctioned_amount) > 0:
			gl_entries = self.get_gl_entries()
			make_gl_entries(gl_entries, cancel)

	def get_gl_entries(self):
		gl_entry = []
		self.validate_account_details()

		# payable entry
		if self.grand_total:
			gl_entry.append(
				self.get_gl_dict({
					"account": self.payable_account,
					"credit": self.grand_total,
					"credit_in_account_currency": self.grand_total,
					"against": ",".join([d.default_account for d in self.expenses]),
					"party_type": "Employee",
					"party": self.employee,
					"against_voucher_type": self.doctype,
					"against_voucher": self.name,
					"cost_center": self.cost_center
				}, item=self)
			)

		# expense entries
		for data in self.expenses:
			gl_entry.append(
				self.get_gl_dict({
					"account": data.default_account,
					"debit": data.sanctioned_amount,
					"debit_in_account_currency": data.sanctioned_amount,
					"against": self.employee,
					"cost_center": data.cost_center or self.cost_center
				}, item=data)
			)

		for data in self.advances:
			gl_entry.append(
				self.get_gl_dict({
					"account": data.advance_account,
					"credit": data.allocated_amount,
					"credit_in_account_currency": data.allocated_amount,
					"against": ",".join([d.default_account for d in self.expenses]),
					"party_type": "Employee",
					"party": self.employee,
					"against_voucher_type": "Employee Advance",
					"against_voucher": data.employee_advance
				})
			)

		self.add_tax_gl_entries(gl_entry)

		if self.is_paid and self.grand_total:
			# payment entry
			payment_account = get_bank_cash_account(self.mode_of_payment, self.company).get("account")
			gl_entry.append(
				self.get_gl_dict({
					"account": payment_account,
					"credit": self.grand_total,
					"credit_in_account_currency": self.grand_total,
					"against": self.employee
				}, item=self)
			)

			gl_entry.append(
				self.get_gl_dict({
					"account": self.payable_account,
					"party_type": "Employee",
					"party": self.employee,
					"against": payment_account,
					"debit": self.grand_total,
					"debit_in_account_currency": self.grand_total,
					"against_voucher": self.name,
					"against_voucher_type": self.doctype,
				}, item=self)
			)

		return gl_entry

	def add_tax_gl_entries(self, gl_entries):
		# tax table gl entries
		for tax in self.get("taxes"):
			gl_entries.append(
				self.get_gl_dict({
					"account": tax.account_head,
					"debit": tax.tax_amount,
					"debit_in_account_currency": tax.tax_amount,
					"against": self.employee,
					"cost_center": self.cost_center,
					"against_voucher_type": self.doctype,
					"against_voucher": self.name
				}, item=tax)
			)

	def validate_account_details(self):
		if not self.cost_center:
			frappe.throw(_("Cost center is required to book an expense claim"))

		if self.is_paid:
			if not self.mode_of_payment:
				frappe.throw(_("Mode of payment is required to make a payment").format(self.employee))

	def calculate_total_amount(self):
		self.total_claimed_amount = 0
		self.total_sanctioned_amount = 0
		for d in self.get('expenses'):
			if self.approval_status == 'Rejected':
				d.sanctioned_amount = 0.0

			self.total_claimed_amount += flt(d.amount)
			self.total_sanctioned_amount += flt(d.sanctioned_amount)

	def calculate_taxes(self):
		self.total_taxes_and_charges = 0
		for tax in self.taxes:
			if tax.rate:
				tax.tax_amount = flt(self.total_sanctioned_amount) * flt(tax.rate/100)

			tax.total = flt(tax.tax_amount) + flt(self.total_sanctioned_amount)
			self.total_taxes_and_charges += flt(tax.tax_amount)

		self.grand_total = flt(self.total_sanctioned_amount) + flt(self.total_taxes_and_charges) - flt(self.total_advance_amount)

	def update_task(self):
		task = frappe.get_doc("Task", self.task)
		task.update_total_expense_claim()
		task.save()

	def validate_advances(self):
		self.total_advance_amount = 0
		for d in self.get("advances"):
			ref_doc = frappe.db.get_value("Employee Advance", d.employee_advance,
				["posting_date", "paid_amount", "claimed_amount", "advance_account"], as_dict=1)
			d.posting_date = ref_doc.posting_date
			d.advance_account = ref_doc.advance_account
			d.advance_paid = ref_doc.paid_amount
			d.unclaimed_amount = flt(ref_doc.paid_amount) - flt(ref_doc.claimed_amount)

			if d.allocated_amount and flt(d.allocated_amount) > flt(d.unclaimed_amount):
				frappe.throw(_("Row {0}# Allocated amount {1} cannot be greater than unclaimed amount {2}")
					.format(d.idx, d.allocated_amount, d.unclaimed_amount))

			self.total_advance_amount += flt(d.allocated_amount)

		if self.total_advance_amount:
			precision = self.precision("total_advance_amount")
			if flt(self.total_advance_amount, precision) > flt(self.total_claimed_amount, precision):
				frappe.throw(_("Total advance amount cannot be greater than total claimed amount"))

			if self.total_sanctioned_amount \
					and flt(self.total_advance_amount, precision) > flt(self.total_sanctioned_amount, precision):
				frappe.throw(_("Total advance amount cannot be greater than total sanctioned amount"))

	def validate_sanctioned_amount(self):
		for d in self.get('expenses'):
			if flt(d.sanctioned_amount) > flt(d.amount):
				frappe.throw(_("Sanctioned Amount cannot be greater than Claim Amount in Row {0}.").format(d.idx))

	def set_expense_account(self, validate=False):
		for expense in self.expenses:
			if not expense.default_account or not validate:
				expense.default_account = get_expense_claim_account(expense.expense_type, self.company)["account"]

def update_reimbursed_amount(doc):
	amt = frappe.db.sql("""select ifnull(sum(debit_in_account_currency), 0) as amt
		from `tabGL Entry` where against_voucher_type = 'Expense Claim' and against_voucher = %s
		and party = %s """, (doc.name, doc.employee) ,as_dict=1)[0].amt

	doc.total_amount_reimbursed = amt
	frappe.db.set_value("Expense Claim", doc.name , "total_amount_reimbursed", amt)

	doc.set_status()
	frappe.db.set_value("Expense Claim", doc.name , "status", doc.status)


@frappe.whitelist()
def make_bank_entry_mkt(dt, dn):
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account
	from erpnext.accounts.utils import get_balance_on, get_account_currency

	expense_claim = frappe.get_doc(dt, dn)
	default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Bank")
	if not default_bank_cash_account:
		default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Cash")

	account_details = frappe.db.get_value("Account", expense_claim.cash_account,
		["account_currency", "account_type"], as_dict=1)

	payable_amount = flt(expense_claim.total_sanctioned_amount) \
		- flt(expense_claim.total_amount_reimbursed) - flt(expense_claim.total_advance_amount)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = 'Bank Entry'
	je.company = expense_claim.company
	je.remark = 'Payment against Expense Claim: ' + dn

	je.append("accounts", {
		"account": expense_claim.payable_account,
		"debit_in_account_currency": payable_amount,
		"reference_type": "Expense Claim",
		"party_type": "Employee",
		"party": expense_claim.employee,
		"cost_center": expense_claim.cost_center,
		"reference_name": expense_claim.name
	})

	je.append("accounts", {
		"account": expense_claim.cash_account,
		"credit_in_account_currency": payable_amount,
		"reference_type": "Expense Claim",
		"reference_name": expense_claim.name,
		"balance": get_balance_on(expense_claim.cash_account) ,
		"account_currency": account_details.account_currency,
		"cost_center": expense_claim.cost_center,
		"account_type": account_details.account_type
	})

	return je.as_dict()

@frappe.whitelist()
def make_bank_entry(dt, dn):
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	expense_claim = frappe.get_doc(dt, dn)
	default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Bank")
	if not default_bank_cash_account:
		default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Cash")

	payable_amount = flt(expense_claim.total_sanctioned_amount) \
		- flt(expense_claim.total_amount_reimbursed) - flt(expense_claim.total_advance_amount)

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = 'Bank Entry'
	je.company = expense_claim.company
	je.remark = 'Payment against Expense Claim: ' + dn

	je.append("accounts", {
		"account": expense_claim.payable_account,
		"debit_in_account_currency": payable_amount,
		"reference_type": "Expense Claim",
		"party_type": "Employee",
		"party": expense_claim.employee,
		"cost_center": erpnext.get_default_cost_center(expense_claim.company),
		"reference_name": expense_claim.name
	})

	je.append("accounts", {
		"account": default_bank_cash_account.account,
		"credit_in_account_currency": payable_amount,
		"reference_type": "Expense Claim",
		"reference_name": expense_claim.name,
		"balance": default_bank_cash_account.balance,
		"account_currency": default_bank_cash_account.account_currency,
		"cost_center": erpnext.get_default_cost_center(expense_claim.company),
		"account_type": default_bank_cash_account.account_type
	})

	return je.as_dict()


# @frappe.whitelist()
# def make_bank_entry(dt, dn):
# 	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

# 	expense_claim = frappe.get_doc(dt, dn)
# 	default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Bank")
# 	if not default_bank_cash_account:
# 		default_bank_cash_account = get_default_bank_cash_account(expense_claim.company, "Cash")

# 	payable_amount = flt(expense_claim.total_sanctioned_amount) \
# 		- flt(expense_claim.total_amount_reimbursed) - flt(expense_claim.total_advance_amount)

# 	je = frappe.new_doc("Journal Entry")
# 	je.voucher_type = 'Bank Entry'
# 	je.company = expense_claim.company
# 	je.remark = 'Payment against Expense Claim: ' + dn

# 	je.append("accounts", {
# 		"account": expense_claim.payable_account,
# 		"debit_in_account_currency": payable_amount,
# 		"reference_type": "Expense Claim",
# 		"party_type": "Employee",
# 		"party": expense_claim.employee,
# 		"cost_center": erpnext.get_default_cost_center(expense_claim.company),
# 		"reference_name": expense_claim.name
# 	})

# 	je.append("accounts", {
# 		"account": default_bank_cash_account.account,
# 		"credit_in_account_currency": payable_amount,
# 		"reference_type": "Expense Claim",
# 		"reference_name": expense_claim.name,
# 		"balance": default_bank_cash_account.balance,
# 		"account_currency": default_bank_cash_account.account_currency,
# 		"cost_center": erpnext.get_default_cost_center(expense_claim.company),
# 		"account_type": default_bank_cash_account.account_type
# 	})

# 	return je.as_dict()

@frappe.whitelist()
def get_expense_claim_account(expense_claim_type, company):
	account = frappe.db.get_value("Expense Claim Account",
		{"parent": expense_claim_type, "company": company}, "default_account")
	if not account:
		frappe.throw(_("Please set default account in Expense Claim Type {0}")
			.format(expense_claim_type))

	return {
		"account": account
	}

@frappe.whitelist()
def get_advances(employee, advance_id=None):
	if not advance_id:
		condition = 'docstatus=1 and employee={0} and paid_amount > 0 and paid_amount > claimed_amount + return_amount'.format(frappe.db.escape(employee))
	else:
		condition = 'name={0}'.format(frappe.db.escape(advance_id))

	return frappe.db.sql("""
		select
			name, posting_date, paid_amount, claimed_amount, advance_account, travel_request
		from
			`tabEmployee Advance`
		where {0}
	""".format(condition), as_dict=1)


@frappe.whitelist()
def get_expense_claim(
	employee_name, company, employee_advance_name, posting_date, paid_amount, claimed_amount, travel_request=None, approver_1=None, approver_2=None, approver_3=None):
	default_payable_account = frappe.get_cached_value('Company',  company,  "default_payable_account")
	default_cost_center = frappe.get_cached_value('Company',  company,  'cost_center')

	expense_claim = frappe.new_doc('Expense Claim')
	expense_claim.company = company
	expense_claim.employee = employee_name
	expense_claim.payable_account = default_payable_account
	expense_claim.cost_center = default_cost_center
	expense_claim.travel_request = travel_request
	expense_claim.is_paid = 1 if flt(paid_amount) else 0
	expense_claim.approver_1= approver_1
	expense_claim.approver_2= approver_2
	expense_claim.approver_3= approver_3
	expense_claim.append(
		'advances',
		{
			'employee_advance': employee_advance_name,
			'posting_date': posting_date,
			'advance_paid': flt(paid_amount),
			'unclaimed_amount': flt(paid_amount) - flt(claimed_amount),
			'allocated_amount': flt(paid_amount) - flt(claimed_amount)
		}
	)

	return expense_claim

@frappe.whitelist()
def create_expense_claim_dm(employee, approver_1=None, approver_2=None, approver_3=None, travel_request=None, expenses=[], remark="", advances=[], file_urls =  None, cost_center = None):
	if not employee:
		return {"error": "employee not defined"}

	doc = create_expense_claim( employee, approver_1, approver_2, approver_3, travel_request, expenses, remark, advances, cost_center)

	doc.insert(ignore_permissions=True)
	doc.db_set("workflow_state", "Submitted")
	for file_url in file_urls:
		attach_bills(doc, file_url)
	frappe.db.commit()
	r = frappe.request
	return doc

@frappe.whitelist()
def create_expense_claim_spv(employee, approver_1=None, approver_2=None, approver_3=None, travel_request=None, expenses=[], remark="", advances=[], file_urls = None, cost_center = None):
	if not employee:
		return {"error": "employee not defined"}

	doc = create_expense_claim( employee, approver_1, approver_2, approver_3, travel_request, expenses, remark, advances, cost_center)
	doc.insert(ignore_permissions=True)
	if not approver_2:
		doc.db_set("workflow_state", "Received CSD")
	else:
		doc.db_set("workflow_state", "Approved 1")

	for file_url in file_urls:
		attach_bills(doc, file_url)
	frappe.db.commit()
	return doc


def create_expense_claim(employee, approver_1=None, approver_2=None, approver_3=None, travel_request=None, expenses=[], remark="", advances=[], cost_center = None):
	company = frappe.db.get_single_value('Global Defaults', 'default_company')
	print(company)
	default_payable_account = frappe.get_cached_value('Company',  company,  "default_payable_account")
	print(default_payable_account)
	company_abbr = frappe.get_cached_value('Company',  company,  "abbr")
	default_cost_center = "MKT - " + company_abbr
	print(default_cost_center)

	expense_claim = frappe.new_doc('Expense Claim')
	expense_claim.company = company
	expense_claim.employee = employee
	expense_claim.approver_1 = approver_1
	expense_claim.approver_2 = approver_2
	expense_claim.approver_3 = approver_3
	expense_claim.travel_request = travel_request
	expense_claim.payable_account = default_payable_account
	expense_claim.cost_center = cost_center if cost_center else default_cost_center

	for advance in json.loads(advances): #json.loads(
		expense_claim.append("advances", advance)
	expense_claim.remark = remark
	for expense in json.loads(expenses):
		expense_claim.append("expenses", expense)

	return expense_claim


def attach_bills(expense_claim, file_url):
	if not file_url:
		return

	files = []
	is_private = False
	doctype = "Expense Claim"
	docname = expense_claim.name
	fieldname = None
	file_url = file_url
	folder = "Home/Attachments"
	method = None
	content = None
	filename = None

	if 'file' in files:
		file = files['file']
		content = file.stream.read()
		filename = file.filename

	frappe.local.uploaded_file = content
	frappe.local.uploaded_filename = filename

	if frappe.session.user == 'Guest':
		import mimetypes
		filetype = mimetypes.guess_type(filename)[0]
		if filetype not in ['image/png', 'image/jpeg', 'application/pdf']:
			frappe.throw("You can only upload JPG, PNG or PDF files.")

	ret = frappe.get_doc({
		"doctype": "File",
		"attached_to_doctype": doctype,
		"attached_to_name": docname,
		"attached_to_field": fieldname,
		"folder": folder,
		"file_name": filename,
		"file_url": file_url,
		"is_private": cint(is_private),
		"content": content
	})
	ret.save(ignore_permissions=True)
	return ret


@frappe.whitelist()
def get_expense_claim_list(username):
	employee = frappe.db.get_list("Employee", filters={"user_id" : username})

	if len(employee) == 0:
		return {"error": "Employee not found"}
	# ec.workflow_state, ec.travel_request, ec.approver_1, ec.approver_2, ec.approver_3, ec.approval_status, ec.is_paid

	ecs = frappe.db.get_list("Expense Claim",
		filters={
        	'employee': employee[0].name
    	},
		fields=['name', 'posting_date', 'employee_name', 'status', 'total_sanctioned_amount', 'mode_of_payment', 'total_advance_amount', 'remark', 'total_claimed_amount', 'approval_status', 'total_amount_reimbursed', 'workflow_state', 'approver_1', 'approver_2', 'travel_request'],
	)

	for ec in ecs:
		ec.expenses = frappe.db.get_all('Expense Claim Detail',
				filters={ 'parent': ec.name },
				fields=['expense_date','expense_type', 'default_account', 'description', 'amount', 'sanctioned_amount', 'cost_center']
			)
		if ec.travel_request :
			ec.itinerary =  frappe.db.get_all('Travel Itinerary',
				filters={ 'parent': ec.travel_request },
				fields=['travel_to','travel_from', 'arrival_date', 'departure_date']
			)
	return ecs
