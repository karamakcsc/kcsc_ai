"""
Authentication API — Phase 2

Endpoints (all via /api/method/kcsc_ai.kcsc_ai.api.auth.<name>):

  POST generate_login_qr     → produce a rotating login QR token
  POST generate_action_qr    → produce a one-time action-confirmation QR
  POST generate_static_qr    → produce a device-pairing static QR
  POST qr_login              → exchange a scanned QR token for access+refresh tokens
  POST refresh               → exchange a refresh token for a new access token
  POST logout                → revoke all tokens for the current session
"""

import frappe
from frappe import _


# -----------------------------------------------------------------------
# QR Generation — no auth required (rate-limited by IP)
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def generate_login_qr(user: str) -> dict:
	"""
	Generate a dynamic login QR token for a given user.
	The QR rotates every 60 seconds; the mobile app re-polls this endpoint.
	Returns the opaque token string to be rendered as a QR code.
	"""
	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip
	from kcsc_ai.kcsc_ai.services.qr_service import generate_login_qr_payload
	from kcsc_ai.kcsc_ai.utils.redis_helper import check_rate_limit

	ip = _get_client_ip()
	if not check_rate_limit(ip, "generate_login_qr", max_attempts=10, window_seconds=60):
		frappe.throw(_("QR generation rate limit exceeded. Wait 60 seconds."), frappe.AuthenticationError)

	if not frappe.db.exists("User", user):
		frappe.throw(_("User not found"), frappe.DoesNotExistError)

	payload = generate_login_qr_payload(user)
	return {"qr_data": payload["qr_data"], "expires_in": payload["expires_in"], "qr_type": "login"}


@frappe.whitelist(allow_guest=False)
def generate_action_qr(action_queue_id: str) -> dict:
	"""
	Generate a one-time action-confirmation QR for an Awaiting Confirmation queue entry.
	The signed token is sent to the mobile device to approve the pending action.
	"""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.qr_service import generate_action_qr_payload

	user, device_id = require_token()

	queue = frappe.get_doc("KCSC AI Action Queue", action_queue_id)
	if queue.user != user:
		frappe.throw(_("You are not authorised to approve this action"), frappe.PermissionError)
	if queue.status != "Awaiting Confirmation":
		frappe.throw(_(f"Action is not awaiting confirmation (current status: {queue.status})"))

	payload = generate_action_qr_payload(user, action_queue_id)
	return {"qr_data": payload["qr_data"], "expires_in": payload["expires_in"], "qr_type": "action"}


@frappe.whitelist(allow_guest=True)
def generate_static_qr(user: str) -> dict:
	"""
	Generate a static device-pairing QR. Valid for 10 minutes.
	Used during initial device registration from the mobile app.
	"""
	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip
	from kcsc_ai.kcsc_ai.services.qr_service import generate_static_pairing_qr
	from kcsc_ai.kcsc_ai.utils.redis_helper import check_rate_limit

	ip = _get_client_ip()
	if not check_rate_limit(ip, "generate_static_qr", max_attempts=5, window_seconds=300):
		frappe.throw(_("Static QR generation rate limit exceeded."), frappe.AuthenticationError)

	if not frappe.db.exists("User", user):
		frappe.throw(_("User not found"), frappe.DoesNotExistError)

	payload = generate_static_pairing_qr(user)
	return {"qr_data": payload["qr_data"], "expires_in": payload["expires_in"], "qr_type": "static"}


# -----------------------------------------------------------------------
# Token Exchange
# -----------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def qr_login(qr_token: str, device_id: str, device_name: str = "", platform: str = "Unknown") -> dict:
	"""
	Exchange a scanned QR token for an access token + refresh token pair.

	Flow:
	  1. Consume (validate + mark used) the QR token
	  2. Register/update the device
	  3. Issue access + refresh tokens
	  4. Log the login event
	"""
	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip, get_request_tenant
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.device_service import get_or_create_device
	from kcsc_ai.kcsc_ai.services.token_service import (
		consume_qr_token,
		generate_access_token,
		generate_refresh_token,
	)

	if not device_id:
		frappe.throw(_("device_id is required"), frappe.ValidationError)

	ip = _get_client_ip()

	# Validate and consume the QR token (one-time use)
	context = consume_qr_token(qr_token)
	user = context["user"]

	# Ensure device exists and is not blocked
	device = get_or_create_device(
		user=user,
		device_id=device_id,
		device_name=device_name or device_id,
		platform=platform,
		ip_address=ip,
	)

	if device.is_blocked:
		log_activity("Security Event", user=user, device_id=device_id, ip_address=ip, status="Failed",
					 description="Login blocked — device is blocked")
		frappe.throw(_("This device has been blocked. Contact your administrator."), frappe.AuthenticationError)

	# Issue tokens
	access_raw, _ = generate_access_token(user, device_id, ip)
	refresh_raw, _ = generate_refresh_token(user, device_id, ip)

	device.update_last_active(ip)

	tenant = get_request_tenant(user)
	log_activity("Login", user=user, tenant=tenant, device_id=device_id, ip_address=ip, status="Success",
				 description="QR login successful")

	return {
		"access_token": access_raw,
		"refresh_token": refresh_raw,
		"token_type": "Bearer",
		"expires_in": 3600,
		"user": user,
	}


@frappe.whitelist(allow_guest=True)
def refresh(refresh_token: str, device_id: str = "") -> dict:
	"""Exchange a valid refresh token for a new access token."""
	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.token_service import (
		generate_access_token,
		validate_refresh_token,
	)

	ip = _get_client_ip()
	user = validate_refresh_token(refresh_token)

	access_raw, _ = generate_access_token(user, device_id, ip)
	log_activity("Token Refresh", user=user, device_id=device_id, ip_address=ip, status="Success")

	return {"access_token": access_raw, "token_type": "Bearer", "expires_in": 3600}


@frappe.whitelist(allow_guest=True)
def logout() -> dict:
	"""Revoke all tokens for the currently authenticated user."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.token_service import revoke_all_user_tokens

	user, device_id = require_token()
	revoke_all_user_tokens(user)
	log_activity("Security Event", user=user, device_id=device_id, description="User logged out — all tokens revoked")

	return {"message": "Logged out successfully"}
