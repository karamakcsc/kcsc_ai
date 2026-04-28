"""
Device Registration & Management Service — Phase 2
"""

import frappe


def get_or_create_device(
	user: str,
	device_id: str,
	device_name: str,
	platform: str,
	ip_address: str = "",
	device_fingerprint: str = "",
) -> object:
	"""
	Return existing device record or create a new one.
	Does NOT apply tenant device limits — used for login flow where the
	device may already exist.
	"""
	existing_name = frappe.db.get_value(
		"KCSC AI Device", {"device_id": device_id, "user": user}, "name"
	)
	if existing_name:
		device = frappe.get_doc("KCSC AI Device", existing_name)
		# Refresh metadata in case device_name or IP changed
		if device.device_name != device_name or (ip_address and device.last_ip != ip_address):
			device.db_set({
				"device_name": device_name,
				"last_ip": ip_address or device.last_ip,
				"last_active": frappe.utils.now_datetime(),
			})
		return device

	device = frappe.get_doc({
		"doctype": "KCSC AI Device",
		"user": user,
		"device_id": device_id,
		"device_name": device_name,
		"platform": platform,
		"device_fingerprint": device_fingerprint,
		"last_ip": ip_address,
		"trusted": 0,
		"is_blocked": 0,
		"failed_attempts": 0,
	})
	device.insert(ignore_permissions=True)
	frappe.db.commit()
	return device


def register_device(
	user: str,
	device_id: str,
	device_name: str,
	platform: str,
	device_fingerprint: str = "",
	ip_address: str = "",
	tenant: str = None,
) -> object:
	"""
	Full device registration with tenant limit enforcement.
	Raises frappe.ValidationError if the tenant's device limit is reached.
	"""
	# Enforce tenant device limit before creating a new record
	if tenant:
		from kcsc_ai.kcsc_ai.services.tenant_policy import check_device_limit
		check_device_limit(tenant, user)

	return get_or_create_device(
		user=user,
		device_id=device_id,
		device_name=device_name,
		platform=platform,
		ip_address=ip_address,
		device_fingerprint=device_fingerprint,
	)


def validate_device_trust(user: str, device_id: str) -> bool:
	"""
	Return True if the device is known, not blocked, and trusted.
	Used by the risk engine to determine auth requirements.
	"""
	record = frappe.db.get_value(
		"KCSC AI Device",
		{"device_id": device_id, "user": user},
		["trusted", "is_blocked"],
		as_dict=True,
	)
	if not record:
		return False
	if record.is_blocked:
		frappe.throw("Device is blocked", frappe.AuthenticationError)
	return bool(record.trusted)
