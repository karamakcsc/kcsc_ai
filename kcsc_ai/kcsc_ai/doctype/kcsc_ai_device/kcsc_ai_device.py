import frappe
from frappe.model.document import Document

_MAX_FAILED_ATTEMPTS = 5


class KCSCAIDevice(Document):
	def before_insert(self):
		if not self.device_id:
			frappe.throw("Device ID is required")
		self._enforce_device_limit()

	def validate(self):
		# Auto-block after too many failed attempts
		if self.failed_attempts >= _MAX_FAILED_ATTEMPTS and not self.is_blocked:
			self.is_blocked = 1
			frappe.log_error(
				f"Device {self.device_id} auto-blocked: {self.failed_attempts} failed attempts",
				"KCSC AI Device Security",
			)

	def update_last_active(self, ip_address: str = None):
		"""Call this on every successful authenticated request from this device."""
		updates = {"last_active": frappe.utils.now_datetime()}
		if ip_address:
			updates["last_ip"] = ip_address
		self.db_set(updates, update_modified=False)

	def increment_failed_attempts(self):
		new_count = (self.failed_attempts or 0) + 1
		self.db_set("failed_attempts", new_count)
		if new_count >= _MAX_FAILED_ATTEMPTS:
			self.db_set("is_blocked", 1)

	def reset_failed_attempts(self):
		self.db_set({"failed_attempts": 0, "is_blocked": 0})

	def _enforce_device_limit(self):
		# Delegates to tenant policy — Phase 2 will wire this to KCSC AI Tenant.
		# Placeholder so the hook point exists and is testable from day one.
		pass
