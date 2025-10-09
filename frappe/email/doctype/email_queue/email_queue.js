frappe.ui.form.on("Email Queue", {
	refresh: function (frm) {
		if (["Not Sent", "Partially Sent"].includes(frm.doc.status)) {
			let button = frm.add_custom_button("Send Now", function () {
				frappe.call({
					method: "frappe.email.doctype.email_queue.email_queue.send_now",
					args: {
						name: frm.doc.name,
						force_send: true,
					},
					btn: button,
					callback: function () {
						frm.reload_doc();
						if (cint(frappe.sys_defaults.suspend_email_queue)) {
							frappe.show_alert(
								__(
									"Email queue is currently suspended. Resume to automatically send other emails."
								)
							);
						}
					},
				});
			});
		} else if (frm.doc.status == "Error") {
			frm.add_custom_button("Retry Sending", function () {
				frm.call({
					method: "retry_sending",
					doc: frm.doc,
					args: {
						name: frm.doc.name,
					},
					callback: function () {
						frm.reload_doc();
					},
				});
			});
		} else if (frm.doc.status == "Sent") {
			frm.add_custom_button("Resend Email", function () {
				frm.call({
					method: "resend_email",
					doc: frm.doc,
					args: {
						name: frm.doc.name,
					},
					callback: function () {
						frm.reload_doc();
					},
				});
			});
		}
	},
});
frappe.ui.form.on('Email Queue', {
	refresh: function(frm) {
		render_email_preview(frm);
	},
	
	onload: function(frm) {
		render_email_preview(frm);
	}
});

function render_email_preview(frm) {
	if (!frm.doc.name || frm.is_new()) {
		return;
	}
	
	frm.$wrapper.find('.email-preview-section').remove();
	
	let $message_wrapper = frm.fields_dict.message.$wrapper;
	
	let $preview_section = $(`
		<div class="email-preview-section" style="margin-top: 30px; margin-bottom: 20px;">
			<div class="section-head">
				<div class="section-head-label">
					<h6 class="uppercase">📧 Email Preview</h6>
				</div>
			</div>
			<div class="email-preview-content" style="
				border: 1px solid #d1d8dd;
				border-radius: 6px;
				padding: 15px;
				background: #f8f9fa;
				min-height: 200px;
			">
				<div style="text-align: center; padding: 40px; color: #888;">
					<i class="fa fa-spinner fa-spin fa-2x"></i>
					<p style="margin-top: 15px;">Loading preview...</p>
				</div>
			</div>
		</div>
	`).insertAfter($message_wrapper);
	
	// Fetch and render the preview
	frappe.call({
		method: 'frappe.email.doctype.email_queue.email_queue.get_rendered_preview',
		args: {
			name: frm.doc.name
		},
		callback: function(r) {
			if (r.message && r.message.success) {
				$preview_section.find('.email-preview-content').html(r.message.html);
			} else {
				$preview_section.find('.email-preview-content').html(`
					<div style="padding: 20px; text-align: center; color: #999;">
						<p>❌ Failed to load preview</p>
						<p style="font-size: 12px;">${r.message ? r.message.error : 'Unknown error'}</p>
					</div>
				`);
			}
		},
		error: function(err) {
			$preview_section.find('.email-preview-content').html(`
				<div style="padding: 20px; text-align: center; color: #999;">
					<p>❌ Error loading preview</p>
				</div>
			`);
		}
	});
}