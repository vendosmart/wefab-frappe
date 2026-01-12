# Copyright (c) 2015, Frappe Technologies and contributors
# License: MIT. See LICENSE

from __future__ import annotations

import json
import quopri
import re
import traceback
from contextlib import suppress
from email.parser import Parser
from email.policy import SMTP, default
from typing import TYPE_CHECKING
import html as html_module

import frappe
from frappe import _, safe_encode, task
from frappe.core.utils import html2text
from frappe.database.database import savepoint
from frappe.email.doctype.email_account.email_account import EmailAccount
from frappe.email.email_body import add_attachment, get_email, get_formatted_html
from frappe.email.queue import get_unsubcribed_url, get_unsubscribe_message
from frappe.email.smtp import SMTPServer
from frappe.model.document import Document
from frappe.query_builder import DocType, Interval
from frappe.query_builder.functions import Now
from frappe.utils import (
	add_days,
	cint,
	cstr,
	get_hook_method,
	get_string_between,
	get_url,
	now,
	nowdate,
	sbool,
	split_emails,
)
from frappe.utils.deprecations import deprecated
from frappe.utils.verified_command import get_signed_params

if TYPE_CHECKING:
	from typing import Literal


class EmailQueue(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.email.doctype.email_queue_recipient.email_queue_recipient import EmailQueueRecipient
		from frappe.types import DF

		add_unsubscribe_link: DF.Check
		attachments: DF.Code | None
		communication: DF.Link | None
		creation_time: DF.Datetime | None
		document_id: DF.Data | None
		email_account: DF.Link | None
		error: DF.Code | None
		expose_recipients: DF.Data | None
		message: DF.Code | None
		message_id: DF.SmallText | None
		priority: DF.Int
		recipients: DF.Table[EmailQueueRecipient]
		reference_doctype: DF.Link | None
		html_preview: DF.HTML | None
		reference_name: DF.Data | None
		retry: DF.Int
		send_after: DF.Datetime | None
		sender: DF.Data | None
		show_as_cc: DF.SmallText | None
		status: DF.Literal["Not Sent", "Sending", "Sent", "Partially Sent", "Error"]
		unsubscribe_method: DF.Data | None
		unsubscribe_param: DF.Data | None
	# end: auto-generated types
	DOCTYPE = "Email Queue"

	def set_recipients(self, recipients):
		self.set("recipients", [])
		for r in recipients:
			self.append("recipients", {"recipient": r.strip(), "status": "Not Sent"})

	def on_trash(self):
		self.prevent_email_queue_delete()

	def prevent_email_queue_delete(self):
		if frappe.session.user != "Administrator":
			frappe.throw(_("Only Administrator can delete Email Queue"))

	def get_duplicate(self, recipients):
		values = self.as_dict()
		del values["name"]
		duplicate = frappe.get_doc(values)
		duplicate.set_recipients(recipients)
		return duplicate

	@classmethod
	def new(cls, doc_data, ignore_permissions=False) -> EmailQueue:
		data = doc_data.copy()
		if not data.get("recipients"):
			return

		recipients = data.pop("recipients")
		doc = frappe.new_doc(cls.DOCTYPE)
		doc.update(data)
		doc.set_recipients(recipients)
		doc.insert(ignore_permissions=ignore_permissions)
		return doc

	@classmethod
	def find(cls, name) -> EmailQueue:
		return frappe.get_doc(cls.DOCTYPE, name)

	@classmethod
	def find_one_by_filters(cls, **kwargs):
		name = frappe.db.get_value(cls.DOCTYPE, kwargs)
		return cls.find(name) if name else None

	def update_db(self, commit=False, **kwargs):
		frappe.db.set_value(self.DOCTYPE, self.name, kwargs)
		if commit:
			frappe.db.commit()

	def update_status(self, status, commit=False, **kwargs):
		self.update_db(status=status, commit=commit, **kwargs)
		if self.communication:
			communication_doc = frappe.get_doc("Communication", self.communication)
			communication_doc.set_delivery_status(commit=commit)

	@property
	def cc(self):
		return (self.show_as_cc and self.show_as_cc.split(",")) or []

	@property
	def to(self):
		return [r.recipient for r in self.recipients if r.recipient not in self.cc]

	@property
	def attachments_list(self):
		return json.loads(self.attachments) if self.attachments else []

	def get_email_account(self, raise_error=False):
		if self.email_account:
			return frappe.get_cached_doc("Email Account", self.email_account)

		return EmailAccount.find_outgoing(
			match_by_email=self.sender, match_by_doctype=self.reference_doctype, _raise_error=raise_error
		)

	def is_to_be_sent(self):
		return self.status in ["Not Sent", "Partially Sent"]

	def can_send_now(self):
		if (
			frappe.are_emails_muted()
			or not self.is_to_be_sent()
			or cint(frappe.db.get_default("suspend_email_queue")) == 1
		):
			return False

		return True

	def send(self, smtp_server_instance: SMTPServer = None, force_send: bool = False):
		"""Send emails to recipients."""
		if not self.can_send_now() and not force_send:
			return

		with SendMailContext(self, smtp_server_instance) as ctx:
			ctx.fetch_smtp_server()
			message = None
			for recipient in self.recipients:
				if recipient.is_mail_sent():
					continue

				message = ctx.build_message(recipient.recipient)
				if method := get_hook_method("override_email_send"):
					method(self, self.sender, recipient.recipient, message)
				else:
					if not frappe.flags.in_test or frappe.flags.testing_email:
						ctx.smtp_server.session.sendmail(
							from_addr=self.sender,
							to_addrs=recipient.recipient,
							msg=message.decode("utf-8").encode(),
						)

				ctx.update_recipient_status_to_sent(recipient)

			if frappe.flags.in_test and not frappe.flags.testing_email:
				frappe.flags.sent_mail = message
				return

			if ctx.email_account_doc.append_emails_to_sent_folder:
				ctx.email_account_doc.append_email_to_sent_folder(message)

	@staticmethod
	def clear_old_logs(days=30):
		"""Remove low priority older than 31 days in Outbox or configured in Log Settings.
		Note: Used separate query to avoid deadlock
		"""
		days = days or 31
		email_queue = frappe.qb.DocType("Email Queue")
		email_recipient = frappe.qb.DocType("Email Queue Recipient")

		# Delete queue table
		(
			frappe.qb.from_(email_queue).delete().where(email_queue.modified < (Now() - Interval(days=days)))
		).run()

		# delete child tables, note that this has potential to leave some orphan
		# child table behind if modified time was later than parent doc (rare).
		# But it's safe since child table doesn't contain links.
		(
			frappe.qb.from_(email_recipient)
			.delete()
			.where(email_recipient.modified < (Now() - Interval(days=days)))
		).run()

	@frappe.whitelist()
	def retry_sending(self):
		if self.status == "Error":
			self.status = "Not Sent"
			self.save(ignore_permissions=True)

	@frappe.whitelist()
	def resend_email(self):
		"""Resend email to the same recipients"""
		if not self.recipients:
			frappe.throw(_("No recipients found to resend email"))
		
		# Create a new email queue with the same content
		new_queue = frappe.new_doc("Email Queue")
		
		# Copy all the fields except name and recipients
		for field in self.meta.fields:
			if field.fieldname not in ["name", "recipients"]:
				new_queue.set(field.fieldname, self.get(field.fieldname))
		
		# Set recipients with "Not Sent" status
		for recipient in self.recipients:
			new_queue.append("recipients", {
				"recipient": recipient.recipient,
				"status": "Not Sent"
			})
		
		new_queue.insert(ignore_permissions=True)
		
		# Send the email immediately
		new_queue.send(force_send=True)
		
		frappe.msgprint(_("Email resent successfully to {0} recipients").format(len(self.recipients)))

	# ========================================
	# EMAIL PREVIEW METHODS
	# ========================================

	def before_load(self):
		"""Generate email preview before loading the document"""
		self.generate_email_preview()

	def before_insert(self):
		"""Set creation time before inserting the document"""
		if not self.creation_time:
			self.creation_time = now()
		if not self.document_id:
			self.document_id = self.reference_name.split(',')[1]
			self.reference_name = self.reference_name.split(',')[0]

	def after_insert(self):
		"""Generate and save preview after document is inserted"""
		if self.message:
			self.generate_email_preview()
			if self.html_preview:
				frappe.db.set_value(
					"Email Queue",
					self.name,
					"html_preview",
					self.html_preview,
					update_modified=False
				)
	
	def on_update(self):
		"""Generate and save preview when document is updated"""
		if self.message and not getattr(self, 'html_preview', None):
			self.generate_email_preview()
			if self.html_preview:
				frappe.db.set_value(
					"Email Queue",
					self.name,
					"html_preview",
					self.html_preview,
					update_modified=False
				)

	def generate_email_preview(self):
		"""Generate HTML preview of the email - Server Side Only"""
		if not self.message:
			self.html_preview = self._get_empty_preview()
			return
		
		# Build the preview HTML
		preview_html = self._build_preview_html()
		
		# Set the preview in the html_preview field
		self.html_preview = preview_html

	def _build_preview_html(self):
		"""Build the complete email preview HTML"""
		
		# Extract HTML content from MIME message
		email_html = self._extract_html_from_message()
		
		# Get recipient list HTML
		recipients_html = self._get_recipients_html()
		
		# Get CC list if available
		cc_html = ""
		if self.show_as_cc:
			cc_html = f"""
				<div class="email-header-row">
					<span class="email-label">CC:</span>
					<span class="email-value">{frappe.utils.escape_html(self.show_as_cc)}</span>
				</div>
			"""
		
		# Get attachments info
		attachments_html = self._get_attachments_html()
		
		# Get email subject
		subject = self._get_email_subject()
		
		# Build the complete preview
		preview = f"""
		<div class="email-preview-container">
			<style>
				.email-preview-container {{
					font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
					max-width: 100%;
					margin: 0;
					background: #f8f9fa;
					border: 1px solid #dee2e6;
					border-radius: 8px;
					overflow: hidden;
				}}
				.email-preview-header {{
					background: #ffffff;
					padding: 20px;
					border-bottom: 1px solid #dee2e6;
				}}
				.email-header-row {{
					margin-bottom: 12px;
					display: flex;
					align-items: flex-start;
					font-size: 14px;
				}}
				.email-label {{
					font-weight: 600;
					color: #495057;
					min-width: 80px;
					flex-shrink: 0;
				}}
				.email-value {{
					color: #212529;
					flex: 1;
					word-break: break-word;
				}}
				.recipient-tag {{
					display: inline-block;
					background: #e9ecef;
					padding: 4px 10px;
					border-radius: 12px;
					margin-right: 6px;
					margin-bottom: 6px;
					font-size: 13px;
				}}
				.recipient-sent {{
					background: #d1e7dd;
					color: #0f5132;
				}}
				.recipient-error {{
					background: #f8d7da;
					color: #842029;
				}}
				.email-preview-body {{
					background: #ffffff;
					padding: 20px;
					min-height: 200px;
					overflow-x: auto;
					border-top: 1px solid #dee2e6;
				}}
				.email-attachments {{
					background: #f8f9fa;
					padding: 15px 20px;
					border-top: 1px solid #dee2e6;
				}}
				.attachment-item {{
					display: inline-block;
					background: #ffffff;
					border: 1px solid #dee2e6;
					border-radius: 6px;
					padding: 8px 12px;
					margin-right: 10px;
					margin-bottom: 8px;
					font-size: 13px;
				}}
				.attachment-icon {{
					margin-right: 6px;
					color: #6c757d;
				}}
				.status-badge {{
					display: inline-block;
					padding: 4px 10px;
					border-radius: 4px;
					font-size: 12px;
					font-weight: 600;
					margin-left: 10px;
				}}
				.status-sent {{
					background: #d1e7dd;
					color: #0f5132;
				}}
				.status-not-sent {{
					background: #fff3cd;
					color: #664d03;
				}}
				.status-error {{
					background: #f8d7da;
					color: #842029;
				}}
				.status-sending {{
					background: #cfe2ff;
					color: #084298;
				}}
				.preview-toolbar {{
					background: #495057;
					color: #ffffff;
					padding: 10px 20px;
					font-size: 12px;
					font-weight: 600;
					text-transform: uppercase;
					letter-spacing: 0.5px;
				}}
				.email-metadata {{
					background: #f8f9fa;
					padding: 10px 20px;
					border-top: 1px solid #dee2e6;
					font-size: 12px;
					color: #6c757d;
				}}
				.metadata-item {{
					display: inline-block;
					margin-right: 20px;
				}}
			</style>
			
			<div class="preview-toolbar">
				📧 Email Preview
			</div>
			
			<div class="email-preview-header">
				<div class="email-header-row">
					<span class="email-label">From:</span>
					<span class="email-value">{frappe.utils.escape_html(self.sender or 'N/A')}</span>
				</div>
				
				<div class="email-header-row">
					<span class="email-label">To:</span>
					<span class="email-value">{recipients_html}</span>
				</div>
				
				{cc_html}
				
				<div class="email-header-row">
					<span class="email-label">Subject:</span>
					<span class="email-value"><strong>{frappe.utils.escape_html(subject)}</strong></span>
				</div>
			</div>
			
			<div class="email-preview-body">
				{email_html}
			</div>
			
			{attachments_html}
			
		</div>
		"""
		
		return preview

	def _get_empty_preview(self):
		"""Return empty preview HTML"""
		return """
		<div style="padding: 40px; text-align: center; color: #6c757d; background: #f8f9fa; border: 1px dashed #dee2e6; border-radius: 8px;">
			<div style="font-size: 48px; margin-bottom: 16px;">📭</div>
			<div style="font-size: 16px; font-weight: 600;">No Email Content</div>
			<div style="font-size: 14px; margin-top: 8px;">The message field is empty</div>
		</div>
		"""
	
	def _extract_html_from_message(self):
		"""Extract HTML content from MIME message"""
		if not self.message:
			return "<p>No message content</p>"
		
		try:
			# Parse the MIME message
			msg = Parser(policy=default).parsestr(self.message)
			
			# Try to get HTML part
			html_content = None
			
			if msg.is_multipart():
				for part in msg.walk():
					content_type = part.get_content_type()
					if content_type == 'text/html':
						try:
							html_content = part.get_content()
							if html_content:
								break
						except Exception as e:
							# If get_content() fails, try get_payload with decode
							try:
								payload = part.get_payload(decode=True)
								if payload:
									html_content = payload.decode('utf-8', errors='replace')
									break
							except:
								continue
			else:
				if msg.get_content_type() == 'text/html':
					try:
						html_content = msg.get_content()
					except:
						payload = msg.get_payload(decode=True)
						if payload:
							html_content = payload.decode('utf-8', errors='replace')
			
			if html_content:
				return html_content
			
			# Fallback: try to get text/plain and convert to HTML
			if msg.is_multipart():
				for part in msg.walk():
					content_type = part.get_content_type()
					if content_type == 'text/plain':
						try:
							text_content = part.get_content()
							return f"<pre style='white-space: pre-wrap; font-family: inherit;'>{html_module.escape(text_content)}</pre>"
						except:
							continue
			else:
				if msg.get_content_type() == 'text/plain':
					try:
						text_content = msg.get_content()
						return f"<pre style='white-space: pre-wrap; font-family: inherit;'>{html_module.escape(text_content)}</pre>"
					except:
						pass
			
			return "<p style='color: #999;'>No HTML or text content found in email</p>"
			
		except Exception as e:
			frappe.log_error(f"Error extracting HTML from message: {str(e)}")
			# Fallback: show the raw message with some formatting
			return f"<pre style='white-space: pre-wrap; font-family: monospace; font-size: 12px;'>{frappe.utils.escape_html(self.message[:2000])}</pre>"

	def _get_recipients_html(self):
		"""Generate HTML for recipients with status indicators"""
		if not self.recipients:
			return '<em style="color: #6c757d;">No recipients</em>'
		
		recipients_tags = []
		for recipient in self.recipients:
			status_class = ""
			if recipient.status == "Sent":
				status_class = "recipient-sent"
			elif recipient.error:
				status_class = "recipient-error"
			
			recipients_tags.append(
				f'<span class="recipient-tag {status_class}">'
				f'{frappe.utils.escape_html(recipient.recipient or "")}'
				f'</span>'
			)
		
		return "".join(recipients_tags)

	def _get_attachments_html(self):
		"""Generate HTML for attachments section"""
		if not self.attachments:
			return ""
		
		try:
			attachments_list = json.loads(self.attachments) if isinstance(self.attachments, str) else self.attachments
			if not attachments_list:
				return ""
		except (json.JSONDecodeError, TypeError):
			return ""
		
		attachment_items = []
		for attachment in attachments_list:
			file_name = attachment.get('fname', 'Unknown file')
			file_size = attachment.get('fsize', '')
			
			size_display = ""
			if file_size:
				try:
					size_kb = int(file_size) / 1024
					if size_kb > 1024:
						size_display = f" ({size_kb/1024:.1f} MB)"
					else:
						size_display = f" ({size_kb:.1f} KB)"
				except:
					pass
			
			attachment_items.append(
				f'<div class="attachment-item">'
				f'<span class="attachment-icon">📎</span>'
				f'{frappe.utils.escape_html(file_name)}{size_display}'
				f'</div>'
			)
		
		if not attachment_items:
			return ""
		
		return f"""
		<div class="email-attachments">
			<div style="margin-bottom: 8px; font-weight: 600; color: #495057; font-size: 13px;">
				📎 Attachments ({len(attachment_items)})
			</div>
			{"".join(attachment_items)}
		</div>
		"""

	def _get_email_subject(self):
		"""Extract subject from message or get from communication"""
		# Try to get subject from linked communication
		if self.communication:
			try:
				comm = frappe.get_doc("Communication", self.communication)
				if comm.subject:
					return comm.subject
			except:
				pass
		
		# Try to parse subject from MIME message
		if self.message:
			try:
				msg = Parser(policy=default).parsestr(self.message)
				if msg['Subject']:
					return msg['Subject']
			except:
				pass
			
			# Look for <title> tag
			match = re.search(r'<title>(.*?)</title>', self.message, re.IGNORECASE | re.DOTALL)
			if match:
				return match.group(1).strip()
			
			# Look for first <h1> tag
			match = re.search(r'<h1[^>]*>(.*?)</h1>', self.message, re.IGNORECASE | re.DOTALL)
			if match:
				# Remove HTML tags from h1 content
				h1_text = re.sub(r'<[^>]+>', '', match.group(1))
				return h1_text.strip()
		
		# Default subject based on reference
		if self.reference_doctype and self.reference_name:
			return f"{self.reference_doctype}: {self.reference_name}"
		
		return "Email from Frappe"

@task(queue="short")
@deprecated
def send_mail(email_queue_name, smtp_server_instance: SMTPServer = None):
	"""This is equivalent to EmailQueue.send.

	This provides a way to make sending mail as a background job.
	"""
	record = EmailQueue.find(email_queue_name)
	record.send(smtp_server_instance=smtp_server_instance)


class SendMailContext:
	def __init__(
		self,
		queue_doc: Document,
		smtp_server_instance: SMTPServer = None,
	):
		self.queue_doc: EmailQueue = queue_doc
		self.smtp_server: SMTPServer = smtp_server_instance
		self.sent_to_atleast_one_recipient = any(
			rec.recipient for rec in self.queue_doc.recipients if rec.is_mail_sent()
		)
		self.email_account_doc = None

	def fetch_smtp_server(self):
		self.email_account_doc = self.queue_doc.get_email_account(raise_error=True)
		if not self.smtp_server:
			self.smtp_server = self.email_account_doc.get_smtp_server()

	def __enter__(self):
		self.queue_doc.update_status(status="Sending", commit=True)
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		if exc_type:
			update_fields = {"error": frappe.get_traceback()}
			if self.queue_doc.retry < get_email_retry_limit():
				update_fields.update(
					{
						"status": "Partially Sent" if self.sent_to_atleast_one_recipient else "Not Sent",
						"retry": self.queue_doc.retry + 1,
					}
				)
			else:
				update_fields.update({"status": "Error"})
				self.notify_failed_email()
		else:
			update_fields = {"status": "Sent"}

		self.queue_doc.update_status(**update_fields, commit=True)

	@savepoint(catch=Exception)
	def notify_failed_email(self):
		# Parse the email body to extract the subject
		subject = Parser(policy=SMTP).parsestr(self.queue_doc.message)["Subject"]

		# Construct the notification
		notification = frappe.new_doc("Notification Log")
		notification.for_user = self.queue_doc.owner
		notification.set("type", "Alert")
		notification.from_user = self.queue_doc.owner
		notification.document_type = self.queue_doc.doctype
		notification.document_name = self.queue_doc.name
		notification.subject = _("Failed to send email with subject:") + f" {subject}"
		notification.insert()

	def update_recipient_status_to_sent(self, recipient):
		self.sent_to_atleast_one_recipient = True
		recipient.update_db(status="Sent", commit=True)

	def get_message_object(self, message):
		return Parser(policy=SMTP).parsestr(message)

	def message_placeholder(self, placeholder_key):
		# sourcery skip: avoid-builtin-shadow
		map = {
			"tracker": "<!--email_open_check-->",
			"unsubscribe_url": "<!--unsubscribe_url-->",
			"cc": "<!--cc_message-->",
			"recipient": "<!--recipient-->",
		}
		return map.get(placeholder_key)

	def build_message(self, recipient_email) -> bytes:
		"""Build message specific to the recipient."""
		message = self.queue_doc.message

		if not message:
			return ""

		message = message.replace(self.message_placeholder("tracker"), self.get_tracker_str(recipient_email))
		message = message.replace(self.message_placeholder("unsubscribe_url"), self.get_unsubscribe_str(recipient_email))
		message = message.replace(self.message_placeholder("cc"), self.get_receivers_str())
		message = message.replace(
			self.message_placeholder("recipient"), self.get_recipient_str(recipient_email)
		)
		# Convert timezone markers for this recipient
		message = self.convert_timezone_markers(message, recipient_email)
		message = self.include_attachments(message)
		return message

	def get_tracker_str(self, recipient_email) -> str:
		tracker_url = ""
		if self.queue_doc.get("email_read_tracker_url"):
			email_read_tracker_url = self.queue_doc.email_read_tracker_url
			params = {
				"recipient_email": recipient_email,
				"reference_name": self.queue_doc.reference_name,
				"reference_doctype": self.queue_doc.reference_doctype,
			}
			tracker_url = get_url(f"{email_read_tracker_url}?{get_signed_params(params)}")

		elif (
			self.email_account_doc
			and self.email_account_doc.track_email_status
			and self.queue_doc.communication
		):
			tracker_url = f"{get_url()}/api/method/frappe.core.doctype.communication.email.mark_email_as_seen?name={self.queue_doc.communication}"

		if tracker_url:
			tracker_url_html = f'<img src="{tracker_url}"/>'
			return quopri.encodestring(tracker_url_html.encode()).decode()

		return ""

	def get_unsubscribe_str(self, recipient_email: str) -> str:
		unsubscribe_url = ""

		if self.queue_doc.add_unsubscribe_link and self.queue_doc.reference_doctype:
			unsubscribe_url = get_unsubcribed_url(
				reference_doctype=self.queue_doc.reference_doctype,
				reference_name=self.queue_doc.reference_name,
				email=recipient_email,
				unsubscribe_method=self.queue_doc.unsubscribe_method,
				unsubscribe_params=self.queue_doc.unsubscribe_param,
			)

		return quopri.encodestring(unsubscribe_url.encode()).decode()

	def get_receivers_str(self):
		message = ""
		if self.queue_doc.expose_recipients == "footer":
			to_str = ", ".join(self.queue_doc.to)
			cc_str = ", ".join(self.queue_doc.cc)
			message = f"This email was sent to {to_str}"
			message = f"{message} and copied to {cc_str}" if cc_str else message
		return message

	def get_recipient_str(self, recipient_email):
		return recipient_email if self.queue_doc.expose_recipients != "header" else ""

	def convert_timezone_markers(self, message: str, recipient_email: str) -> str:
		"""
		Convert all data-utc timezone markers to recipient's timezone.

		Handles MIME-encoded messages by decoding, converting, then re-encoding.

		Markers format: <span data-utc="2025-01-15T10:30:00Z">15 Jan 2025 04:00 PM (UTC+05:30)</span>
		Output format: 15 Jan 2025 11:30 AM (UTC-05:00)
		"""
		import re
		import pytz
		from datetime import datetime
		from email.parser import Parser
		from email.policy import SMTP

		# Get recipient timezone
		recipient_tz = self.get_recipient_timezone(recipient_email)

		try:
			target_timezone = pytz.timezone(recipient_tz)
		except Exception:
			return message  # Invalid timezone, return unchanged

		# Parse the MIME message
		try:
			msg = Parser(policy=SMTP).parsestr(message)
		except Exception:
			return message  # Can't parse, return unchanged

		def convert_html_content(html_content: str) -> str:
			"""Apply timezone conversion to decoded HTML content."""
			# Pattern: <span data-utc="2025-01-15T10:30:00Z">any text</span>
			datetime_pattern = r'<span data-utc="([^"]+)">([^<]*)</span>'

			def replace_datetime(match):
				utc_str = match.group(1)
				try:
					# Parse UTC datetime
					utc_dt = datetime.strptime(utc_str, '%Y-%m-%dT%H:%M:%SZ')
					utc_dt = pytz.UTC.localize(utc_dt)

					# Convert to target timezone
					local_dt = utc_dt.astimezone(target_timezone)

					# Format: "15 Jan 2025 04:00 PM (UTC+05:30)"
					offset = local_dt.strftime('%z')
					offset_formatted = f"UTC{offset[:3]}:{offset[3:]}"
					formatted_time = local_dt.strftime('%d %b %Y %I:%M %p')

					return f"{formatted_time} ({offset_formatted})"
				except Exception:
					return match.group(2)  # Return original text on error

			# Convert datetime markers
			result = re.sub(datetime_pattern, replace_datetime, html_content)

			# Strip date-only markers (no timezone conversion needed)
			date_pattern = r'<span data-date="[^"]+">([^<]*)</span>'
			result = re.sub(date_pattern, r'\1', result)

			return result

		def process_part(part):
			"""Process a single MIME part."""
			content_type = part.get_content_type()

			if content_type in ('text/html', 'text/plain'):
				try:
					# Get the payload (decoded)
					charset = part.get_content_charset() or 'utf-8'
					payload = part.get_payload(decode=True)

					if payload:
						decoded_content = payload.decode(charset, errors='replace')

						# Apply timezone conversion
						converted_content = convert_html_content(decoded_content)

						# Set the new payload
						part.set_payload(converted_content, charset=charset)
				except Exception:
					pass  # Keep original on error

		# Process the message
		if msg.is_multipart():
			for part in msg.walk():
				process_part(part)
		else:
			process_part(msg)

		# Return the modified message as string
		return msg.as_string()

	def get_recipient_timezone(self, recipient_email: str) -> str:
		"""
		Get timezone for a recipient by email.
		Falls back to system timezone if user not found or no timezone set.
		"""
		from frappe.utils import get_system_timezone

		try:
			user_tz = frappe.db.get_value("User", recipient_email, "time_zone")
			if user_tz:
				return user_tz
		except Exception:
			pass

		return get_system_timezone()

	def include_attachments(self, message):
		message_obj = self.get_message_object(message)
		attachments = self.queue_doc.attachments_list

		for attachment in attachments:
			if attachment.get("fcontent"):
				continue

			file_filters = {}
			if attachment.get("fid"):
				file_filters["name"] = attachment.get("fid")
			elif attachment.get("file_url"):
				file_filters["file_url"] = attachment.get("file_url")

			if file_filters:
				_file = frappe.get_doc("File", file_filters)
				fcontent = _file.get_content()
				attachment.update({"fname": _file.file_name, "fcontent": fcontent, "parent": message_obj})
				attachment.pop("fid", None)
				attachment.pop("file_url", None)
				add_attachment(**attachment)

			elif attachment.get("print_format_attachment") == 1:
				attachment.pop("print_format_attachment", None)
				print_format_file = frappe.attach_print(**attachment)
				self._store_file(print_format_file["fname"], print_format_file["fcontent"])
				print_format_file.update({"parent": message_obj})
				add_attachment(**print_format_file)

		return safe_encode(message_obj.as_string())

	def _store_file(self, file_name, content):
		if not frappe.get_system_settings("store_attached_pdf_document"):
			return

		file_data = frappe._dict(file_name=file_name, is_private=1)

		# Store on communication if available, else email queue doc
		if self.queue_doc.communication:
			file_data.attached_to_doctype = "Communication"
			file_data.attached_to_name = self.queue_doc.communication
		else:
			file_data.attached_to_doctype = self.queue_doc.doctype
			file_data.attached_to_name = self.queue_doc.name

		if frappe.db.exists("File", file_data):
			return

		file = frappe.new_doc("File", **file_data)
		file.content = content
		file.insert()


@frappe.whitelist()
def bulk_retry(queues):
	frappe.only_for("System Manager")

	if isinstance(queues, str):
		queues = json.loads(queues)

	if not queues:
		return

	frappe.msgprint(
		_("Updating Email Queue Statuses. The emails will be picked up in the next scheduled run."),
		_("Processing..."),
	)

	email_queue = frappe.qb.DocType("Email Queue")
	frappe.qb.update(email_queue).set(email_queue.status, "Not Sent").set(email_queue.modified, now()).set(
		email_queue.modified_by, frappe.session.user
	).where(email_queue.name.isin(queues) & email_queue.status == "Error").run()


@frappe.whitelist()
def send_now(name, force_send: bool = False):
	record = EmailQueue.find(name)
	if record:
		record.check_permission()
		record.send(force_send=force_send)


@frappe.whitelist()
def toggle_sending(enable):
	frappe.only_for("System Manager")
	frappe.db.set_default("suspend_email_queue", 0 if sbool(enable) else 1)


@frappe.whitelist()
def get_email_preview(name):
	"""
	Whitelisted method to regenerate and get email preview
	
	Args:
		name: Email Queue document name
	
	Returns:
		dict: Preview HTML and metadata
	"""
	try:
		doc = frappe.get_doc("Email Queue", name)
		doc.generate_email_preview()
		doc.save(ignore_permissions=True)
		
		return {
			"success": True,
			"preview_html": doc.html_preview,
			"status": doc.status,
			"sender": doc.sender,
			"recipient_count": len(doc.recipients) if doc.recipients else 0
		}
	except Exception as e:
		frappe.log_error(f"Error generating email preview: {str(e)}")
		return {
			"success": False,
			"error": str(e)
		}


@frappe.whitelist()
def get_rendered_preview(name):
	"""Get the rendered HTML preview for Email Queue"""
	try:
		doc = frappe.get_doc("Email Queue", name)
		
		# Generate preview if not exists
		if not hasattr(doc, 'html_preview') or not doc.html_preview:
			doc.generate_email_preview()
			if doc.html_preview:
				frappe.db.set_value(
					"Email Queue",
					name,
					"html_preview",
					doc.html_preview,
					update_modified=False
				)
				frappe.db.commit()
		
		return {
			"success": True,
			"html": doc.html_preview or "<p>No preview available</p>"
		}
	except Exception as e:
		frappe.log_error(f"Error getting preview: {str(e)}")
		return {
			"success": False,
			"error": str(e)
		}


@frappe.whitelist()
def regenerate_all_previews(limit=100):
	"""
	Regenerate previews for all Email Queue documents
	
	Args:
		limit: Maximum number of documents to process
	
	Returns:
		dict: Processing statistics
	"""
	frappe.only_for("System Manager")
	
	email_queues = frappe.get_all("Email Queue", limit=limit, pluck="name")
	
	stats = {
		"total": len(email_queues),
		"success": 0,
		"failed": 0
	}
	
	for name in email_queues:
		try:
			doc = frappe.get_doc("Email Queue", name)
			doc.generate_email_preview()
			doc.save(ignore_permissions=True)
			stats["success"] += 1
		except Exception as e:
			stats["failed"] += 1
			frappe.log_error(f"Failed to generate preview for {name}: {str(e)}")
	
	return stats


def on_doctype_update():
	"""Add index in `tabCommunication` for `(reference_doctype, reference_name)`"""
	frappe.db.add_index("Email Queue", ("status", "send_after", "priority", "creation"), "index_bulk_flush")

	frappe.db.add_index("Email Queue", ["message_id(140)"])


def get_email_retry_limit():
	return cint(frappe.db.get_system_setting("email_retry_limit")) or 3


class QueueBuilder:
	"""Builds Email Queue from the given data"""

	def __init__(
		self,
		recipients=None,
		sender=None,
		subject=None,
		message=None,
		text_content=None,
		reference_doctype=None,
		reference_name=None,
		unsubscribe_method=None,
		unsubscribe_params=None,
		unsubscribe_message=None,
		attachments=None,
		reply_to=None,
		cc=None,
		bcc=None,
		message_id=None,
		in_reply_to=None,
		send_after=None,
		expose_recipients=None,
		send_priority=1,
		communication=None,
		read_receipt=None,
		queue_separately=False,
		is_notification=False,
		add_unsubscribe_link=1,
		inline_images=None,
		header=None,
		print_letterhead=False,
		with_container=False,
		email_read_tracker_url=None,
		x_priority: Literal[1, 3, 5] = 3,
	):
		"""Add email to sending queue (Email Queue)

		:param recipients: List of recipients.
		:param sender: Email sender.
		:param subject: Email subject.
		:param message: Email message.
		:param text_content: Text version of email message.
		:param reference_doctype: Reference DocType of caller document.
		:param reference_name: Reference name of caller document.
		:param send_priority: Priority for Email Queue, default 1.
		:param unsubscribe_method: URL method for unsubscribe. Default is `/api/method/frappe.email.queue.unsubscribe`.
		:param unsubscribe_params: additional params for unsubscribed links. default are name, doctype, email
		:param attachments: Attachments to be sent.
		:param reply_to: Reply to be captured here (default inbox)
		:param in_reply_to: Used to send the Message-Id of a received email back as In-Reply-To.
		:param send_after: Send this email after the given datetime. If value is in integer, then `send_after` will be the automatically set to no of days from current date.
		:param communication: Communication link to be set in Email Queue record
		:param queue_separately: Queue each email separately
		:param is_notification: Marks email as notification so will not trigger notifications from system
		:param add_unsubscribe_link: Send unsubscribe link in the footer of the Email, default 1.
		:param inline_images: List of inline images as {"filename", "filecontent"}. All src properties will be replaced with random Content-Id
		:param header: Append header in email (boolean)
		:param with_container: Wraps email inside styled container
		:param email_read_tracker_url: A URL for tracking whether an email is read by the recipient.
		:param x_priority: 1 = HIGHEST, 3 = NORMAL, 5 = LOWEST
		"""

		self._unsubscribe_method = unsubscribe_method
		self._recipients = recipients
		self._cc = cc
		self._bcc = bcc
		self._send_after = send_after
		self._sender = sender
		self._text_content = text_content
		self._message = message
		self._x_priority: Literal[1, 3, 5] = x_priority
		self._add_unsubscribe_link = add_unsubscribe_link
		self._unsubscribe_message = unsubscribe_message
		self._attachments = attachments

		self._unsubscribed_user_emails = None
		self._email_account = None

		self.unsubscribe_params = unsubscribe_params
		self.subject = subject
		self.reference_doctype = reference_doctype
		self.reference_name = reference_name
		self.expose_recipients = expose_recipients
		self.with_container = with_container
		self.header = header
		self.reply_to = reply_to
		self.message_id = message_id
		self.in_reply_to = in_reply_to
		self.send_priority = send_priority
		self.communication = communication
		self.read_receipt = read_receipt
		self.queue_separately = queue_separately
		self.is_notification = is_notification
		self.inline_images = inline_images
		self.print_letterhead = print_letterhead
		self.email_read_tracker_url = email_read_tracker_url

	@property
	def unsubscribe_method(self):
		return self._unsubscribe_method or "/api/method/frappe.email.queue.unsubscribe"

	def _get_emails_list(self, emails=None):
		emails = split_emails(emails) if isinstance(emails, str) else (emails or [])
		return [each for each in set(emails) if each]

	@property
	def recipients(self):
		return self._get_emails_list(self._recipients)

	@property
	def cc(self):
		return self._get_emails_list(self._cc)

	@property
	def bcc(self):
		return self._get_emails_list(self._bcc)

	@property
	def send_after(self):
		if isinstance(self._send_after, int):
			return add_days(nowdate(), self._send_after)
		return self._send_after

	@property
	def sender(self):
		if not self._sender or self._sender == "Administrator":
			email_account = self.get_outgoing_email_account()
			return email_account.default_sender
		return self._sender

	def email_text_content(self):
		unsubscribe_msg = self.unsubscribe_message()
		unsubscribe_text_message = (unsubscribe_msg and unsubscribe_msg.text) or ""

		if self._text_content:
			return self._text_content + unsubscribe_text_message

		try:
			text_content = html2text(self._message)
		except Exception:
			text_content = "See html attachment"
		return text_content + unsubscribe_text_message

	def email_html_content(self):
		email_account = self.get_outgoing_email_account()
		return get_formatted_html(
			self.subject,
			self._message,
			header=self.header,
			email_account=email_account,
			unsubscribe_link=self.unsubscribe_message(),
			with_container=self.with_container,
		)

	def should_include_unsubscribe_link(self):
		return (
			self._add_unsubscribe_link == 1
			and self.reference_doctype
			and (self._unsubscribe_message or self.reference_doctype == "Newsletter")
		)

	def unsubscribe_message(self):
		if self.should_include_unsubscribe_link():
			return get_unsubscribe_message(self._unsubscribe_message, self.expose_recipients)

	def get_outgoing_email_account(self):
		if self._email_account:
			return self._email_account

		self._email_account = EmailAccount.find_outgoing(
			match_by_doctype=self.reference_doctype, match_by_email=self._sender, _raise_error=True
		)
		return self._email_account

	def get_unsubscribed_user_emails(self):
		if self._unsubscribed_user_emails is not None:
			return self._unsubscribed_user_emails

		all_ids = list(set(self.recipients + self.cc))

		EmailUnsubscribe = DocType("Email Unsubscribe")

		if len(all_ids) > 0:
			unsubscribed = (
				frappe.qb.from_(EmailUnsubscribe)
				.select(EmailUnsubscribe.email)
				.where(
					EmailUnsubscribe.email.isin(all_ids)
					& (
						(
							(EmailUnsubscribe.reference_doctype == self.reference_doctype)
							& (EmailUnsubscribe.reference_name == self.reference_name)
						)
						| (EmailUnsubscribe.global_unsubscribe == 1)
					)
				)
				.distinct()
			).run(pluck=True)
		else:
			unsubscribed = None

		self._unsubscribed_user_emails = unsubscribed or []
		return self._unsubscribed_user_emails

	def final_recipients(self):
		unsubscribed_emails = self.get_unsubscribed_user_emails()
		return [mail_id for mail_id in self.recipients if mail_id not in unsubscribed_emails]

	def final_cc(self):
		unsubscribed_emails = self.get_unsubscribed_user_emails()
		return [mail_id for mail_id in self.cc if mail_id not in unsubscribed_emails]

	def get_attachments(self):
		attachments = []
		if self._attachments:
			# store attachments with fid or print format details, to be attached on-demand later
			for att in self._attachments:
				if att.get("fid") or att.get("file_url"):
					attachments.append(att)
				elif att.get("print_format_attachment") == 1:
					if not att.get("lang", None):
						att["lang"] = frappe.local.lang
					att["print_letterhead"] = self.print_letterhead
					attachments.append(att)
		return attachments

	def prepare_email_content(self):
		email_account = self.get_outgoing_email_account()
		if email_account.always_bcc:
			self._bcc = [*self.bcc, email_account.always_bcc]
		mail = get_email(
			recipients=self.final_recipients(),
			sender=self.sender,
			subject=self.subject,
			formatted=self.email_html_content(),
			text_content=self.email_text_content(),
			attachments=self._attachments,
			reply_to=self.reply_to,
			cc=self.final_cc(),
			bcc=self.bcc,
			email_account=email_account,
			expose_recipients=self.expose_recipients,
			inline_images=self.inline_images,
			header=self.header,
			x_priority=self._x_priority,
		)

		mail.set_message_id(self.message_id, self.is_notification)
		if self.read_receipt:
			mail.msg_root["Disposition-Notification-To"] = self.sender
		if self.in_reply_to:
			mail.set_in_reply_to(self.in_reply_to)
		return mail

	def process(self, send_now=False) -> EmailQueue | None:
		"""Build and return the email queues those are created.

		Sends email incase if it is requested to send now.
		"""
		final_recipients = self.final_recipients()
		queue_separately = (final_recipients and self.queue_separately) or len(final_recipients) > 100
		if not (final_recipients + self.final_cc()):
			return []

		queue_data = self.as_dict(include_recipients=False)
		if not queue_data:
			return []

		if not queue_separately:
			recipients = list(set(final_recipients + self.final_cc() + self.bcc))
			q = EmailQueue.new({**queue_data, **{"recipients": recipients}}, ignore_permissions=True)
			send_now and q.send()
			return q
		else:
			if send_now and len(final_recipients) >= 1000:
				# force queueing if there are too many recipients to avoid timeouts
				send_now = False
			for recipients in frappe.utils.create_batch(final_recipients, 1000):
				frappe.enqueue(
					self.send_emails,
					queue_data=queue_data,
					final_recipients=recipients,
					job_name=frappe.utils.get_job_name(
						"send_bulk_emails_for", self.reference_doctype, self.reference_name
					),
					now=frappe.flags.in_test or send_now,
					queue="long",
				)

	def send_emails(self, queue_data, final_recipients):
		# This is used to bulk send emails from same sender to multiple recipients separately
		# This re-uses smtp server instance to minimize the cost of new session creation
		smtp_server_instance = None
		for r in final_recipients:
			recipients = list(set([r, *self.final_cc(), *self.bcc]))
			q = EmailQueue.new({**queue_data, **{"recipients": recipients}}, ignore_permissions=True)
			if not smtp_server_instance:
				email_account = q.get_email_account(raise_error=True)
				smtp_server_instance = email_account.get_smtp_server()

			with suppress(Exception):
				q.send(smtp_server_instance=smtp_server_instance)

		smtp_server_instance.quit()

	def as_dict(self, include_recipients=True):
		email_account = self.get_outgoing_email_account()
		email_account_name = email_account and email_account.is_exists_in_db() and email_account.name

		mail = self.prepare_email_content()
		try:
			mail_to_string = cstr(mail.as_string())
		except frappe.InvalidEmailAddressError:
			# bad Email Address - don't add to queue
			frappe.log_error(
				title="Invalid email address",
				message="Invalid email address Sender: {}, Recipients: {}, \nTraceback: {} ".format(
					self.sender, ", ".join(self.final_recipients()), traceback.format_exc()
				),
				reference_doctype=self.reference_doctype,
				reference_name=self.reference_name,
			)
			return

		d = {
			"priority": self.send_priority,
			"attachments": json.dumps(self.get_attachments()),
			"message_id": get_string_between("<", mail.msg_root["Message-Id"], ">"),
			"message": mail_to_string,
			"sender": mail.sender,
			"reference_doctype": self.reference_doctype,
			"reference_name": self.reference_name,
			"add_unsubscribe_link": self._add_unsubscribe_link,
			"unsubscribe_method": self.unsubscribe_method,
			"unsubscribe_params": self.unsubscribe_params,
			"expose_recipients": self.expose_recipients,
			"communication": self.communication,
			"send_after": self.send_after,
			"show_as_cc": ",".join(self.final_cc()),
			"show_as_bcc": ",".join(self.bcc),
			"email_account": email_account_name or None,
			"email_read_tracker_url": self.email_read_tracker_url,
		}

		if include_recipients:
			d["recipients"] = self.final_recipients()

		return d
