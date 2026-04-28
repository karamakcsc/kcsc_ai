app_name = "kcsc_ai"
app_title = "Kcsc Ai"
app_publisher = "KCSC"
app_description = "KCSC AI — Enterprise SaaS Platform Layer for ERPNext"
app_email = "msiam@kcsc.com.jo"
app_license = "mit"

# -----------------------------------------------------------------------
# Installation
# -----------------------------------------------------------------------
after_install = "kcsc_ai.install.after_install"

# -----------------------------------------------------------------------
# Document Events
#
# RULE: hooks are used ONLY for lightweight validation and audit logging.
#       Heavy workflow execution always goes through Action Queue + enqueue.
# -----------------------------------------------------------------------
doc_events = {
	# Controller methods (validate, before_insert, on_update, before_save, etc.)
	# are called automatically by Frappe from the doctype class.
	# This section is reserved for cross-doctype triggers added in later phases.
}

# -----------------------------------------------------------------------
# Scheduled Tasks
# -----------------------------------------------------------------------
scheduler_events = {
	# Every 5 minutes: clean up expired QR tokens in DB
	"cron": {
		"*/5 * * * *": [
			"kcsc_ai.kcsc_ai.tasks.rotate_dynamic_qr_tokens",
		],
	},
	# Hourly
	"hourly": [
		"kcsc_ai.kcsc_ai.tasks.cleanup_expired_tokens",
		"kcsc_ai.kcsc_ai.tasks.expire_stale_action_queue_entries",
	],
	# Daily
	"daily": [
		"kcsc_ai.kcsc_ai.tasks.cleanup_old_activity_logs",
		"kcsc_ai.kcsc_ai.tasks.reset_monthly_ai_quotas",
	],
}

# -----------------------------------------------------------------------
# Default Log Clearing (Frappe 16 built-in retention mechanism)
# -----------------------------------------------------------------------
default_log_clearing_doctypes = {
	"KCSC AI Activity Log": 90,  # days to retain
}
