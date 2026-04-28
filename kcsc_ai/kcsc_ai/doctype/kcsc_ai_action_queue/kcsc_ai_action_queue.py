import json

import frappe
from frappe.model.document import Document

# Terminal states — once reached, the record must not be mutated.
_TERMINAL_STATES = {"Executed", "Rejected", "Failed"}


class KCSCAIActionQueue(Document):
	def before_insert(self):
		if not self.created_at:
			self.created_at = frappe.utils.now_datetime()
		self._check_idempotency()

	def validate(self):
		self._validate_payload_json()
		if self.status == "Executed" and not self.executed_at:
			self.executed_at = frappe.utils.now_datetime()

	def before_save(self):
		# Prevent mutation of terminal records (immutability guarantee)
		if not self.is_new():
			prev_status = frappe.db.get_value("KCSC AI Action Queue", self.name, "status")
			if prev_status in _TERMINAL_STATES and self.status != prev_status:
				frappe.throw(
					f"Action Queue {self.name} is in terminal state '{prev_status}' and cannot be modified."
				)

	def on_update(self):
		# Lightweight audit log on every status change — no heavy logic.
		from kcsc_ai.kcsc_ai.services.activity_logger import log_activity

		log_activity(
			activity_type="Workflow Action",
			user=self.user,
			tenant=self.tenant,
			reference_doctype=self.reference_doctype or "",
			reference_name=self.reference_name or "",
			description=f"Action Queue {self.name} → {self.status}",
			risk_level=self.risk_level or "Low",
			status="Success" if self.status in ("Executed", "Approved") else "Failed" if self.status == "Failed" else "Warning",
			action_queue_ref=self.name,
		)

	# ------------------------------------------------------------------
	# Business helpers (called by workflow_service, not from hooks)
	# ------------------------------------------------------------------

	def approve(self, confirmed_by: str, method: str):
		self.db_set({"status": "Approved", "confirmed_by": confirmed_by, "confirmation_method": method})

	def reject(self, confirmed_by: str, reason: str = ""):
		self.db_set({"status": "Rejected", "confirmed_by": confirmed_by, "error_message": reason})

	def mark_executed(self):
		self.db_set({"status": "Executed", "executed_at": frappe.utils.now_datetime()})

	def mark_failed(self, error: str):
		self.db_set({
			"status": "Failed",
			"error_message": error[:500],
			"retry_count": (self.retry_count or 0) + 1,
		})

	# ------------------------------------------------------------------
	# Private helpers
	# ------------------------------------------------------------------

	def _check_idempotency(self):
		if not self.idempotency_key:
			return
		existing = frappe.db.get_value(
			"KCSC AI Action Queue",
			{"idempotency_key": self.idempotency_key, "status": ("not in", ["Failed"])},
			"name",
		)
		if existing:
			frappe.throw(
				f"Duplicate request: idempotency_key '{self.idempotency_key}' already maps to {existing}.",
				frappe.DuplicateEntryError,
			)

	def _validate_payload_json(self):
		if self.payload:
			try:
				json.loads(self.payload)
			except json.JSONDecodeError as exc:
				frappe.throw(f"Action payload must be valid JSON: {exc}")
