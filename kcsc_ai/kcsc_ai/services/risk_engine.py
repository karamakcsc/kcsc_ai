"""
Adaptive risk scoring engine.

Scoring is additive. Each factor contributes a weighted score.
The final band (Low / Medium / High) drives which auth challenge is required.

Risk band → required auth:
  Low     (0–24)   → QR only
  Medium  (25–49)  → QR + Device verification
  High    (50+)    → QR + OTP
"""

from dataclasses import dataclass, field
from typing import Optional

import frappe

_WEIGHTS = {
	"new_device": 30,
	"untrusted_device": 15,
	"blocked_device": 60,
	"high_value": 40,		# transaction_value > 50,000
	"medium_value": 20,		# transaction_value > 10,000
	"bulk_action": 25,
	"after_hours": 15,		# outside 06:00–22:00
	"ip_change": 20,
	"sensitive_doctype": 30,
}

_SENSITIVE_DOCTYPES = {
	"Purchase Order", "Sales Order", "Payment Entry",
	"Journal Entry", "Employee", "Salary Slip",
}

_AUTH_MAP = {
	"Low": "QR",
	"Medium": "QR + Device",
	"High": "QR + OTP",
}


@dataclass
class RiskResult:
	score: int
	risk_level: str
	required_auth: str
	factors: list[str] = field(default_factory=list)


def calculate_risk(
	user: str,
	device_id: str,
	action_type: str,
	reference_doctype: str = None,
	payload: dict = None,
	current_ip: str = None,
) -> RiskResult:
	"""
	Evaluate risk for a proposed action.
	Returns a RiskResult with score, band, required auth, and factor list.
	"""
	score = 0
	factors = []

	# --- Device checks ---
	device = frappe.db.get_value(
		"KCSC AI Device",
		{"device_id": device_id, "user": user},
		["trusted", "failed_attempts", "is_blocked", "last_ip"],
		as_dict=True,
	)

	if not device:
		score += _WEIGHTS["new_device"]
		factors.append("new_device")
	elif device.is_blocked:
		score += _WEIGHTS["blocked_device"]
		factors.append("blocked_device")
	elif not device.trusted:
		score += _WEIGHTS["untrusted_device"]
		factors.append("untrusted_device")

	# IP change relative to last known IP
	if device and current_ip and device.last_ip and device.last_ip != current_ip:
		score += _WEIGHTS["ip_change"]
		factors.append("ip_change")

	# --- Payload / value checks ---
	if payload:
		try:
			value = float(payload.get("transaction_value", 0) or 0)
			if value > 50_000:
				score += _WEIGHTS["high_value"]
				factors.append("high_value")
			elif value > 10_000:
				score += _WEIGHTS["medium_value"]
				factors.append("medium_value")
		except (TypeError, ValueError):
			pass

	# --- Action-level checks ---
	if action_type == "bulk_approval":
		score += _WEIGHTS["bulk_action"]
		factors.append("bulk_action")

	# --- DocType sensitivity ---
	if reference_doctype and reference_doctype in _SENSITIVE_DOCTYPES:
		score += _WEIGHTS["sensitive_doctype"]
		factors.append("sensitive_doctype")

	# --- Temporal check ---
	hour = frappe.utils.now_datetime().hour
	if hour < 6 or hour > 22:
		score += _WEIGHTS["after_hours"]
		factors.append("after_hours")

	# --- Band assignment ---
	if score >= 50:
		band = "High"
	elif score >= 25:
		band = "Medium"
	else:
		band = "Low"

	return RiskResult(
		score=score,
		risk_level=band,
		required_auth=_AUTH_MAP[band],
		factors=factors,
	)
