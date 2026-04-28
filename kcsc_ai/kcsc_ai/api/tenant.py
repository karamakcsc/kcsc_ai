"""
Tenant Management API — Phase 4

System Manager-only endpoints for managing multi-tenant SaaS configuration.

Endpoints:
  GET   list_tenants       → all tenants
  GET   get_tenant         → single tenant details + live usage stats
  POST  update_tenant      → modify plan/limits/status
  POST  suspend_tenant     → suspend a tenant (revokes all tokens)
  POST  activate_tenant    → re-activate a suspended tenant
  GET   usage_stats        → real-time usage metrics for a tenant
"""

import frappe
from frappe import _


def _require_system_manager():
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	user, device_id = require_token()
	if "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Tenant management requires System Manager role"), frappe.PermissionError)
	return user, device_id


@frappe.whitelist(allow_guest=True)
def list_tenants() -> list:
	"""List all tenant records."""
	_require_system_manager()
	return frappe.db.get_all(
		"KCSC AI Tenant",
		fields=["name", "tenant_name", "status", "plan", "site_url", "max_users",
				"max_devices", "max_ai_requests", "ai_mode", "created_at"],
		order_by="created_at desc",
	)


@frappe.whitelist(allow_guest=True)
def get_tenant(tenant_name: str) -> dict:
	"""Return full tenant details including live usage stats."""
	_require_system_manager()
	from kcsc_ai.kcsc_ai.services.tenant_policy import get_usage_stats

	if not frappe.db.exists("KCSC AI Tenant", tenant_name):
		frappe.throw(_("Tenant not found"), frappe.DoesNotExistError)

	tenant = frappe.get_doc("KCSC AI Tenant", tenant_name)
	stats = get_usage_stats(tenant_name)

	return {
		"tenant_name": tenant.tenant_name,
		"status": tenant.status,
		"plan": tenant.plan,
		"site_url": tenant.site_url,
		"isolation_level": tenant.isolation_level,
		"ai_mode": tenant.ai_mode,
		"limits": tenant.get_limits(),
		"usage": stats,
	}


@frappe.whitelist(allow_guest=True)
def update_tenant(
	tenant_name: str,
	plan: str = None,
	max_users: int = None,
	max_devices: int = None,
	max_ai_requests: int = None,
	ai_mode: str = None,
	ai_endpoint: str = None,
) -> dict:
	"""Update tenant plan or limits."""
	_require_system_manager()

	if not frappe.db.exists("KCSC AI Tenant", tenant_name):
		frappe.throw(_("Tenant not found"), frappe.DoesNotExistError)

	tenant = frappe.get_doc("KCSC AI Tenant", tenant_name)
	if plan is not None:
		tenant.plan = plan
	if max_users is not None:
		tenant.max_users = int(max_users)
	if max_devices is not None:
		tenant.max_devices = int(max_devices)
	if max_ai_requests is not None:
		tenant.max_ai_requests = int(max_ai_requests)
	if ai_mode is not None:
		tenant.ai_mode = ai_mode
	if ai_endpoint is not None:
		tenant.ai_endpoint = ai_endpoint

	tenant.save(ignore_permissions=True)
	frappe.db.commit()
	return {"message": f"Tenant '{tenant_name}' updated", "tenant": tenant_name}


@frappe.whitelist(allow_guest=True)
def suspend_tenant(tenant_name: str, reason: str = "") -> dict:
	"""Suspend a tenant — sets status=Suspended and revokes all tokens."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.tenant_policy import suspend_tenant as _suspend

	admin_user, _ = require_token()
	if "System Manager" not in frappe.get_roles(admin_user):
		frappe.throw(_("Requires System Manager"), frappe.PermissionError)

	_suspend(tenant_name)
	log_activity(
		"Security Event", user=admin_user, tenant=tenant_name,
		description=f"Tenant '{tenant_name}' suspended. Reason: {reason}", status="Warning",
	)
	return {"message": f"Tenant '{tenant_name}' suspended"}


@frappe.whitelist(allow_guest=True)
def activate_tenant(tenant_name: str) -> dict:
	"""Re-activate a suspended tenant."""
	_require_system_manager()

	if not frappe.db.exists("KCSC AI Tenant", tenant_name):
		frappe.throw(_("Tenant not found"), frappe.DoesNotExistError)

	frappe.db.set_value("KCSC AI Tenant", tenant_name, "status", "Active")
	frappe.db.commit()
	return {"message": f"Tenant '{tenant_name}' activated"}


@frappe.whitelist(allow_guest=True)
def usage_stats(tenant_name: str) -> dict:
	"""Real-time usage metrics for a tenant."""
	_require_system_manager()
	from kcsc_ai.kcsc_ai.services.tenant_policy import get_usage_stats
	return get_usage_stats(tenant_name)
