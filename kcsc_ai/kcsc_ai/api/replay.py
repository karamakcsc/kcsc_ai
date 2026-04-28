"""
Action Replay API — Phase 5

Endpoints:
  POST  replay_action   → re-queue a failed/executed action for retry
  GET   replay_history  → full action + replay timeline for a document
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def replay_action(action_queue_id: str) -> dict:
	"""Replay a failed or completed action."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.action_replay import replay_action as _replay

	user, _ = require_token()
	return _replay(action_queue_id, requested_by=user)


@frappe.whitelist(allow_guest=True)
def replay_history(reference_doctype: str, reference_name: str) -> list:
	"""Full workflow + replay timeline for a document."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.action_replay import get_replay_history

	user, _ = require_token()

	# Verify the user has read permission on the reference document
	if not frappe.has_permission(reference_doctype, "read", doc=reference_name, user=user):
		frappe.throw(_("No read permission on this document"), frappe.PermissionError)

	return get_replay_history(reference_doctype, reference_name)
