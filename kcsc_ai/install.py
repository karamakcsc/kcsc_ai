"""
Post-install bootstrap for kcsc_ai.

Creates a default Tenant record tied to the current ERPNext site so
the system is immediately usable after `bench install-app kcsc_ai`.
"""

import frappe


def after_install():
	_create_default_tenant()
	_warn_missing_config()


def _create_default_tenant():
	site_name = frappe.local.site
	if frappe.db.exists("KCSC AI Tenant", site_name):
		return

	tenant = frappe.get_doc(
		{
			"doctype": "KCSC AI Tenant",
			"tenant_name": site_name,
			"status": "Active",
			"plan": "Basic",
			"isolation_level": "Site",
			"site_url": f"https://{site_name}",
			"max_users": 50,
			"max_devices": 100,
			"max_ai_requests": 1000,
			"ai_mode": "Local",
		}
	)
	tenant.insert(ignore_permissions=True)
	frappe.db.commit()
	print(f"[kcsc_ai] Default tenant '{site_name}' created.")


def _warn_missing_config():
	missing = []
	for key in ("kcsc_ai_encryption_key",):
		if not frappe.conf.get(key):
			missing.append(key)

	if missing:
		print(
			"\n[kcsc_ai] WARNING: The following site_config.json keys are not set:\n"
			+ "\n".join(f"  - {k}" for k in missing)
			+ "\n\nGenerate the encryption key with:"
			+ '\n  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
			+ "\nThen: bench set-config kcsc_ai_encryption_key <value>\n"
		)
