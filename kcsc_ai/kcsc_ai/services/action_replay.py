"""
Action Replay System — Phase 5

Allows failed or historical actions to be replayed for debugging or recovery.

Replay creates a NEW Action Queue record (different name) from the original's
payload — it does NOT re-run the original record directly. This ensures:
  1. Idempotency: original + replay are separate audit trail entries
  2. Risk re-assessment: replay may have a different risk profile
  3. Full confirmation flow: even replays require approval
"""

import json

import frappe


def replay_action(original_queue_id: str, requested_by: str = None) -> dict:
	"""
	Replay a failed or completed action by cloning its payload into a new queue entry.
	The new entry goes through the full risk-assessment + confirmation flow.

	Only System Managers or the original requester may replay.
	"""
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.workflow_service import create_queued_action

	original = frappe.get_doc("KCSC AI Action Queue", original_queue_id)
	actor = requested_by or frappe.session.user

	# Permission check
	if original.user != actor and "System Manager" not in frappe.get_roles(actor):
		frappe.throw("Only the original requester or a System Manager can replay this action", frappe.PermissionError)

	# Only replay failed/executed actions
	if original.status not in ("Failed", "Executed"):
		frappe.throw(
			f"Replay is only available for Failed or Executed actions (current: {original.status})",
			frappe.ValidationError,
		)

	try:
		payload_dict = json.loads(original.payload or "{}")
	except json.JSONDecodeError:
		payload_dict = {}

	# Mark replay in payload for audit trail
	payload_dict["_replay_of"] = original_queue_id
	payload_dict["_replayed_by"] = actor

	new_queue = create_queued_action(
		user=original.user,
		device_id="",  # Device re-assessed at confirmation time
		action_type=original.action_type.lower(),
		reference_doctype=original.reference_doctype or "",
		reference_name=original.reference_name or "",
		workflow_action=original.workflow_action or "",
		payload_dict=payload_dict,
		tenant=original.tenant,
		idempotency_key="",  # New entry — no idempotency carry-over
		ip_address="",
	)

	log_activity(
		"Workflow Action", user=actor, tenant=original.tenant,
		reference_doctype=original.reference_doctype, reference_name=original.reference_name,
		description=f"Action {original_queue_id} replayed as {new_queue.name}",
		status="Warning", action_queue_ref=new_queue.name,
		metadata={"original": original_queue_id, "replay": new_queue.name},
	)

	return {
		"original_action_queue_id": original_queue_id,
		"new_action_queue_id": new_queue.name,
		"status": new_queue.status,
		"risk_level": new_queue.risk_level,
		"required_auth": new_queue.required_auth,
		"message": "Action replayed. Complete verification to execute.",
	}


def get_replay_history(reference_doctype: str, reference_name: str) -> list:
	"""
	Return the full action history (original + all replays) for a document.
	Useful for debugging workflow state transitions.
	"""
	records = frappe.db.get_all(
		"KCSC AI Action Queue",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		fields=[
			"name", "user", "action_type", "workflow_action", "status",
			"risk_level", "confirmation_method", "created_at", "executed_at",
			"error_message", "retry_count",
		],
		order_by="created_at asc",
	)

	# Annotate replays
	for rec in records:
		if rec.get("name"):
			payload_raw = frappe.db.get_value("KCSC AI Action Queue", rec["name"], "payload") or "{}"
			try:
				p = json.loads(payload_raw)
				rec["replay_of"] = p.get("_replay_of")
				rec["replayed_by"] = p.get("_replayed_by")
			except json.JSONDecodeError:
				rec["replay_of"] = None
				rec["replayed_by"] = None

	return records
