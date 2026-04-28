"""
Centralised audit logger.

RULE: Every authentication event, workflow state change, AI request,
      and security event MUST pass through this module.

Design:
- log_activity()      → synchronous insert (safe for hooks, very fast)
- log_activity_async()→ frappe.enqueue wrapper for non-critical paths
- Logging failures are swallowed and written to frappe.log_error so they
  never break the request that triggered them.
"""

import json
from typing import Optional

import frappe


def log_activity(
	activity_type: str,
	user: str = None,
	tenant: str = None,
	reference_doctype: str = "",
	reference_name: str = "",
	description: str = "",
	ip_address: str = "",
	device_id: str = "",
	risk_level: str = "Low",
	status: str = "Success",
	metadata: Optional[dict] = None,
	action_queue_ref: str = None,
) -> Optional[str]:
	"""
	Insert an activity log record synchronously.
	Returns the new document name, or None on failure.
	"""
	try:
		resolved_user = user or frappe.session.user
		resolved_ip = ip_address or _get_request_ip()

		doc = frappe.get_doc(
			{
				"doctype": "KCSC AI Activity Log",
				"user": resolved_user,
				"tenant": tenant,
				"activity_type": activity_type,
				"reference_doctype": reference_doctype or "",
				"reference_name": reference_name or "",
				"description": description or "",
				"ip_address": resolved_ip,
				"device_id": device_id or "",
				"risk_level": risk_level,
				"status": status,
				"metadata": json.dumps(metadata, default=str) if metadata else "{}",
				"action_queue_ref": action_queue_ref,
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name
	except Exception as exc:
		# Logging must never crash the caller.
		frappe.log_error(f"KCSC AI activity log failed: {exc}", "KCSC AI Logger")
		return None


def log_activity_async(
	activity_type: str,
	**kwargs,
):
	"""
	Fire-and-forget version. Use for high-frequency paths where even a
	synchronous DB insert adds too much latency (e.g. API middleware).
	"""
	frappe.enqueue(
		"kcsc_ai.kcsc_ai.services.activity_logger.log_activity",
		queue="short",
		is_async=True,
		activity_type=activity_type,
		**kwargs,
	)


def _get_request_ip() -> str:
	try:
		return getattr(frappe.local, "request_ip", "") or ""
	except Exception:
		return ""
