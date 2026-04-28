import frappe
from frappe.model.document import Document


class KCSCAIActivityLog(Document):
	# Activity logs are append-only — they must never be edited after creation.

	def before_insert(self):
		if not self.created_at:
			self.created_at = frappe.utils.now_datetime()

	def validate(self):
		if not self.is_new():
			frappe.throw(
				"Activity logs are immutable. Modification after creation is not permitted."
			)
