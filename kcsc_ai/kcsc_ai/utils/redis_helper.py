"""
Thin wrapper around Frappe's RedisWrapper (frappe.cache()).

All keys are automatically namespaced to the current site by Frappe's
RedisWrapper, so multi-tenant isolation is handled transparently.
"""

from typing import Any


def _cache():
	import frappe
	return frappe.cache()


def set_value(key: str, value: Any, ttl_seconds: int):
	"""Store a value with a TTL (seconds). Value is serialized via frappe.as_json."""
	_cache().set_value(key, value, expires_in_sec=ttl_seconds)


def get_value(key: str) -> Any:
	"""Retrieve a cached value. Returns None on miss."""
	return _cache().get_value(key)


def delete_key(key: str):
	"""Remove a key from the cache."""
	_cache().delete_key(key)


# ------------------------------------------------------------------
# Token-specific helpers (semantic wrappers)
# ------------------------------------------------------------------

def set_token_cache(key: str, data: dict, ttl_seconds: int):
	set_value(key, data, ttl_seconds)


def get_token_cache(key: str) -> dict | None:
	return get_value(key)


def delete_token_cache(key: str):
	delete_key(key)


# ------------------------------------------------------------------
# Rate limiting
# ------------------------------------------------------------------

def check_rate_limit(identifier: str, action: str, max_attempts: int, window_seconds: int) -> bool:
	"""
	Sliding-window rate limiter.
	Returns True if the request is allowed, False if the limit is exceeded.
	"""
	key = f"kcsc_ratelimit:{action}:{identifier}"
	current = get_value(key)

	if current is None:
		set_value(key, 1, window_seconds)
		return True

	count = int(current)
	if count >= max_attempts:
		return False

	# Increment without resetting the TTL (approximates a fixed window).
	set_value(key, count + 1, window_seconds)
	return True
