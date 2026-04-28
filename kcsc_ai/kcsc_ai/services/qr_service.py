"""
QR Token Generation Service — Phase 2

Three QR modes (spec §3.1):

  Static QR   → device pairing, valid 10 minutes, one-time use
  Dynamic QR  → login, rotates every 60 seconds
  Action QR   → action confirmation, valid 2 minutes, one-time use

The server never renders the QR image — it returns the opaque token
string that the mobile app or desk JS renders into a QR code.
For desk-side rendering we also optionally return a base64 PNG.
"""

import base64
import io
import json

import frappe

_DYNAMIC_QR_TTL = 60	# seconds — login QR rotation window
_STATIC_QR_TTL = 600	# 10 minutes — device pairing window
_ACTION_QR_TTL = 120	# 2 minutes — action confirmation window


# ------------------------------------------------------------------
# Public generators
# ------------------------------------------------------------------

def generate_login_qr_payload(user: str) -> dict:
	"""
	Generate a rotating dynamic QR for login.
	Returns {qr_data, expires_in, qr_image_b64 (optional)}.
	"""
	from kcsc_ai.kcsc_ai.services.token_service import generate_qr_token

	raw_token, _ = generate_qr_token(
		user=user,
		action_context={"qr_type": "login"},
	)

	qr_data = _build_qr_payload("login", raw_token)
	return {
		"qr_data": qr_data,
		"expires_in": _DYNAMIC_QR_TTL,
		"qr_image_b64": _render_qr_b64(qr_data),
	}


def generate_action_qr_payload(user: str, action_queue_id: str) -> dict:
	"""
	Generate a one-time action-confirmation QR.
	Embeds the action_queue_id in the encrypted token context.
	"""
	from kcsc_ai.kcsc_ai.services.token_service import generate_qr_token

	raw_token, _ = generate_qr_token(
		user=user,
		action_context={"qr_type": "action", "action_queue_id": action_queue_id},
	)

	qr_data = _build_qr_payload("action", raw_token)
	return {
		"qr_data": qr_data,
		"expires_in": _ACTION_QR_TTL,
		"qr_image_b64": _render_qr_b64(qr_data),
	}


def generate_static_pairing_qr(user: str) -> dict:
	"""
	Generate a long-lived device-pairing QR.
	Used once during initial device registration.
	"""
	from kcsc_ai.kcsc_ai.services.token_service import generate_qr_token

	# Static QR uses a longer TTL baked into the QR token via crypto.py
	# We pass the override via action_context; token_service uses default TTL.
	# For static pairing, extend via separate Redis key.
	raw_token, token_hash = generate_qr_token(
		user=user,
		action_context={"qr_type": "static", "purpose": "device_pairing"},
	)

	# Extend TTL for static QR (override the default 2-min QR TTL)
	from kcsc_ai.kcsc_ai.utils.redis_helper import get_token_cache, set_token_cache

	cache_key = f"kcsc_token:qr:{token_hash}"
	cached = get_token_cache(cache_key)
	if cached:
		set_token_cache(cache_key, cached, _STATIC_QR_TTL)

	qr_data = _build_qr_payload("static", raw_token)
	return {
		"qr_data": qr_data,
		"expires_in": _STATIC_QR_TTL,
		"qr_image_b64": _render_qr_b64(qr_data),
	}


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _build_qr_payload(qr_type: str, raw_token: str) -> str:
	"""
	Build the compact JSON string that gets encoded into the QR.
	Short keys keep the QR density low for reliable scanning.
	"""
	site = frappe.local.site
	payload = {"t": qr_type, "k": raw_token, "s": site}
	return json.dumps(payload, separators=(",", ":"))


def _render_qr_b64(data: str) -> str:
	"""
	Render the QR data string into a base64 PNG for optional desk display.
	Returns empty string if qrcode library is unavailable.
	"""
	try:
		import qrcode
		from qrcode.image.pure import PyPNGImage

		qr = qrcode.QRCode(
			version=None,
			error_correction=qrcode.constants.ERROR_CORRECT_M,
			box_size=6,
			border=4,
		)
		qr.add_data(data)
		qr.make(fit=True)

		img = qr.make_image(fill_color="black", back_color="white")
		buf = io.BytesIO()
		img.save(buf, format="PNG")
		return base64.b64encode(buf.getvalue()).decode("utf-8")
	except ImportError:
		return ""
