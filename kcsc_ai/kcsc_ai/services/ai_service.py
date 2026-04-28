"""
AI Action Validation Service — Phase 3

Enforces the strict AI action schema from spec §6.2.
AI is NEVER allowed to execute directly — it always creates an Action Queue entry.
"""

import frappe

# Valid action types that AI is permitted to request
_VALID_ACTION_TYPES = {"workflow", "query", "api"}

# Read-only query — answered immediately without queuing
_QUERY_ONLY_TYPES = {"query"}


def validate_ai_action_schema(payload: dict) -> None:
	"""
	Validate an AI-submitted action payload against the strict schema.

	Required fields for workflow/api:
	  action_type, doctype, name, action

	Required fields for query:
	  action_type, doctype, query
	"""
	action_type = str(payload.get("action_type", "")).lower()

	if action_type not in _VALID_ACTION_TYPES:
		frappe.throw(
			f"Invalid action_type '{action_type}'. Must be one of: {sorted(_VALID_ACTION_TYPES)}",
			frappe.ValidationError,
		)

	if action_type in ("workflow", "api"):
		_validate_mutating_action(payload)

	if action_type == "query":
		_validate_query_action(payload)


def handle_query_action(user: str, payload: dict) -> dict:
	"""
	Answer a read-only query immediately.
	Queries fetch data but never modify documents.
	"""
	doctype = payload.get("doctype")
	name = payload.get("name")
	query_text = payload.get("query", "")

	if not frappe.db.exists("DocType", doctype):
		frappe.throw(f"DocType '{doctype}' not found", frappe.DoesNotExistError)

	if not frappe.has_permission(doctype, "read", user=user):
		frappe.throw(f"No read permission on {doctype}", frappe.PermissionError)

	if name and frappe.db.exists(doctype, name):
		doc = frappe.get_doc(doctype, name)
		return {
			"type": "query_result",
			"doctype": doctype,
			"name": name,
			"data": doc.as_dict(),
			"query": query_text,
		}

	# Listing query
	records = frappe.db.get_all(
		doctype,
		fields=["name", "modified"],
		limit=20,
		order_by="modified desc",
	)
	return {
		"type": "query_result",
		"doctype": doctype,
		"name": None,
		"records": records,
		"query": query_text,
	}


def build_action_queue_payload_from_ai(ai_payload: dict) -> dict:
	"""
	Convert a validated AI payload into the format expected by create_queued_action().
	"""
	return {
		"action_type": ai_payload["action_type"],
		"reference_doctype": ai_payload.get("doctype", ""),
		"reference_name": ai_payload.get("name", ""),
		"workflow_action": ai_payload.get("action", ""),
		"payload_dict": {
			"source": "ai",
			"query": ai_payload.get("query", ""),
			"context": ai_payload.get("context", {}),
			"resolved_as": "Workflow" if ai_payload["action_type"] == "workflow" else "API",
		},
	}


# ------------------------------------------------------------------
# Private validators
# ------------------------------------------------------------------

def _validate_mutating_action(payload: dict):
	"""Validate fields required for workflow/api actions."""
	doctype = payload.get("doctype")
	name = payload.get("name")
	action = payload.get("action")

	if not doctype:
		frappe.throw("AI action missing required field: doctype", frappe.ValidationError)
	if not name:
		frappe.throw("AI action missing required field: name", frappe.ValidationError)
	if not action:
		frappe.throw("AI action missing required field: action (e.g. 'Approve')", frappe.ValidationError)

	# Verify the DocType exists on this site
	if not frappe.db.exists("DocType", doctype):
		frappe.throw(f"DocType '{doctype}' does not exist on this site", frappe.ValidationError)

	# Verify the document exists
	if not frappe.db.exists(doctype, name):
		frappe.throw(f"Document '{doctype}' / '{name}' not found", frappe.DoesNotExistError)


def _validate_query_action(payload: dict):
	doctype = payload.get("doctype")
	if not doctype:
		frappe.throw("AI query missing required field: doctype", frappe.ValidationError)

	if not frappe.db.exists("DocType", doctype):
		frappe.throw(f"DocType '{doctype}' does not exist", frappe.ValidationError)
