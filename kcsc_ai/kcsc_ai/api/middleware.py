"""
Request-level middleware helpers.

Every KCSC AI API endpoint that requires authentication calls
_require_token() as its first line. This:
  1. Reads the Authorization: Bearer <token> header
  2. Validates the token via token_service
  3. Calls frappe.set_user() so all subsequent ERPNext permission
     checks run under the correct user identity
  4. Returns (user, device_id) for the caller's use
"""

import frappe


def require_token() -> tuple[str, str]:
	"""
	Validate the KCSC AI Bearer token from the Authorization header.
	Sets the Frappe session user on success.
	Raises frappe.AuthenticationError on any failure.
	"""
	from kcsc_ai.kcsc_ai.services.token_service import validate_access_token
	from kcsc_ai.kcsc_ai.utils.redis_helper import check_rate_limit

	auth = frappe.get_request_header("Authorization", "")
	if not auth.startswith("Bearer "):
		frappe.throw("Authorization header must use 'Bearer <token>' scheme", frappe.AuthenticationError)

	raw_token = auth[7:].strip()
	if not raw_token:
		frappe.throw("Bearer token is empty", frappe.AuthenticationError)

	# Rate-limit token validation attempts per IP to blunt brute-force
	client_ip = _get_client_ip()
	if not check_rate_limit(client_ip, "token_validate", max_attempts=60, window_seconds=60):
		frappe.throw("Too many authentication attempts. Please wait.", frappe.AuthenticationError)

	user = validate_access_token(raw_token)
	device_id = frappe.get_request_header("X-Device-ID", "")

	# Set ERPNext user context so all doc permission checks apply correctly
	frappe.set_user(user)

	return user, device_id


def get_request_tenant(user: str) -> str | None:
	"""
	Derive the tenant for this request.
	Looks up X-Tenant-ID header first; falls back to the first active
	tenant record that matches the current site.
	"""
	tenant_id = frappe.get_request_header("X-Tenant-ID", "")
	if tenant_id:
		if not frappe.db.exists("KCSC AI Tenant", tenant_id):
			frappe.throw(f"Tenant '{tenant_id}' not found", frappe.DoesNotExistError)
		return tenant_id

	# Default: first active tenant on this site
	tenant = frappe.db.get_value("KCSC AI Tenant", {"status": "Active"}, "name")
	return tenant


def _get_client_ip() -> str:
	try:
		request = frappe.local.request
		forwarded = request.headers.get("X-Forwarded-For", "")
		if forwarded:
			return forwarded.split(",")[0].strip()
		return request.remote_addr or ""
	except Exception:
		return ""


def validate_kcsc_bearer_token():
	"""
	Frappe auth hook (registered in hooks.py:auth_hooks).

	Frappe's validate_auth() raises AuthenticationError if any Authorization
	header is present but the session user is still Guest after OAuth and API-key
	checks. We intercept here so our custom KCSC Bearer tokens pass that check.
	"""
	auth = frappe.get_request_header("Authorization", "")
	if not auth.startswith("Bearer "):
		return

	raw_token = auth[7:].strip()
	if not raw_token:
		return

	try:
		from kcsc_ai.kcsc_ai.services.token_service import validate_access_token
		user = validate_access_token(raw_token)
		if user:
			# frappe.set_user() resets frappe.local.form_dict — save and restore it
			# so the endpoint function receives its parameters correctly.
			# Same pattern used by frappe.auth.validate_api_key_secret.
			saved_form_dict = frappe.local.form_dict
			frappe.set_user(user)
			frappe.local.form_dict = saved_form_dict
	except Exception:
		pass  # Invalid/expired token — endpoint-level require_token() returns the precise error


def success(data: dict | list, message: str = "ok") -> dict:
	"""Standardised success envelope for all KCSC AI API responses."""
	return {"status": "success", "message": message, "data": data}


def error(message: str, code: str = "error") -> dict:
	"""Standardised error envelope. Prefer frappe.throw() for hard errors."""
	return {"status": "error", "code": code, "message": message}
