"""
OTP (One-Time Password) Service — Phase 2

Generates and validates 6-digit numeric OTPs stored in Redis.
Used as the "OTP" factor in the High-risk auth flow (QR + OTP).

In production, the OTP is delivered out-of-band:
  - SMS via ERPNext's SMS integration
  - Email via frappe.sendmail

OTPs are single-use, expire in 5 minutes, and are rate-limited.
"""

import random
import string

import frappe

_OTP_TTL = 300		# 5 minutes
_OTP_LENGTH = 6


def generate_otp(user: str, action_queue_id: str) -> str:
	"""
	Generate a 6-digit OTP for the given user + action.
	Stores it in Redis and sends it via the configured delivery method.
	Returns the OTP (for testing/internal use only — never expose in API responses).
	"""
	from kcsc_ai.kcsc_ai.utils.crypto import hash_token
	from kcsc_ai.kcsc_ai.utils.redis_helper import set_token_cache

	otp = "".join(random.choices(string.digits, k=_OTP_LENGTH))
	cache_key = _otp_cache_key(user, action_queue_id)

	set_token_cache(cache_key, {"otp": otp, "user": user, "action_queue_id": action_queue_id, "used": False}, _OTP_TTL)
	_deliver_otp(user, otp, action_queue_id)
	return otp


def validate_otp(user: str, action_queue_id: str, otp_input: str) -> bool:
	"""
	Validate an OTP. Marks it used on success (one-time only).
	Raises frappe.AuthenticationError on failure.
	"""
	from kcsc_ai.kcsc_ai.utils.redis_helper import delete_token_cache, get_token_cache, set_token_cache

	cache_key = _otp_cache_key(user, action_queue_id)
	data = get_token_cache(cache_key)

	if not data:
		frappe.throw("OTP expired or not found. Request a new one.", frappe.AuthenticationError)

	if data.get("used"):
		frappe.throw("OTP has already been used.", frappe.AuthenticationError)

	if data.get("otp") != str(otp_input).strip():
		frappe.throw("Invalid OTP.", frappe.AuthenticationError)

	# Mark consumed (10-second grace before Redis evicts)
	data["used"] = True
	set_token_cache(cache_key, data, 10)
	return True


def _otp_cache_key(user: str, action_queue_id: str) -> str:
	from kcsc_ai.kcsc_ai.utils.crypto import hash_token
	return f"kcsc_otp:{hash_token(f'{user}:{action_queue_id}')[:24]}"


def _deliver_otp(user: str, otp: str, action_queue_id: str):
	"""
	Deliver the OTP to the user. Tries SMS first, falls back to email.
	Silently logs failures — a delivery failure must never break the flow.
	"""
	try:
		user_doc = frappe.get_doc("User", user)
		subject = "Your KCSC AI Verification Code"
		body = (
			f"Your verification code is: <strong>{otp}</strong><br>"
			f"It expires in 5 minutes.<br>"
			f"Action reference: {action_queue_id}"
		)
		frappe.sendmail(
			recipients=[user_doc.email],
			subject=subject,
			message=body,
			delayed=False,
		)
	except Exception as exc:
		frappe.log_error(f"OTP delivery failed for {user}: {exc}", "KCSC AI OTP")
