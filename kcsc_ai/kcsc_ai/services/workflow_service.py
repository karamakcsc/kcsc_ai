"""
Workflow Execution Service — Phase 2

This is the enforcement layer between API/AI requests and ERPNext's workflow engine.

INVARIANT: frappe.model.workflow.apply_workflow() is ONLY called from
execute_approved_action() which runs as a background job (frappe.enqueue).
It is NEVER called synchronously from an API endpoint.

Execution state machine:
  create_queued_action  →  Pending
       ↓  (risk assessment)
  Awaiting Confirmation
       ↓  (confirm_and_execute)
  Approved
       ↓  (frappe.enqueue → execute_approved_action)
  Executed  OR  Failed
"""

import json

import frappe


# ------------------------------------------------------------------
# Step 1: Queue the action
# ------------------------------------------------------------------

def create_queued_action(
	user: str,
	device_id: str,
	action_type: str,
	reference_doctype: str,
	reference_name: str,
	workflow_action: str,
	payload_dict: dict,
	tenant: str,
	idempotency_key: str = "",
	ip_address: str = "",
) -> object:
	"""
	Create an Action Queue record and assess its risk.
	Returns the saved document.
	"""
	from kcsc_ai.kcsc_ai.services.risk_engine import calculate_risk
	from kcsc_ai.kcsc_ai.services.tenant_policy import check_tenant_active

	# Tenant must be active before queuing any action
	check_tenant_active(tenant)

	# Assess risk to determine auth requirements
	risk = calculate_risk(
		user=user,
		device_id=device_id,
		action_type=action_type,
		reference_doctype=reference_doctype,
		payload=payload_dict,
		current_ip=ip_address,
	)

	queue = frappe.get_doc({
		"doctype": "KCSC AI Action Queue",
		"user": user,
		"tenant": tenant,
		"action_type": _normalise_action_type(action_type),
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"workflow_action": workflow_action,
		"payload": json.dumps(payload_dict, default=str),
		"status": "Awaiting Confirmation",
		"risk_level": risk.risk_level,
		"required_auth": risk.required_auth,
		"idempotency_key": idempotency_key or "",
	})
	queue.insert(ignore_permissions=True)
	frappe.db.commit()
	return queue


# ------------------------------------------------------------------
# Step 2: Confirm the action
# ------------------------------------------------------------------

def confirm_and_execute(
	user: str,
	device_id: str,
	queue_name: str,
	confirmation_token: str,
	confirmation_method: str,
) -> dict:
	"""
	Validate the confirmation proof, approve the queue entry,
	and enqueue background execution.

	confirmation_method: "QR" | "OTP" | "Device"
	"""
	queue = frappe.get_doc("KCSC AI Action Queue", queue_name)

	_assert_queue_ownership(queue, user)
	_assert_status(queue, "Awaiting Confirmation")
	_assert_auth_satisfied(queue, user, device_id, confirmation_token, confirmation_method)

	queue.approve(confirmed_by=user, method=confirmation_method)
	frappe.db.commit()

	# Enqueue execution — never execute synchronously
	frappe.enqueue(
		"kcsc_ai.kcsc_ai.services.workflow_service.execute_approved_action",
		queue="default",
		is_async=True,
		queue_name=queue_name,
	)

	return {
		"action_queue_id": queue_name,
		"status": "Approved",
		"message": "Action approved and queued for execution.",
	}


# ------------------------------------------------------------------
# Step 3: Background execution (called by frappe.enqueue)
# ------------------------------------------------------------------

def execute_approved_action(queue_name: str):
	"""
	Execute an approved workflow action.
	Called exclusively as a background job — never from a request.
	Uses frappe.model.workflow.apply_workflow() for all Workflow-type actions.
	"""
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity

	queue = frappe.get_doc("KCSC AI Action Queue", queue_name)

	# Guard against duplicate execution (e.g. retried job)
	if queue.status != "Approved":
		return

	try:
		if queue.action_type == "Workflow":
			_run_workflow_action(queue)
		elif queue.action_type == "API":
			_run_api_action(queue)
		elif queue.action_type == "AI":
			_run_ai_action(queue)
		else:
			frappe.throw(f"Unknown action_type: {queue.action_type}")

		queue.mark_executed()
		log_activity(
			"Workflow Action", user=queue.user, tenant=queue.tenant,
			reference_doctype=queue.reference_doctype, reference_name=queue.reference_name,
			description=f"Action {queue_name} executed successfully",
			status="Success", action_queue_ref=queue_name,
		)

	except Exception as exc:
		error_msg = str(exc)[:500]
		queue.mark_failed(error_msg)
		log_activity(
			"Workflow Action", user=queue.user, tenant=queue.tenant,
			reference_doctype=queue.reference_doctype, reference_name=queue.reference_name,
			description=f"Action {queue_name} FAILED: {error_msg}",
			status="Failed", action_queue_ref=queue_name,
		)
		frappe.log_error(f"Action Queue {queue_name} failed: {exc}", "KCSC AI Workflow Execution")
		raise


