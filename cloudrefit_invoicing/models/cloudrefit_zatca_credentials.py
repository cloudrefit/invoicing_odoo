"""Centralized ZATCA Credential Resolution & Validation.

This module provides the single source of truth for:
- Which credentials exist (the full set of ZATCA config params)
- Which credentials are required per mode (live / sandbox)
- How to validate credentials and produce actionable error messages

All ZATCA-related models should inherit ``cloudrefit.zatca.credentials``
and use::

    creds = self._get_zatca_credentials(company)
    self._validate_zatca_credentials(creds, mode='live')   # returns list
    self._assert_zatca_credentials(creds, mode='live')      # raises or passes
"""

import logging

from odoo import models, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credential key registry — the single source of truth for all ZATCA params
# ---------------------------------------------------------------------------

#: Map of (full_ir_config_key) -> (default, type_coerce)
_ZATCA_PARAM_REGISTRY = {
    'cloudrefit_invoicing.business_id_live':        ('', str),
    'cloudrefit_invoicing.business_id_sandbox':     ('', str),
    'cloudrefit_invoicing.gateway_url_live':        ('https://api.invoicing.cloudrefit.com', str),
    'cloudrefit_invoicing.gateway_url_sandbox':     ('https://api.invoicing.cloudrefit.com', str),
    'cloudrefit_invoicing.mode':                    ('live', str),
    'cloudrefit_invoicing.sandbox_enabled':         ('False', lambda v: v == 'True'),
    'cloudrefit_invoicing.live_enabled':            ('False', lambda v: v == 'True'),
    'cloudrefit_invoicing.default_invoice_type':    ('auto', str),
    'cloudrefit_invoicing.api_key_live':            ('', str),
    'cloudrefit_invoicing.signing_secret_live':     ('', str),
    'cloudrefit_invoicing.unit_id_live':            ('', str),
    'cloudrefit_invoicing.api_key_sandbox':         ('', str),
    'cloudrefit_invoicing.signing_secret_sandbox':  ('', str),
    'cloudrefit_invoicing.unit_id_sandbox':         ('', str),
    'cloudrefit_invoicing.zatca_download_xml':      ('True', lambda v: str(v) != 'False'),
    'cloudrefit_invoicing.auto_generate_payment_links': ('False', lambda v: v == 'True'),
    'cloudrefit_invoicing.payment_gateway_id':          ('', str),
}

#: Human-readable labels for error messages (short_key -> label)
_ZATCA_PARAM_LABELS = {
    'business_id_live':       'Live Business ID',
    'business_id_sandbox':    'Sandbox Business ID',
    'gateway_url_live':       'Live Gateway URL',
    'gateway_url_sandbox':    'Sandbox Gateway URL',
    'api_key_live':           'Live API Key',
    'signing_secret_live':    'Live Signing Secret',
    'unit_id_live':           'Live Technical Unit ID',
    'api_key_sandbox':        'Sandbox API Key',
    'signing_secret_sandbox': 'Sandbox Signing Secret',
    'unit_id_sandbox':        'Sandbox Technical Unit ID',
    'sandbox_enabled':        'Sandbox Enabled',
    'live_enabled':           'Enable Live',
    'zatca_download_xml':     'Download Signed XMLs',
}

#: Which credential short-keys are required per execution mode
_REQUIRED_BY_MODE = {
    'live':    ['business_id_live', 'gateway_url_live', 'api_key_live', 'signing_secret_live', 'unit_id_live'],
    'sandbox': ['business_id_sandbox', 'gateway_url_sandbox', 'api_key_sandbox', 'signing_secret_sandbox', 'unit_id_sandbox'],
}


