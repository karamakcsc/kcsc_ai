"""
AI Integration API — Phase 3

CRITICAL RULE: AI can suggest or query but NEVER executes directly.
Every AI action is enqueued in the Action Queue and requires human confirmation.

Endpoints:
  POST  request   → validate AI action schema → queue it → return queue ID
  GET   history   → AI request history for the current user
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def request(
	action_type: str,
	doctype: str,
	name: str = "",
	action: str = "",
	query: str = "",
	context: str = "{}",
) -> dict:
	"""
	Submit an AI action request.

	Strict schema (spec §6.2):
	  action_type: "workflow" | "query" | "api"
	  doctype:     target DocType
	  name:        document name (required for workflow/api)
	  action:      workflow transition name (required for workflow)
	  query:       natural-language query (for action_type=query)
	  context:     extra JSON context

	Query-type requests are answered immediately from read-only data.
	Workflow/API requests are queued and require human confirmation.
	"""
	import json

	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip, get_request_tenant, require_token
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.ai_service import validate_ai_action_schema
	from kcsc_ai.kcsc_ai.services.ai_router import route_ai_request

	user, device_id = require_token()
	ip = _get_client_ip()
	tenant_name = get_request_tenant(user)

	try:
		ctx = json.loads(context) if isinstance(context, str) else context
	except (ValueError, TypeError):
		frappe.throw(_("context must be valid JSON"), frappe.ValidationError)

	ai_payload = {
		"action_type": action_type,
		"doctype": doctype,
		"name": name,
		"action": action,
		"query": query,
		"context": ctx,
	}

	# Schema validation — AI cannot bypass this gate
	validate_ai_action_schema(ai_payload)

	# Route through AI layer → always via Action Queue for mutating actions
	result = route_ai_request(
		user=user,
		device_id=device_id,
		tenant_name=tenant_name,
		ai_payload=ai_payload,
		ip_address=ip,
	)

	log_activity(
		"AI Request", user=user, tenant=tenant_name, device_id=device_id, ip_address=ip,
		reference_doctype=doctype, reference_name=name,
		description=f"AI {action_type} on {doctype}/{name}",
		metadata={"action": action, "query": query[:200] if query else ""},
	)

	return result


@frappe.whitelist(allow_guest=True)
def history(limit: int = 20) -> list:
	"""Return recent AI requests for the current user."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token

	user, _ = require_token()
	return frappe.db.get_all(
		"KCSC AI Activity Log",
		filters={"user": user, "activity_type": "AI Request"},
		fields=["name", "reference_doctype", "reference_name", "description", "status", "created_at"],
		order_by="created_at desc",
		limit=min(int(limit), 100),
	)