# ------------------------------------------------------------------
# Execution handlers
# ------------------------------------------------------------------

def _run_workflow_action(queue):
	"""
	Apply an ERPNext workflow transition using the native Frappe engine.
	This is the ONLY place where apply_workflow is called in the entire app.
	"""
	from frappe.model.workflow import apply_workflow

	if not queue.reference_doctype or not queue.reference_name or not queue.workflow_action:
		frappe.throw(
			"Workflow actions require reference_doctype, reference_name, and workflow_action"
		)

	doc = frappe.get_doc(queue.reference_doctype, queue.reference_name)

	# Run as the user who requested the action (respects their ERPNext permissions)
	frappe.set_user(queue.user)
	apply_workflow(doc, queue.workflow_action)
	frappe.set_user("Administrator")  # restore after execution


def _run_api_action(queue):
	"""Execute a whitelisted API method from the queue payload."""
	payload = json.loads(queue.payload or "{}")
	method = payload.get("method")
	if not method:
		frappe.throw("API action payload missing 'method' key")

	# Only call whitelisted methods — never eval arbitrary code
	fn = frappe.get_attr(method)
	if not getattr(fn, "whitelisted", False):
		frappe.throw(f"Method '{method}' is not whitelisted for KCSC AI execution")

	frappe.set_user(queue.user)
	fn(**payload.get("kwargs", {}))
	frappe.set_user("Administrator")


def _run_ai_action(queue):
	"""
	Execute an AI-sourced workflow action.
	AI actions always resolve to a Workflow or API call internally.
	"""
	payload = json.loads(queue.payload or "{}")
	resolved_action_type = payload.get("resolved_as", "Workflow")
	queue.action_type = resolved_action_type

	if resolved_action_type == "Workflow":
		_run_workflow_action(queue)
	elif resolved_action_type == "API":
		_run_api_action(queue)


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------

def _assert_queue_ownership(queue, user: str):
	if queue.user != user and "System Manager" not in frappe.get_roles(user):
		frappe.throw("You are not authorised to confirm this action", frappe.PermissionError)


def _assert_status(queue, expected: str):
	if queue.status != expected:
		frappe.throw(
			f"Action is in status '{queue.status}', expected '{expected}'",
			frappe.ValidationError,
		)


def _assert_auth_satisfied(queue, user: str, device_id: str, token: str, method: str):
	"""Validate that the provided confirmation satisfies the required auth level."""
	required = queue.required_auth  # "QR" | "QR + Device" | "QR + OTP"

	if method == "QR":
		_validate_qr_confirmation(user, queue.name, token)
	elif method == "OTP":
		_validate_otp_confirmation(user, queue.name, token)
	elif method == "Device":
		_validate_device_confirmation(user, device_id)
	else:
		frappe.throw(f"Unknown confirmation method: {method}", frappe.ValidationError)

	# Compound auth checks
	if required == "QR + Device" and method != "QR":
		_validate_device_confirmation(user, device_id)
	if required == "QR + OTP" and method == "QR":
		frappe.throw("This action requires QR + OTP. Submit OTP via confirmation_method='OTP'.")


def _validate_qr_confirmation(user: str, queue_id: str, token: str):
	from kcsc_ai.kcsc_ai.services.token_service import consume_qr_token

	context = consume_qr_token(token)
	if context.get("user") != user:
		frappe.throw("QR token does not belong to this user", frappe.AuthenticationError)

	ctx_action = context.get("action_context", {})
	if ctx_action.get("qr_type") == "action":
		if ctx_action.get("action_queue_id") != queue_id:
			frappe.throw("QR token is for a different action", frappe.AuthenticationError)


def _validate_otp_confirmation(user: str, queue_id: str, otp: str):
	from kcsc_ai.kcsc_ai.services.otp_service import validate_otp
	validate_otp(user, queue_id, otp)


def _validate_device_confirmation(user: str, device_id: str):
	from kcsc_ai.kcsc_ai.services.device_service import validate_device_trust
	if not validate_device_trust(user, device_id):
		frappe.throw(
			"Device confirmation failed — device is not trusted. "
			"Ask a System Manager to trust this device.",
			frappe.AuthenticationError,
		)


def _normalise_action_type(raw: str) -> str:
	mapping = {"workflow": "Workflow", "api": "API", "ai": "AI"}
	return mapping.get(raw.lower(), raw.capitalize())
