"""
Token lifecycle service.

Hybrid storage strategy:
  Access Token  → Redis (fast path) + DB (audit trail)
  Refresh Token → DB only (long-lived, low-frequency lookup)
  QR Token      → Redis only (one-time use, TTL ≤ 2 min) + DB for audit

Callers receive (raw_token, token_hash).
raw_token is returned ONCE and never persisted anywhere.
token_hash is what gets stored and compared.
"""

import json

import frappe

from kcsc_ai.kcsc_ai.utils.crypto import encrypt_payload, generate_secure_token, hash_token
from kcsc_ai.kcsc_ai.utils.redis_helper import (
	delete_token_cache,
	get_token_cache,
	set_token_cache,
)

_ACCESS_TOKEN_TTL = 3600		# 1 hour
_REFRESH_TOKEN_TTL = 86400 * 30	# 30 days
_QR_TOKEN_TTL = 120				# 2 minutes — short window for QR scan


# ------------------------------------------------------------------
# Generators
# ------------------------------------------------------------------

def generate_access_token(user: str, device_id: str, ip_address: str = None) -> tuple[str, str]:
	"""
	Issue a short-lived access token.
	Returns (raw_token, token_hash). raw_token is shown to the caller once.
	"""
	raw = generate_secure_token(32)
	h = hash_token(raw)
	expires_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=_ACCESS_TOKEN_TTL)

	# Redis — primary fast-path store
	set_token_cache(
		f"kcsc_token:access:{h}",
		{"user": user, "device_id": device_id, "expires_at": str(expires_at), "revoked": False},
		_ACCESS_TOKEN_TTL,
	)

	# DB — audit trail + fallback when Redis is cold
	_insert_token_record(h, "Access Token", user, device_id, expires_at, ip_address)
	return raw, h


def generate_refresh_token(user: str, device_id: str, ip_address: str = None) -> tuple[str, str]:
	"""Issue a long-lived refresh token. Stored in DB only (never in Redis)."""
	raw = generate_secure_token(64)
	h = hash_token(raw)
	expires_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=_REFRESH_TOKEN_TTL)
	_insert_token_record(h, "Refresh Token", user, device_id, expires_at, ip_address)
	return raw, h


def generate_qr_token(
	user: str,
	action_context: dict = None,
	device_id: str = None,
) -> tuple[str, str]:
	"""
	Issue a one-time QR token.
	Payload is Fernet-encrypted so it can be embedded in the QR image safely.
	"""
	import secrets as _secrets

	raw = generate_secure_token(32)
	h = hash_token(raw)
	expires_at = frappe.utils.add_to_date(frappe.utils.now_datetime(), seconds=_QR_TOKEN_TTL)

	encrypted_ctx = encrypt_payload(
		json.dumps({"user": user, "action_context": action_context or {}, "nonce": _secrets.token_hex(8)})
	)

	set_token_cache(
		f"kcsc_token:qr:{h}",
		{
			"user": user,
			"device_id": device_id,
			"encrypted_context": encrypted_ctx,
			"used": False,
			"expires_at": str(expires_at),
		},
		_QR_TOKEN_TTL,
	)

	# DB record for audit only
	_insert_token_record(h, "QR Token", user, device_id or "", expires_at, ip_address=None)
	return raw, h


# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------

def validate_access_token(raw_token: str) -> str:
	"""
	Validate an access token and return the associated user.
	Redis-first; falls back to DB if the cache is cold (e.g. after Redis restart).
	Raises frappe.AuthenticationError on any failure.
	"""
	h = hash_token(raw_token)

	cached = get_token_cache(f"kcsc_token:access:{h}")
	if cached:
		if cached.get("revoked"):
			_auth_error("Token has been revoked")
		return cached["user"]

	# Redis miss — check DB
	record = _get_token_record(h, "Access Token")
	if not record:
		_auth_error("Access token not found")
	_assert_not_expired(record.expires_at)
	return record.user


def validate_refresh_token(raw_token: str) -> str:
	"""Validate a refresh token and return the associated user."""
	h = hash_token(raw_token)
	record = _get_token_record(h, "Refresh Token")
	if not record:
		_auth_error("Refresh token not found")
	_assert_not_expired(record.expires_at)
	return record.user


def consume_qr_token(raw_token: str) -> dict:
	"""
	Validate and consume a QR token (one-time use).
	Returns the decrypted action context dict.
	Raises frappe.AuthenticationError if invalid, expired, or already used.
	"""
	from kcsc_ai.kcsc_ai.utils.crypto import decrypt_payload

	h = hash_token(raw_token)
	cache_key = f"kcsc_token:qr:{h}"
	cached = get_token_cache(cache_key)

	if not cached:
		_auth_error("QR token expired or not found")
	if cached.get("used"):
		_auth_error("QR token has already been used")

	# Mark consumed atomically in Redis (10-second grace window before eviction)
	cached["used"] = True
	set_token_cache(cache_key, cached, 10)

	# Also mark revoked in DB
	frappe.db.set_value(
		"KCSC AI Token",
		{"token_hash": h, "token_type": "QR Token"},
		"revoked",
		1,
	)
	frappe.db.commit()

	context = json.loads(decrypt_payload(cached["encrypted_context"]))
	return context


# ------------------------------------------------------------------
# Revocation
# ------------------------------------------------------------------

def revoke_token(token_hash: str):
	"""Revoke a single token by its hash (works for all types)."""
	for prefix in ("access", "refresh", "qr"):
		delete_token_cache(f"kcsc_token:{prefix}:{token_hash}")
	frappe.db.set_value("KCSC AI Token", {"token_hash": token_hash}, "revoked", 1)
	frappe.db.commit()


def revoke_all_user_tokens(user: str, token_type: str = None):
	"""Revoke every active token for a user (e.g. on security event or logout)."""
	filters = {"user": user, "revoked": 0}
	if token_type:
		filters["token_type"] = token_type

	hashes = frappe.db.get_all("KCSC AI Token", filters=filters, pluck="token_hash")
	for h in hashes:
		revoke_token(h)


# ------------------------------------------------------------------
# Background job (called by scheduler)
# ------------------------------------------------------------------

def cleanup_expired_tokens():
	"""Remove expired AND revoked tokens from DB. Safe to run hourly."""
	frappe.db.delete(
		"KCSC AI Token",
		{"expires_at": ("<", frappe.utils.now_datetime()), "revoked": 1},
	)
	frappe.db.commit()


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _insert_token_record(token_hash, token_type, user, device_id, expires_at, ip_address):
	doc = frappe.get_doc(
		{
			"doctype": "KCSC AI Token",
			"token_hash": token_hash,
			"token_type": token_type,
			"user": user,
			"device_id": device_id or "",
			"expires_at": expires_at,
			"ip_address": ip_address or "",
			"revoked": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def _get_token_record(token_hash: str, token_type: str):
	return frappe.db.get_value(
		"KCSC AI Token",
		{"token_hash": token_hash, "token_type": token_type, "revoked": 0},
		["name", "user", "device_id", "expires_at"],
		as_dict=True,
	)


def _assert_not_expired(expires_at):
	if frappe.utils.now_datetime() > frappe.utils.get_datetime(expires_at):
		_auth_error("Token has expired")


def _auth_error(message: str):
	frappe.throw(message, frappe.AuthenticationError)