class CloudrefitZatcaCredentials(models.AbstractModel):
    _name = 'cloudrefit.zatca.credentials'
    _description = 'Centralized ZATCA Credential Resolution & Validation'

    # ------------------------------------------------------------------
    #  _get_zatca_credentials
    # ------------------------------------------------------------------
    @api.model
    def _get_zatca_credentials(self, company=None):
        """Return a dict of ALL ZATCA credentials for the given company.

        If *company* is ``None``, uses ``self.env.company`` (the current
        context company).  This works correctly when called on a
        TransientModel, a regular model, or a cron job — as long as the
        correct company context is active.

        Returns a dict with short keys (no ``cloudrefit_invoicing.`` prefix)::

            {
                'company_id': 1,
                'company_name': 'My Company',
                'business_id_live': 'biz_live_001',
                'business_id_sandbox': 'biz_sandbox_001',
                'gateway_url': 'https://...',
                'mode': 'live',
                'sandbox_enabled': True,
                'default_invoice_type': 'auto',
                'api_key_live': 'sk_...',
                'signing_secret_live': '...',
                'unit_id_live': 999,
                'api_key_sandbox': 'sk_...',
                'signing_secret_sandbox': '...',
                'unit_id_sandbox': 998,
            }
        """
        target_company = company or self.env.company
        # Build a temporary recordset in the target company's context
        # so _cr_get_param resolves per-company keys correctly
        resolver = self.with_company(target_company) if target_company else self

        creds = {
            'company_id': target_company.id if target_company else None,
            'company_name': target_company.name if target_company else 'Unknown',
        }

        ICP = self.env['ir.config_parameter'].sudo()

        for full_key, (default, coerce) in _ZATCA_PARAM_REGISTRY.items():
            short_key = full_key.replace('cloudrefit_invoicing.', '')
            raw_value = resolver._cr_get_param(full_key, default)

            # For enable fields (live_enabled, sandbox_enabled),
            # prefer the base key value written by Odoo's standard execute() → set_values
            # over any stale company-suffixed key left by _reset_mode_on_failure.
            if short_key in ('live_enabled', 'sandbox_enabled'):
                base_value = ICP.get_param(full_key)
                if base_value is not None:
                    raw_value = base_value
                    # Clean up any stale suffixed key so _cr_get_param
                    # falls through to the base key on future reads.
                    if target_company:
                        stale_key = f'{full_key}_{target_company.id}'
                        stale_val = ICP.get_param(stale_key)
                        if stale_val is not None and stale_val != '':
                            ICP.set_param(stale_key, '')
                            _logger.info(
                                "action=cleanup_stale_key key=%s old=%s",
                                stale_key, stale_val,
                            )

            creds[short_key] = coerce(raw_value)

        return creds

    # ------------------------------------------------------------------
    #  _validate_zatca_credentials
    # ------------------------------------------------------------------
    @api.model
    def _validate_zatca_credentials(self, credentials, mode='live'):
        """Validate that all required credentials for *mode* are present.

        Args:
            credentials: dict returned by :meth:`_get_zatca_credentials`.
            mode: ``'live'`` or ``'sandbox'``.

        Returns:
            list[str]: Human-readable names of missing fields.
                       Empty list means all required fields are present.
        """
        missing = []
        required_keys = _REQUIRED_BY_MODE.get(mode, [])

        for key in required_keys:
            value = credentials.get(key)
            if value in (None, '', False):
                missing.append(_ZATCA_PARAM_LABELS.get(key, key))

        # sandbox mode also requires sandbox_enabled to be True
        if mode == 'sandbox' and not credentials.get('sandbox_enabled'):
            missing.append('Sandbox Enabled (must be turned ON)')

        return missing

    # ------------------------------------------------------------------
    #  _assert_zatca_credentials
    # ------------------------------------------------------------------
    @api.model
    def _assert_zatca_credentials(self, credentials, mode='live'):
        """Validate credentials and raise a single ``UserError`` if any are
        missing.

        The error message names the company and lists ALL missing fields at
        once, so the user can fix everything in one trip to Settings.

        Args:
            credentials: dict from :meth:`_get_zatca_credentials`.
            mode: ``'live'`` or ``'sandbox'``.

        Returns:
            dict: The *credentials* dict (unchanged), for chaining.

        Raises:
            UserError: If any required credentials are missing.
        """
        missing = self._validate_zatca_credentials(credentials, mode=mode)
        if missing:
            company_name = credentials.get('company_name', 'Unknown')
            raise UserError(
                f'ZATCA {mode.title()} is not fully configured for company '
                f'"{company_name}".\n\n'
                f'Please go to Settings \u2192 CloudRefit ZATCA, select company '
                f'"{company_name}", and configure all required fields.'
            )
        return credentials
