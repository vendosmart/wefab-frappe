# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import json

import frappe
from frappe.model.document import Document
from frappe.utils.jinja import validate_template


class EmailTemplate(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		response: DF.TextEditor | None
		response_html: DF.Code | None
		subject: DF.Data
		use_html: DF.Check
	# end: auto-generated types

	@property
	def response_(self):
		return self.response_html if self.use_html else self.response

	def validate(self):
		validate_template(self.subject)
		validate_template(self.response_)
		# Update HTML preview
		self.update_html_preview()

	def update_html_preview(self):
		"""Generate HTML preview of the email template"""
		if not self.response_html and not self.response:
			self.html_preview = ""
			return
		
		# Get the content based on use_html flag
		content = self.response_html if self.use_html else self.response
		
		if not content:
			self.html_preview = ""
			return
		
		# Render with dummy context
		context = _flatten_preview_context(_build_dummy_context())
		try:
			rendered_subject = frappe.render_template(self.subject or "", context)
		except Exception:
			rendered_subject = self.subject or ""
		try:
			rendered_content = frappe.render_template(content or "", context)
		except Exception:
			rendered_content = content or ""
		
		# Create the preview HTML
		preview_html = f"""
		<div style="border: 1px solid #d1d8dd; border-radius: 4px; padding: 15px; background-color: #f9fafb; margin-top: 10px;">
			<div style="margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #d1d8dd;">
				<strong style="color: #2e3338;">Subject:</strong> 
				<span style="color: #5e64ff;">{frappe.utils.escape_html(rendered_subject)}</span>
			</div>
			<div style="background-color: white; padding: 15px; border-radius: 4px; border: 1px solid #e3e8ee;">
				<div style="color: #6c7680; font-size: 12px; margin-bottom: 10px;">
					<strong>Template Preview:</strong> (Variables will be replaced with actual values when email is sent)
				</div>
				<div style="border-top: 1px solid #e3e8ee; padding-top: 10px;">
					{rendered_content}
				</div>
			</div>
			<div style="margin-top: 10px; padding: 8px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 3px;">
				<small style="color: #856404;">
					<strong>Note:</strong> This is a preview of the template. 
					Jinja variables (like <code>{{{{ variable }}}}</code>) will be replaced with actual values when the email is sent.
				</small>
			</div>
		</div>
		"""
		
		self.html_preview = preview_html

	def get_formatted_subject(self, doc):
		return frappe.render_template(self.subject, doc)

	def get_formatted_response(self, doc):
		return frappe.render_template(self.response_, doc)

	def get_formatted_email(self, doc):
		if isinstance(doc, str):
			doc = json.loads(doc)

		return {
			"subject": self.get_formatted_subject(doc),
			"message": self.get_formatted_response(doc),
		}


@frappe.whitelist()
def get_email_template(template_name, doc):
	"""Returns the processed HTML of a email template with the given doc"""

	email_template = frappe.get_doc("Email Template", template_name)
	return email_template.get_formatted_email(doc)

# Add near the top (module-level)
def _build_dummy_context():
	return {
		"doc": {
			"intake_name": "Intake Testing",
			"creation_date": "07 Oct 2025",
			"name": "DUMMY-0001",
			"quotation_name": "QTN-0001",
			"customer_id": "CUST-0001",
			"customer_rfq_id": "CRFQ-0001",
			"rfq_name": "RFQ-0001",
			"rfq_id": "RFQ-0001",
			"po_name": "PO-0001",
			"po_status": "Open",
			"enquiry": "ENQ-0001",
			"enquiry_reference": "ENQ-0001",
			"project": "Demo Project",
			"project_name": "Demo Project",
			"description": "Demo description",
			"target_completion": "2025-10-07",
			"expiry_date": "2025-12-31",
			"requested_delivery_date": "2025-10-20",
			"promised_delivery_date": "2025-10-25",
			"actual_delivery_date": "2025-10-28",
			"delivery_date": "2025-10-22",
			"delivery_address": "123 Demo St",
			"delivery_full_address": "123 Demo St, City, State, Country",
			"full_delivery_address": "123 Demo St, City, State, Country",
			"billing_address": "456 Billing Ave",
			"full_billing_address": "456 Billing Ave, City, State, Country",
			"special_instructions": "Handle with care",
			"payment_terms": "Net 30",
			"delivery_terms": "FOB",
			"shipping_method": "Air",
			"shipping_terms": "CIF",
			"shipping_notes": "Call before delivery",
			"shipment_tracking_number": "TRACK123",
			"carrier_name": "DHL",
			"shipment_date": "2025-10-18",
			"estimated_arrival_date": "2025-10-23",
			"currency_code": "USD",
			"total_estimated_amount": 1000,
			"sub_total": 900,
			"discount_type": "Percentage",
			"discount": 10,
			"discount_amount": 90,
			"total_tooling": 50,
			"total_miscellaneous": 20,
			"total_tax_amount": 72,
			"shipping_charges": 30,
			"grand_total": 932,
			"quality_requirements": "ISO 9001",
			"packaging_requirements": "Standard Packaging",
			"general_terms": "Standard terms apply",
			"technical_requirements": "Tolerance +/- 0.1mm",
			"quality_standards": "ASTM",
			"custom_quality_requirements": "CoA required",
			"priority": "Medium",
			"status": "Draft",
			"workflow_state": "Pending",
			"from_address": "from@demo.com",
			"to_address": "to@demo.com",
			"quotation_from": "Wefab",
			"quotation_to": "Customer Inc.",
			"estimated_completion_duration": "7 days",
			"validity": "2025-11-30",
			"notes": "Thank you for your business",
			"customer_name": "Customer Inc.",
			"supplier_id": "SUP-0001",
			"supplier_name": "Supplier Co.",
			"customer_po_number": "CPO-123",
			"rfq_reference": "RFQ-0001",
			"wefab_quotation": "WQ-0001",
			"current_date": "2025-10-07",
			"customer_rfq_name": "CRFQ-0001",
			"rfq_date": "2025-10-01",
			"required_by_date": "2025-10-15",
			"contact_email": "customer@demo.com",
			"contact_phone": "+1-111-222-3333",
			"items": [
				{
					"item_code": "ITM-001",
					"item_description": "Aluminum Bracket",
					"material": "Aluminum",
					"quantity": 10,
					"unit": "Nos",
					"unit_price": 50,
					"total_price": 500,
					"miscellaneous": 5,
					"tooling": 10,
					"tax_type": "GST",
					"tax_amount": 45,
					"comments": "No sharp edges"
				}
			],
			"rfq_participant_suppliers": [
				{"supplier_email_id": "supplier1@demo.com"},
				{"supplier_email_id": "supplier2@demo.com"}
			],
			"attachments": [],
			"clarifications": [],
			"drawing_files_s3_paths": [],
			"view_rfq_link": "/demo/rfq",
			"view_po_link": "/demo/po",
			"view_customer_po_link": "/demo/cpo",
			"rfq_id_only": "RFQ-0001"
		},
		# Explicit top-level duplicates so templates using {{ key }} work without doc.*
		"supplier_name": "Supplier Co.",
		"po_status": "Open",
		"customer_rfq_name": "CRFQ-0001",
		"rfq_date": "2025-10-01",
		"required_by_date": "2025-10-15",
		"contact_email": "customer@demo.com",
		"contact_phone": "+1-111-222-3333",
		"delivery_address": "123 Demo St",
		"full_delivery_address": "123 Demo St, City, State, Country",
		"delivery_full_address": "123 Demo St, City, State, Country",
		"rfq_id_only": "RFQ-0001",
		"customer_name": "Customer Inc.",
		"supplier_data": {
			"company_name": "Supplier Co.",
			"name": "SUP-0001",
			"contact_person": "Jane Supplier",
			"city": "City",
			"state": "State",
			"country": "Country",
			"location": "HQ",
			"creation_date": "2025-10-01",
			"modified_date": "2025-10-06",
			"primary_email_id": "supplier@demo.com",
			"primary_phone_number": "+1-234-567-890",
			"onboarding_status": "In Review",
			"onboarding_form_status": "Submitted",
			"portal_url": "/demo/supplier",
			"machine_capabilities": "CNC, Milling",
			"facility_verification": "Pending",
			"financial_information": "ND",
			"additional_information": "Preferred vendor",
			"auction_id": "AUC-0001",
			"supplier_auction_id": "SAUC-0001",
			"auction_name": "Auction-Alpha",
			"po_name": "PO-0001",
			"items": "3",
			"selected_supplier_name": "Supplier Co.",
			"selected_supplier_email": "supplier@demo.com",
			"promised_delivery_date": "2025-10-25",
			"auction_opened_at": "2025-10-05 10:00",
			"auction_closed_at": "2025-10-06 10:00",
			"last_activity": "2025-10-06 09:45",
			"delivery_full_address": "123 Demo St",
			"supplier_full_address": "789 Supplier Rd",
			"supplier_quotation": "SQ-0001",
			"payment_method": "Wire",
			"shipping_method": "Air",
			"customer_po_number": "CPO-123",
			"auction_validity": "2025-11-30",
			"currency_code": "USD",
			"grand_total": 932,
			"flag": 0
		},
		"customer_data": {
			"portal_url": "/demo/customer"
		},
		"portal_url": "/demo",
	}

def _flatten_preview_context(ctx: dict) -> dict:
	"""For preview: expose doc.* as top-level keys so templates using {{ field }} work.
	Does not overwrite explicitly provided top-level keys.
	"""
	try:
		flat = dict(ctx or {})
		doc = flat.get("doc") or {}
		if isinstance(doc, dict):
			for k, v in doc.items():
				flat.setdefault(k, v)
		return flat
	except Exception:
		return ctx

# In get_preview(), render templates with the same dummy context
@frappe.whitelist()
def get_preview(docname):
	"""
	Server-side method to get email template preview
	"""
	if not docname or docname == "new-email-template-1":
		return {
			"preview_html": "",
			"subject": ""
		}
	
	try:
		doc = frappe.get_doc("Email Template", docname)
		
		# Get the content based on use_html flag
		content = doc.response_html if doc.use_html else doc.response
		
		if not content:
			return {
				"preview_html": "",
				"subject": doc.subject or ""
			}
		
		# Render with dummy context
		context = _flatten_preview_context(_build_dummy_context())
		try:
			rendered_subject = frappe.render_template(doc.subject or "", context)
		except Exception:
			rendered_subject = doc.subject or ""
		try:
			rendered_content = frappe.render_template(content or "", context)
		except Exception:
			rendered_content = content or ""
		
		# Create the preview HTML
		preview_html = f"""
		<div style="border: 1px solid #d1d8dd; border-radius: 4px; padding: 15px; background-color: #f9fafb; margin-top: 10px;">
			<div style="margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #d1d8dd;">
				<strong style="color: #2e3338;">Subject:</strong> 
				<span style="color: #5e64ff;">{frappe.utils.escape_html(rendered_subject)}</span>
			</div>
			<div style="background-color: white; padding: 15px; border-radius: 4px; border: 1px solid #e3e8ee;">
				<div style="color: #6c7680; font-size: 12px; margin-bottom: 10px;">
					<strong>Template Preview:</strong> (Variables will be replaced with actual values when email is sent)
				</div>
				<div style="border-top: 1px solid #e3e8ee; padding-top: 10px;">
					{rendered_content}
				</div>
			</div>
			<div style="margin-top: 10px; padding: 8px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 3px;">
				<small style="color: #856404;">
					<strong>Note:</strong> This is a preview of the template. 
					Jinja variables (like <code>{{{{ variable }}}}</code>) will be replaced with actual values when the email is sent.
				</small>
			</div>
		</div>
		"""
		
		return {
			"preview_html": preview_html,
			"subject": rendered_subject or ""
		}
		
	except Exception as e:
		frappe.log_error(f"Error getting email template preview: {str(e)}")
		return {
			"preview_html": f"<div style='color: red;'>Error loading preview: {str(e)}</div>",
			"subject": ""
		}