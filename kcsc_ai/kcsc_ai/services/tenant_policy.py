"""
Tenant Policy Engine — Phase 4

Enforces all per-tenant limits and guards:
  - Active status check (gate on every API call)
  - User limit
  - Device limit
  - AI request quota (monthly rolling window via Redis counter)

Also provides usage stats for the management API.
"""

import frappe

_AI_QUOTA_WINDOW = 86400 * 30  # 30-day rolling window in seconds


# ------------------------------------------------------------------
# Gate checks (called at the start of service functions)
# ------------------------------------------------------------------

def check_tenant_active(tenant_name: str):
	"""Raise if the tenant is suspended or inactive."""
	if not tenant_name:
		return  # No tenant configured — single-site mode, allow

	status = frappe.db.get_value("KCSC AI Tenant", tenant_name, "status")
	if not status:
		frappe.throw(f"Tenant '{tenant_name}' not found", frappe.DoesNotExistError)
	if status == "Suspended":
		frappe.throw(f"Tenant '{tenant_name}' is suspended. Contact your administrator.", frappe.PermissionError)
	if status == "Inactive":
		frappe.throw(f"Tenant '{tenant_name}' is inactive.", frappe.PermissionError)


def check_device_limit(tenant_name: str, user: str):
	"""Raise if the user has reached their device quota for this tenant."""
	if not tenant_name:
		return

	tenant = frappe.get_cached_doc("KCSC AI Tenant", tenant_name)
	current = frappe.db.count("KCSC AI Device", {"user": user, "is_blocked": 0})
	if current >= (tenant.max_devices or 0):
		frappe.throw(
			f"Device limit of {tenant.max_devices} reached. "
			"Remove an existing device or contact your administrator.",
			frappe.ValidationError,
		)


def check_user_limit(tenant_name: str):
	"""Raise if the site has reached the tenant's user quota."""
	if not tenant_name:
		return

	tenant = frappe.get_cached_doc("KCSC AI Tenant", tenant_name)
	current = frappe.db.count("User", {"enabled": 1, "user_type": "System User"})
	if current >= (tenant.max_users or 0):
		frappe.throw(
			f"User limit of {tenant.max_users} reached. Upgrade your plan.",
			frappe.ValidationError,
		)


def check_ai_quota(tenant_name: str):
	"""Raise if the tenant's monthly AI request quota is exhausted."""
	if not tenant_name:
		return

	from kcsc_ai.kcsc_ai.utils.redis_helper import check_rate_limit, get_value, set_value

	tenant = frappe.get_cached_doc("KCSC AI Tenant", tenant_name)
	max_requests = tenant.max_ai_requests or 0

	if max_requests <= 0:
		return  # Unlimited

	cache_key = f"kcsc_ai_quota:{tenant_name}"
	current = get_value(cache_key) or 0

	if int(current) >= max_requests:
		frappe.throw(
			f"AI request quota of {max_requests}/month exhausted for tenant '{tenant_name}'. "
			"Upgrade your plan or contact support.",
			frappe.PermissionError,
		)

	# Increment counter
	set_value(cache_key, int(current) + 1, _AI_QUOTA_WINDOW)


# ------------------------------------------------------------------
# Suspension / Activation
# ------------------------------------------------------------------

def suspend_tenant(tenant_name: str):
	"""Suspend a tenant and revoke all its users' tokens."""
	from kcsc_ai.kcsc_ai.services.token_service import revoke_all_user_tokens

	if not frappe.db.exists("KCSC AI Tenant", tenant_name):
		frappe.throw(f"Tenant '{tenant_name}' not found", frappe.DoesNotExistError)

	# Revoke every active token on this tenant's site
	active_users = frappe.db.get_all(
		"KCSC AI Token",
		filters={"revoked": 0},
		distinct=True,
		pluck="user",
	)
	for user in active_users:
		revoke_all_user_tokens(user)

	frappe.db.set_value("KCSC AI Tenant", tenant_name, "status", "Suspended")
	frappe.db.commit()


# ------------------------------------------------------------------
# Usage Statistics
# ------------------------------------------------------------------

def get_usage_stats(tenant_name: str) -> dict:
	"""
	Return real-time usage metrics for a tenant.
	Combines DB counts with Redis quota counters.
	"""
	from kcsc_ai.kcsc_ai.utils.redis_helper import get_value

	tenant = frappe.get_doc("KCSC AI Tenant", tenant_name)

	active_tokens = frappe.db.count("KCSC AI Token", {"revoked": 0, "expires_at": (">", frappe.utils.now_datetime())})
	active_devices = frappe.db.count("KCSC AI Device", {"is_blocked": 0})
	active_users = frappe.db.count("User", {"enabled": 1, "user_type": "System User"})

	ai_requests_this_month = int(get_value(f"kcsc_ai_quota:{tenant_name}") or 0)

	pending_actions = frappe.db.count(
		"KCSC AI Action Queue",
		{"tenant": tenant_name, "status": ("in", ["Pending", "Awaiting Confirmation"])},
	)

	return {
		"active_users": active_users,
		"user_limit": tenant.max_users,
		"active_devices": active_devices,
		"device_limit": tenant.max_devices,
		"ai_requests_this_month": ai_requests_this_month,
		"ai_request_limit": tenant.max_ai_requests,
		"active_tokens": active_tokens,
		"pending_actions": pending_actions,
	}
