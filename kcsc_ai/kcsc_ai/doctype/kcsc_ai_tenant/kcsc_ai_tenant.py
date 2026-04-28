import frappe
from frappe.model.document import Document


class KCSCAITenant(Document):
	def before_insert(self):
		if not self.created_at:
			self.created_at = frappe.utils.now_datetime()

	def validate(self):
		if (self.max_users or 0) < 1:
			frappe.throw("Max Users must be at least 1")
		if (self.max_devices or 0) < 1:
			frappe.throw("Max Devices must be at least 1")
		if self.ai_mode == "Remote" and not self.ai_endpoint:
			frappe.throw("AI Endpoint is required when AI Mode is set to Remote")

	# ------------------------------------------------------------------
	# Policy helpers — called by Phase 2 policy engine
	# ------------------------------------------------------------------

	def get_limits(self) -> dict:
		return {
			"max_users": self.max_users,
			"max_devices": self.max_devices,
			"max_ai_requests": self.max_ai_requests,
		}

	def is_active(self) -> bool:
		return self.status == "Active"

	def enforce_user_limit(self):
		count = frappe.db.count("User", {"enabled": 1})
		if count >= (self.max_users or 0):
			frappe.throw(
				f"Tenant '{self.tenant_name}' has reached the user limit ({self.max_users}). "
				"Upgrade your plan or contact support."
			)

	def enforce_device_limit(self, user: str):
		count = frappe.db.count("KCSC AI Device", {"user": user, "is_blocked": 0})
		if count >= (self.max_devices or 0):
			frappe.throw(
				f"Device limit reached for user '{user}' under tenant '{self.tenant_name}'."
			)
