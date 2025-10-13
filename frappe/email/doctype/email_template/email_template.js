frappe.ui.form.on('Email Template', {
    refresh: function(frm) {
        // Update preview on form load
        update_preview(frm);
    },
    
    subject: function(frm) {
        // Update preview when subject changes
        update_preview(frm);
    },
    
    response: function(frm) {
        // Update preview when response changes (text editor)
        update_preview(frm);
    },
    
    response_html: function(frm) {
        // Update preview when response_html changes (code editor)
        update_preview(frm);
    },
    
    use_html: function(frm) {
        // Update preview when use_html checkbox changes
        update_preview(frm);
    }
});

/**
 * Update the HTML preview field
 * @param {Object} frm - The form object
 */
function update_preview(frm) {
    // Don't update if form is new or being loaded
    if (!frm.doc.name || frm.doc.__islocal) {
        frm.set_df_property('html_preview', 'options', '');
        return;
    }
    
    // Show loading indicator
    frm.set_df_property('html_preview', 'options', '<div style="text-align: center; padding: 20px; color: #8D99A6;">Loading preview...</div>');
    
    // Call server-side method to get preview
    frappe.call({
        method: 'frappe.email.doctype.email_template.email_template.get_preview',
        args: {
            docname: frm.doc.name
        },
        callback: function(r) {
            if (r.message && r.message.preview_html) {
                // Update the HTML preview field
                frm.set_df_property('html_preview', 'options', r.message.preview_html);
            } else {
                // Show empty state if no preview available
                frm.set_df_property('html_preview', 'options', 
                    '<div style="text-align: center; padding: 20px; color: #8D99A6;">No preview available. Add content to see preview.</div>'
                );
            }
        },
        error: function(err) {
            console.error('Error loading email template preview:', err);
            frm.set_df_property('html_preview', 'options', 
                '<div style="color: red; padding: 15px;">Error loading preview. Please try again.</div>'
            );
        }
    });
}