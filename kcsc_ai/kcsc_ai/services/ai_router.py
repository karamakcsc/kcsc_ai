"""
AI Router — Phase 3

Routes AI requests to the correct execution path based on tenant configuration:

  Local  → Frappe Assistant Core (FAC) running on this site
  Remote → External FAC or LLM endpoint configured in KCSC AI Tenant
  Disabled → Reject immediately

INVARIANT: Regardless of routing, ALL workflow/api actions from AI
go through the Action Queue and require human confirmation.
Query-type requests are answered directly from read-only data without queuing.
"""

import json

import frappe


def route_ai_request(
	user: str,
	device_id: str,
	tenant_name: str,
	ai_payload: dict,
	ip_address: str = "",
) -> dict:
	"""
	Route an AI request to the correct handler.
	Returns either a query result (immediate) or an Action Queue entry (for workflow/api).
	"""
	from kcsc_ai.kcsc_ai.services.ai_service import (
		build_action_queue_payload_from_ai,
		handle_query_action,
	)
	from kcsc_ai.kcsc_ai.services.tenant_policy import check_ai_quota, check_tenant_active

	check_tenant_active(tenant_name)
	check_ai_quota(tenant_name)

	tenant = frappe.get_doc("KCSC AI Tenant", tenant_name)
	action_type = ai_payload.get("action_type", "").lower()

	if tenant.ai_mode == "Disabled":
		frappe.throw("AI is disabled for this tenant", frappe.ValidationError)

	# Query-type: answer immediately, no queue
	if action_type == "query":
		return handle_query_action(user, ai_payload)

	# For mutating actions: always queue
	queue_params = build_action_queue_payload_from_ai(ai_payload)

	if tenant.ai_mode == "Local":
		return _handle_local(user, device_id, tenant, queue_params, ip_address)
	elif tenant.ai_mode == "Remote":
		return _handle_remote(user, device_id, tenant, ai_payload, queue_params, ip_address)

	frappe.throw(f"Unexpected ai_mode: {tenant.ai_mode}")


# ------------------------------------------------------------------
# Local FAC handler
# ------------------------------------------------------------------

def _handle_local(user, device_id, tenant, queue_params, ip_address) -> dict:
	"""
	Handle via local Frappe Assistant Core.
	Creates the Action Queue entry — FAC integration point for Phase 3+.

	When FAC is installed, replace the queue creation call with:
	  fac_result = frappe.get_attr("frappe_assistant_core.api.process_action")(...)
	  # FAC still must return queue IDs, not execute directly
	"""
	from kcsc_ai.kcsc_ai.services.workflow_service import create_queued_action

	queue = create_queued_action(
		user=user,
		device_id=device_id,
		action_type="AI",  # Marks this as AI-sourced in the queue
		tenant=tenant.name,
		ip_address=ip_address,
		**queue_params,
	)

	return {
		"source": "local_fac",
		"action_queue_id": queue.name,
		"status": queue.status,
		"risk_level": queue.risk_level,
		"required_auth": queue.required_auth,
		"message": "AI action queued. Complete verification to execute.",
	}


# ------------------------------------------------------------------
# Remote FAC handler
# ------------------------------------------------------------------

def _handle_remote(user, device_id, tenant, ai_payload, queue_params, ip_address) -> dict:
	"""
	Forward the request to a remote FAC/LLM endpoint.
	The remote endpoint may enrich the payload (intent parsing, context expansion).
	Regardless of what the remote returns, we still queue locally.
	"""
	import urllib.request

	if not tenant.ai_endpoint:
		frappe.throw("Remote AI endpoint not configured for this tenant", frappe.ValidationError)

	# Best-effort remote call — if it fails, fall back to direct queue
	try:
		enriched_payload = _call_remote_fac(tenant, ai_payload)
		# Merge any enrichment from remote (e.g. resolved docname, expanded context)
		if enriched_payload:
			queue_params["payload_dict"].update(enriched_payload.get("enrichment", {}))
	except Exception as exc:
		frappe.log_error(f"Remote FAC call failed, proceeding with local queue: {exc}", "KCSC AI Router")

	from kcsc_ai.kcsc_ai.services.workflow_service import create_queued_action

	queue = create_queued_action(
		user=user,
		device_id=device_id,
		action_type="AI",
		tenant=tenant.name,
		ip_address=ip_address,
		**queue_params,
	)

	return {
		"source": "remote_fac",
		"action_queue_id": queue.name,
		"status": queue.status,
		"risk_level": queue.risk_level,
		"required_auth": queue.required_auth,
		"message": "AI action queued via remote FAC. Complete verification to execute.",
	}


def _call_remote_fac(tenant, ai_payload: dict) -> dict | None:
	"""
	POST to the configured remote FAC endpoint.
	Returns enriched payload dict or None on failure.
	Raises on HTTP errors — caller handles.
	"""
	import urllib.error
	import urllib.parse
	import urllib.request

	body = json.dumps(ai_payload).encode("utf-8")
	req = urllib.request.Request(
		url=tenant.ai_endpoint,
		data=body,
		method="POST",
		headers={
			"Content-Type": "application/json",
			"X-API-Key": tenant.get_password("ai_api_key") or "",
			"X-Tenant": tenant.tenant_name,
		},
	)

	with urllib.request.urlopen(req, timeout=10) as resp:
		return json.loads(resp.read().decode("utf-8"))
