"""
Cryptographic primitives for the KCSC AI token system.

Design decisions:
- Tokens are NEVER stored raw; callers always get back (raw_token, hash).
- Fernet key is loaded from site_config.json (kcsc_ai_encryption_key).
  Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  Then add to site_config: bench set-config kcsc_ai_encryption_key <value>
"""

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken


def hash_token(raw_token: str) -> str:
	"""Return the SHA-256 hex digest of a raw token string (64 chars)."""
	return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_secure_token(byte_length: int = 32) -> str:
	"""
	Generate a cryptographically secure URL-safe token string.
	byte_length controls entropy: 32 bytes = 256-bit security.
	"""
	return secrets.token_urlsafe(byte_length)


def get_fernet() -> Fernet:
	"""
	Return a Fernet instance using the site-level encryption key.
	Raises a clear error if the key is missing — fail loud in production.
	"""
	import frappe

	key = frappe.conf.get("kcsc_ai_encryption_key")
	if not key:
		frappe.throw(
			"kcsc_ai_encryption_key is not configured in site_config.json. "
			"Run: bench set-config kcsc_ai_encryption_key $(python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\")"
		)
	if isinstance(key, str):
		key = key.encode("utf-8")
	return Fernet(key)


def encrypt_payload(data: str) -> str:
	"""Encrypt a plaintext string with Fernet (AES-128-CBC + HMAC-SHA256)."""
	return get_fernet().encrypt(data.encode("utf-8")).decode("utf-8")


def decrypt_payload(encrypted: str) -> str:
	"""
	Decrypt a Fernet-encrypted string.
	Raises frappe.AuthenticationError on tamper or expiry.
	"""
	import frappe

	try:
		return get_fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
	except InvalidToken:
		frappe.throw("Encrypted payload is invalid or has been tampered with.", frappe.AuthenticationError)
