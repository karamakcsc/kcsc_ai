import frappe
from frappe.model.document import Document


class KCSCAIToken(Document):
	# Raw tokens are NEVER stored here — only SHA-256 hashes.
	# All read/write goes through services/token_service.py.

	def before_insert(self):
		if not self.token_hash or len(self.token_hash) != 64:
			frappe.throw(
				"token_hash must be a valid 64-character SHA-256 hex digest. "
				"Never pass raw tokens to this doctype."
			)
		if not self.created_at:
			self.created_at = frappe.utils.now_datetime()

	def validate(self):
		if self.expires_at and frappe.utils.get_datetime(self.expires_at) < frappe.utils.now_datetime():
			# Expired tokens may still be inserted for audit trail completeness;
			# validation happens in token_service.validate_token(), not here.
			pass

	def on_update(self):
		# When a token is revoked, purge it from the Redis cache immediately.
		if self.revoked:
			from kcsc_ai.kcsc_ai.utils.redis_helper import delete_token_cache

			for prefix in ("access", "refresh", "qr"):
				delete_token_cache(f"kcsc_token:{prefix}:{self.token_hash}")
