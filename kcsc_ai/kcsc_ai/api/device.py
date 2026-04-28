"""
Device Management API — Phase 2

Endpoints:
  POST  register_device    → register/update a device after pairing QR scan
  GET   list_devices       → list all devices for the current user
  POST  trust_device       → mark a device as trusted (admin only)
  POST  block_device       → block a device (admin only)
  POST  untrust_device     → remove trust from a device
  DELETE remove_device     → delete a device and revoke its tokens
"""

import frappe
from frappe import _


@frappe.whitelist(allow_guest=True)
def register_device(
	device_id: str,
	device_name: str,
	platform: str,
	pairing_token: str = "",
	device_fingerprint: str = "",
) -> dict:
	"""
	Register a new device.
	Requires either a valid pairing_token (from generate_static_qr) or
	a valid Bearer token in the Authorization header.
	"""
	from kcsc_ai.kcsc_ai.api.middleware import _get_client_ip, get_request_tenant
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.device_service import register_device as _register
	from kcsc_ai.kcsc_ai.services.token_service import consume_qr_token

	ip = _get_client_ip()

	# Auth: pairing token OR bearer token
	if pairing_token:
		context = consume_qr_token(pairing_token)
		user = context["user"]
		frappe.set_user(user)
	else:
		from kcsc_ai.kcsc_ai.api.middleware import require_token
		user, _ = require_token()

	if not device_id or not device_name or not platform:
		frappe.throw(_("device_id, device_name, and platform are required"), frappe.ValidationError)

	tenant = get_request_tenant(user)
	device = _register(
		user=user,
		device_id=device_id,
		device_name=device_name,
		platform=platform,
		device_fingerprint=device_fingerprint,
		ip_address=ip,
		tenant=tenant,
	)

	log_activity(
		"Device Registration", user=user, tenant=tenant, device_id=device_id, ip_address=ip,
		description=f"Device '{device_name}' registered on {platform}",
	)

	return {
		"device_id": device.device_id,
		"device_name": device.device_name,
		"platform": device.platform,
		"trusted": bool(device.trusted),
		"is_blocked": bool(device.is_blocked),
	}


@frappe.whitelist(allow_guest=True)
def list_devices() -> list:
	"""Return all devices registered to the current user."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token

	user, _ = require_token()
	devices = frappe.db.get_all(
		"KCSC AI Device",
		filters={"user": user},
		fields=["device_id", "device_name", "platform", "trusted", "is_blocked", "last_active", "last_ip"],
		order_by="last_active desc",
	)
	return devices


@frappe.whitelist(allow_guest=True)
def trust_device(device_id: str) -> dict:
	"""Mark a device as trusted. Requires System Manager role."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token

	user, _ = require_token()
	if "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Only System Managers can trust devices"), frappe.PermissionError)

	rec = frappe.db.get_value("KCSC AI Device", {"device_id": device_id}, "name")
	if not rec:
		frappe.throw(_("Device not found"), frappe.DoesNotExistError)

	frappe.db.set_value("KCSC AI Device", rec, "trusted", 1)
	frappe.db.commit()
	return {"message": f"Device {device_id} is now trusted"}


@frappe.whitelist(allow_guest=True)
def block_device(device_id: str, reason: str = "") -> dict:
	"""Block a device and revoke all its tokens. Requires System Manager."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.activity_logger import log_activity
	from kcsc_ai.kcsc_ai.services.token_service import revoke_all_user_tokens

	admin_user, _ = require_token()
	if "System Manager" not in frappe.get_roles(admin_user):
		frappe.throw(_("Only System Managers can block devices"), frappe.PermissionError)

	rec = frappe.db.get_value("KCSC AI Device", {"device_id": device_id}, ["name", "user"], as_dict=True)
	if not rec:
		frappe.throw(_("Device not found"), frappe.DoesNotExistError)

	frappe.db.set_value("KCSC AI Device", rec.name, {"is_blocked": 1, "trusted": 0})
	revoke_all_user_tokens(rec.user)
	frappe.db.commit()

	log_activity(
		"Security Event", user=rec.user, device_id=device_id, status="Warning",
		description=f"Device blocked by {admin_user}. Reason: {reason}",
	)
	return {"message": f"Device {device_id} blocked and all tokens revoked"}


@frappe.whitelist(allow_guest=True)
def remove_device(device_id: str) -> dict:
	"""Remove a device owned by the current user and revoke its tokens."""
	from kcsc_ai.kcsc_ai.api.middleware import require_token
	from kcsc_ai.kcsc_ai.services.token_service import revoke_all_user_tokens

	user, _ = require_token()
	rec = frappe.db.get_value(
		"KCSC AI Device", {"device_id": device_id, "user": user}, "name"
	)
	if not rec:
		frappe.throw(_("Device not found or not owned by you"), frappe.DoesNotExistError)

	frappe.delete_doc("KCSC AI Device", rec, ignore_permissions=True)
	revoke_all_user_tokens(user)
	frappe.db.commit()
	return {"message": f"Device {device_id} removed"}
