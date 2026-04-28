"""
Scheduled and background tasks.

All entries registered in hooks.py → scheduler_events.
Each function is a thin dispatcher — real logic lives in services/.
"""

import frappe


# ------------------------------------------------------------------
# Every 5 minutes
# ------------------------------------------------------------------

def rotate_dynamic_qr_tokens():
	"""Mark expired but unconsumed QR tokens as revoked in DB (Redis already evicted them)."""
	frappe.db.set_value(
		"KCSC AI Token",
		{"token_type": "QR Token", "expires_at": ("<", frappe.utils.now_datetime()), "revoked": 0},
		"revoked",
		1,
	)
	frappe.db.commit()


# ------------------------------------------------------------------
# Hourly
# ------------------------------------------------------------------

def cleanup_expired_tokens():
	"""Purge revoked + expired tokens from DB."""
	from kcsc_ai.kcsc_ai.services.token_service import cleanup_expired_tokens as _cleanup
	_cleanup()


def expire_stale_action_queue_entries():
	"""
	Mark Pending / Awaiting Confirmation queue entries older than 24 hours as Failed.
	Prevents stale confirmations from being approved long after they should have expired.
	"""
	cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-24)
	stale = frappe.db.get_all(
		"KCSC AI Action Queue",
		filters={
			"status": ("in", ["Pending", "Awaiting Confirmation"]),
			"created_at": ("<", cutoff),
		},
		pluck="name",
	)
	for name in stale:
		frappe.db.set_value(
			"KCSC AI Action Queue", name,
			{"status": "Failed", "error_message": "Expired: no confirmation received within 24 hours"},
		)
	if stale:
		frappe.db.commit()


# ------------------------------------------------------------------
# Daily
# ------------------------------------------------------------------

def cleanup_old_activity_logs():
	"""Remove activity logs beyond the retention window (default 90 days)."""
	retention_days = frappe.conf.get("kcsc_ai_log_retention_days", 90)
	cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-retention_days)
	frappe.db.delete("KCSC AI Activity Log", {"created_at": ("<", cutoff)})
	frappe.db.commit()


def reset_monthly_ai_quotas():
	"""
	Reset per-tenant AI request counters in Redis at the start of each day.
	The counter naturally expires after 30 days (TTL set in tenant_policy.py),
	but this job proactively resets it when the window rolls over to avoid
	off-by-one timing edge cases.
	"""
	from kcsc_ai.kcsc_ai.utils.redis_helper import delete_key

	tenants = frappe.db.get_all("KCSC AI Tenant", filters={"status": "Active"}, pluck="name")
	for tenant in tenants:
		# The counter rolls naturally, but an explicit delete on the 1st of the month is clean
		if frappe.utils.now_datetime().day == 1:
			delete_key(f"kcsc_ai_quota:{tenant}")


# ------------------------------------------------------------------
# Background job entry points (called via frappe.enqueue)
# ------------------------------------------------------------------

def execute_approved_action(queue_name: str):
	"""Background job: execute an approved workflow/API action from the queue."""
	from kcsc_ai.kcsc_ai.services.workflow_service import execute_approved_action as _execute
	_execute(queue_name)
