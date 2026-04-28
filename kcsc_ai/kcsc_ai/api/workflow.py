"""
Workflow Action API — Phase 2

CRITICAL RULE: No workflow action is ever executed directly from an API call.
Every request flows through the Action Queue state machine.

Endpoints:
  POST  create_action     → submit a workflow action request → returns queue ID
  POST  confirm_action    → confirm a pending action with QR/OTP/device proof
  GET   get_pending       → list actions awaiting confirmation for current user
  POST  bulk_approve      → confirm multiple actions in one call
  POST  reject_action     → reject a queued action
  GET   get_action_status → poll the status of a specific action
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def create_action(
	action_type: str,
	reference_doctype: str = "",
	reference_name: str = "",
	workflow_action: str = "",
	payload: str = "{}",
	idempotency_key: str = "",
) -> dict:
	"""
	Submit a workflow/API action request.
	Returns the Action Queue record with risk assessment and required auth method.
	"""
	import json

	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip, get_request_tenant, require_token
	from kcsc_ai.kcsc_ai.services.workflow_service import create_queued_action

	user, device_id = require_token()
	ip = _get_client_ip()
	tenant = get_request_tenant(user)

	# Parse and validate payload
	try:
		payload_dict = json.loads(payload) if isinstance(payload, str) else payload
	except (ValueError, TypeError):
		frappe.throw(_("payload must be valid JSON"), frappe.ValidationError)

	queue = create_queued_action(
		user=user,
		device_id=device_id,
		action_type=action_type,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
		workflow_action=workflow_action,
		payload_dict=payload_dict,
		tenant=tenant,
		idempotency_key=idempotency_key,
		ip_address=ip,
	)

	return {
		"action_queue_id": queue.name,
		"status": queue.status,
		"risk_level": queue.risk_level,
		"required_auth": queue.required_auth,
		"message": f"Action queued. Complete {queue.required_auth} verification to proceed.",
	}


@frappe.whitelist(allow_guest=True)
def confirm_action(
	action_queue_id: str,
	confirmation_token: str,
	confirmation_method: str,
) -> dict:
	"""
	Confirm a pending action.

	confirmation_method: "QR" | "OTP" | "Device"
	confirmation_token:
	  - QR:     raw QR token from generate_action_qr
	  - OTP:    6-digit code sent via the OTP service
	  - Device: device_id (trusted device proof)
	"""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.workflow_service import confirm_and_execute

	user, device_id = require_token()

	result = confirm_and_execute(
		user=user,
		device_id=device_id,
		queue_name=action_queue_id,
		confirmation_token=confirmation_token,
		confirmation_method=confirmation_method,
	)
	return result


@frappe.whitelist(allow_guest=True)
def get_pending() -> list:
	"""Return all actions awaiting the current user's confirmation."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token

	user, _ = require_token()
	return frappe.db.get_all(
		"KCSC AI Action Queue",
		filters={"user": user, "status": ("in", ["Pending", "Awaiting Confirmation"])},
		fields=[
			"name", "action_type", "reference_doctype", "reference_name",
			"workflow_action", "status", "risk_level", "required_auth", "created_at",
		],
		order_by="created_at desc",
		limit=100,
	)


@frappe.whitelist(allow_guest=True)
def reject_action(action_queue_id: str, reason: str = "") -> dict:
	"""Reject a pending action."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity

	user, _ = require_token()
	queue = frappe.get_doc("KCSC AI Action Queue", action_queue_id)

	if queue.user != user and "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Not authorised to reject this action"), frappe.PermissionError)

	if queue.status not in ("Pending", "Awaiting Confirmation"):
		frappe.throw(_(f"Cannot reject an action in status '{queue.status}'"))

	queue.reject(confirmed_by=user, reason=reason)
	log_activity(
		"Workflow Action", user=user, reference_doctype=queue.reference_doctype,
		reference_name=queue.reference_name, status="Warning",
		description=f"Action {action_queue_id} rejected. Reason: {reason}",
		action_queue_ref=action_queue_id,
	)
	return {"message": "Action rejected", "status": "Rejected"}


@frappe.whitelist(allow_guest=True)
def bulk_approve(
	action_queue_ids: list,
	confirmation_token: str,
	confirmation_method: str,
) -> dict:
	"""
	Confirm multiple Action Queue entries in a single call.
	Returns a summary of successes and failures.
	"""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.workflow_service import confirm_and_execute

	user, device_id = require_token()

	if not isinstance(action_queue_ids, list) or not action_queue_ids:
		frappe.throw(_("action_queue_ids must be a non-empty list"), frappe.ValidationError)

	results = {"approved": [], "failed": []}
	for qid in action_queue_ids:
		try:
			confirm_and_execute(
				user=user,
				device_id=device_id,
				queue_name=qid,
				confirmation_token=confirmation_token,
				confirmation_method=confirmation_method,
			)
			results["approved"].append(qid)
		except Exception as exc:
			results["failed"].append({"id": qid, "error": str(exc)})

	return results


@frappe.whitelist(allow_guest=True)
def get_action_status(action_queue_id: str) -> dict:
	"""Poll the current status of an Action Queue entry."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token

	user, _ = require_token()
	record = frappe.db.get_value(
		"KCSC AI Action Queue",
		action_queue_id,
		["name", "status", "risk_level", "required_auth", "executed_at", "error_message", "retry_count"],
		as_dict=True,
	)
	if not record:
		frappe.throw(_("Action Queue record not found"), frappe.DoesNotExistError)

	return record
