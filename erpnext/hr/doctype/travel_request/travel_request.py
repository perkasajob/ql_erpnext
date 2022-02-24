# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe, json
from frappe.model.document import Document

class TravelRequest(Document):
	pass

@frappe.whitelist()
def get_travel_requests(employee):
	if not employee:
		return {"error": "employee not defined"}

	return frappe.db.get_list('Travel Request',
			filters={
				'employee': employee,
				'docstatus': 1
			},
			fields=['name','workflow_state', 'description']
		)


@frappe.whitelist(allow_guest=True)
def get_employee_holiday_list(employee, itinerary=[], costings=[], description=None, purpose_of_travel=None):
	if not employee:
		return {"error": "employee not defined"}

	holidays = self.get_holidays_for_employee(self.start_date, self.end_date)
	actual_lwp = self.calculate_lwp(holidays, working_days)

	return doc

@frappe.whitelist(allow_guest=True)
def create_travel_request_dm(employee, itinerary=[], costings=[], description=None, purpose_of_travel=None):
	if not employee:
		return {"error": "employee not defined"}

	doc = create_travel_request(employee, itinerary, costings, description, purpose_of_travel)
	doc.insert(ignore_permissions=True)
	doc.db_set("workflow_state", "Submitted")
	frappe.db.commit()
	r = frappe.request
	return doc

@frappe.whitelist(allow_guest=True)
def create_travel_request_spv(employee, itinerary=[], costings=[], description=None, purpose_of_travel=None):
	if not employee:
		return {"error": "employee not defined"}

	doc = create_travel_request(employee, itinerary, costings, description, purpose_of_travel)
	doc.insert(ignore_permissions=True)
	doc.db_set("workflow_state", "Approved")
	frappe.db.commit()
	return doc


def create_travel_request(employee, itinerary=[], costings=[], description=None, purpose_of_travel=None):
	print(employee)
	print(costings)
	print(purpose_of_travel)
	print(description)
	print(itinerary)

	print("==========================================")

	company = frappe.db.get_single_value('Global Defaults', 'default_company')
	print(company)
	default_payable_account = frappe.get_cached_value('Company',  company,  "default_payable_account")
	print(default_payable_account)
	company_abbr = frappe.get_cached_value('Company',  company,  "abbr")
	default_cost_center = "MKT - " + company_abbr
	print(default_cost_center)

	travel_request = frappe.new_doc('Travel Request')
	travel_request.employee = employee
	travel_request.travel_type = "Domestic"
	travel_request.travel_funding = "Require Full Funding"
	travel_request.purpose_of_travel = purpose_of_travel
	travel_request.description = description
	# travel_request.payable_account = default_payable_account
	travel_request.cost_center = default_cost_center
	for itin in json.loads(itinerary):
		print(itin)
		travel_request.append("itinerary", itin)
	for costing in json.loads(costings):
		print(costing)
		travel_request.append("costings", costing)


	return travel_request