import requests
import logging
from datetime import date
from odoo import models, fields, api

_logger = logging.getLogger(__name__)

RELEASE_JSON_URL = "https://raw.githubusercontent.com/cloudrefit/invoicing_odoo/19.0/RELEASE.json"
SEVERITY_ORDER = ["info", "minor", "major", "critical", "urgent", "blocked"]

class CloudRefitVersionChecker(models.TransientModel):
    _name = 'cloudrefit.version.checker'
    _description = 'CloudRefit Version Checker'

    @api.model
    def check_for_updates(self):
        try:
            response = requests.get(RELEASE_JSON_URL, timeout=10)
            if response.status_code != 200:
                _logger.warning("CloudRefit Version Checker: Failed to fetch RELEASE.json (HTTP %s)", response.status_code)
                return
            data = response.json()
        except Exception as e:
            _logger.warning("CloudRefit Version Checker: Network error fetching RELEASE.json - %s", str(e))
            return  # Silently fail — never disrupt Odoo on network issues

        latest_version = data.get("version")
        installed_version = self._get_installed_version()

        # If RELEASE.json is stale or same version — silently treat as up-to-date
        if not latest_version or self._version_tuple(installed_version) >= self._version_tuple(latest_version):
            self._store("cloudrefit_invoicing.update_severity", "none")
            return

        # Determine effective severity with escalation
        severity = data.get("severity", "info")
        severity = self._apply_deadline_escalation(severity, data.get("deadline"))
        severity = self._apply_compat_escalation(severity, installed_version, data.get("min_compatible_version"))

        # Store all update metadata
        self._store("cloudrefit_invoicing.latest_version", latest_version)
        self._store("cloudrefit_invoicing.update_severity", severity)
        self._store("cloudrefit_invoicing.update_title", data.get("title", ""))
        self._store("cloudrefit_invoicing.update_message", data.get("message", ""))
        self._store("cloudrefit_invoicing.changelog_url", data.get("changelog_url", ""))
        self._store("cloudrefit_invoicing.update_checked_at", fields.Datetime.now())

    @api.model
    def _get_installed_version(self):
        module = self.env['ir.module.module'].search([('name', '=', 'cloudrefit_invoicing')], limit=1)
        return module.installed_version or module.latest_version or '0.0.0.0'

    @api.model
    def _store(self, key, value):
        self.env['ir.config_parameter'].sudo().set_param(key, value)

    @api.model
    def _version_tuple(self, v):
        """Compare versions as integer tuples, not strings."""
        if not v:
            return (0,)
        return tuple(int(x) for x in str(v).split('.') if x.isdigit())

    @api.model
    def _apply_deadline_escalation(self, severity, deadline_str):
        if not deadline_str:
            return severity
        try:
            deadline = date.fromisoformat(deadline_str)
            if date.today() > deadline:
                try:
                    idx = SEVERITY_ORDER.index(severity)
                    return SEVERITY_ORDER[min(idx + 1, len(SEVERITY_ORDER) - 1)]
                except ValueError:
                    pass
        except Exception:
            pass
        return severity

    @api.model
    def _apply_compat_escalation(self, severity, installed, min_compat):
        if not min_compat:
            return severity
        if self._version_tuple(installed) < self._version_tuple(min_compat):
            return "blocked"
        return severity
