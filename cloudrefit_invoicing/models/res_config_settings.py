from odoo import models, fields, api
from odoo.exceptions import AccessError
import hmac
import hashlib
import datetime
import json
import base64
import requests
import logging

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = ['res.config.settings', 'cloudrefit.notification.helper', 'cloudrefit.settings.helper', 'cloudrefit.zatca.credentials']

    # === Live Gateway Connection ===
    cloudrefit_gateway_url_live = fields.Char(
        string='Live Gateway URL',
        config_parameter='cloudrefit_invoicing.gateway_url_live',
        company_dependent=True,
        help='Base URL of the CloudRefit Gateway for live/production operations',
    )
    cloudrefit_api_key_live = fields.Char(
        string='Live API Key',
        config_parameter='cloudrefit_invoicing.api_key_live',
        company_dependent=True,
        help='Live API key from your CloudRefit dashboard',
    )
    cloudrefit_signing_secret_live = fields.Char(
        string='Live Signing Secret',
        config_parameter='cloudrefit_invoicing.signing_secret_live',
        company_dependent=True,
        help='Live signing secret from your CloudRefit dashboard',
    )
    def _get_live_units(self):
        units_json = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.technical_units_live', '[]')
        try:
            units = json.loads(units_json)
            # Both LIVE and SANDBOX modes might return all units, but we filter for LIVE
            return [(str(u['id']), f"[{u['id']}] {u['label']}") for u in units if str(u.get('mode', '')).upper() == 'LIVE']
        except Exception:
            return []

    cloudrefit_unit_id_live = fields.Selection(
        selection=_get_live_units,
        string='Live Technical Unit',
        config_parameter='cloudrefit_invoicing.unit_id_live',
        company_dependent=True,
        help='Select the Live unit/device from your CloudRefit dashboard. Test Connection to refresh the list.',
    )

    # === Sandbox Gateway Connection ===
    cloudrefit_gateway_url_sandbox = fields.Char(
        string='Sandbox Gateway URL',
        config_parameter='cloudrefit_invoicing.gateway_url_sandbox',
        company_dependent=True,
        help='Base URL of the CloudRefit Gateway for sandbox/testing operations',
    )
    cloudrefit_api_key_sandbox = fields.Char(
        string='Sandbox API Key',
        config_parameter='cloudrefit_invoicing.api_key_sandbox',
        company_dependent=True,
        help='Sandbox API key from your CloudRefit dashboard. Used only for sandbox testing wizard.',
    )
    cloudrefit_signing_secret_sandbox = fields.Char(
        string='Sandbox Signing Secret',
        config_parameter='cloudrefit_invoicing.signing_secret_sandbox',
        company_dependent=True,
        help='Sandbox signing secret from your CloudRefit dashboard. Used only for sandbox testing wizard.',
    )
    def _get_sandbox_units(self):
        units_json = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.technical_units_sandbox', '[]')
        try:
            units = json.loads(units_json)
            # Filter for SANDBOX
            return [(str(u['id']), f"[{u['id']}] {u['label']}") for u in units if str(u.get('mode', '')).upper() == 'SANDBOX']
        except Exception:
            return []

    cloudrefit_unit_id_sandbox = fields.Selection(
        selection=_get_sandbox_units,
        string='Sandbox Technical Unit',
        config_parameter='cloudrefit_invoicing.unit_id_sandbox',
        company_dependent=True,
        help='Select the Sandbox unit/device from your CloudRefit dashboard. Test Connection to refresh the list.',
    )

    # === Live Business ID (auto-populated from live gateway ping) ===
    cloudrefit_business_id_live = fields.Char(
        string='Live Business ID',
        config_parameter='cloudrefit_invoicing.business_id_live',
        company_dependent=True,
        readonly=True,
        help='Auto-populated from gateway after a successful Live connection test.',
    )
    cloudrefit_business_name_live = fields.Char(
        string='Live Business Name',
        config_parameter='cloudrefit_invoicing.business_name_live',
        company_dependent=True,
        readonly=True,
    )

    # === Sandbox Business ID (auto-populated from sandbox gateway ping) ===
    cloudrefit_business_id_sandbox = fields.Char(
        string='Sandbox Business ID',
        config_parameter='cloudrefit_invoicing.business_id_sandbox',
        company_dependent=True,
        readonly=True,
        help='Auto-populated from gateway after a successful Sandbox connection test.',
    )
    cloudrefit_business_name_sandbox = fields.Char(
        string='Sandbox Business Name',
        config_parameter='cloudrefit_invoicing.business_name_sandbox',
        company_dependent=True,
        readonly=True,
    )

    # === Toggle Enable Fields (readonly until credentials + connection complete) ===
    can_enable_live = fields.Boolean(
        compute='_compute_can_enable_live',
        help='Technical: whether the Enable Live toggle should be editable',
    )
    can_enable_sandbox = fields.Boolean(
        compute='_compute_can_enable_sandbox',
        help='Technical: whether the Enable Sandbox toggle should be editable',
    )

    # === Default Invoice Behaviour ===
    cloudrefit_default_invoice_type = fields.Selection([
        ('auto', 'Auto-detect (B2B if customer has VAT)'),
        ('simplified', 'Always Simplified (B2C)'),
        ('standard', 'Always Standard (B2B)'),
    ], string='Default Invoice Type',
       config_parameter='cloudrefit_invoicing.default_invoice_type',
       company_dependent=True,
       default='auto',
       help='Default ZATCA invoice type for new invoices',
    )
    is_cloudrefit_live_enabled = fields.Boolean(
        string="Enable Live",
        default=False,
        help="Show the Live ZATCA tab and allow pushing invoices to live ZATCA",
    )
    cloudrefit_zatca_download_xml = fields.Boolean(
        string="Download Signed XMLs",
        default=True,
        help="Download Signed XMLs (May incur additional fees — turn off if CloudRefit hosts your data)",
    )
    is_cloudrefit_sandbox_enabled = fields.Boolean(
        string='Enable Sandbox',
        default=False,
        help="Enable the 'Test in Sandbox' button on invoices.",
    )
    cloudrefit_show_sandbox_settings_ui = fields.Boolean(
        string='Show Sandbox Settings',
        default=False,
        help="Toggle visibility of the sandbox configuration block to declutter the settings page."
    )
    cloudrefit_mode = fields.Selection(
        [('none', 'Not Configured'), ('sandbox', 'Sandbox'), ('live', 'Live')],
        string='ZATCA Environment Mode',
        config_parameter='cloudrefit_invoicing.mode',
        company_dependent=True,
        default='live',
        help='Operational mode for ZATCA integration',
    )
    
    # === Payment Integration Settings ===
    cloudrefit_auto_generate_payment_links = fields.Boolean(
        string='Auto-Generate Payment Links',
        config_parameter='cloudrefit_invoicing.auto_generate_payment_links',
        company_dependent=True,
        default=False,
        help='Automatically generate a payment link when a LIVE invoice is sent or cleared'
    )
    cloudrefit_use_default_payment_gateway = fields.Boolean(
        string='Use Business Default Gateway',
        config_parameter='cloudrefit_invoicing.use_default_payment_gateway',
        company_dependent=True,
        default=True,
        help='Use the default payment integration configured in the CloudRefit dashboard.'
    )
    def _get_payment_gateways(self):
        gateways_json = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.payment_gateways', '[]')
        try:
            gateways = json.loads(gateways_json)
            return [(str(g['id']), f"{g.get('name') or g['provider']} (ID: {g['id']})") for g in gateways]
        except Exception:
            return []
            
    cloudrefit_selected_payment_gateway_id = fields.Selection(
        selection=_get_payment_gateways,
        string='Select Payment Gateway',
        config_parameter='cloudrefit_invoicing.selected_payment_gateway_id',
        company_dependent=True,
        help='Select a specific payment integration for this Odoo instance.'
    )
    has_payment_integrations = fields.Boolean(
        string='Has Payment Integrations',
        compute='_compute_has_payment_integrations',
        help='Technical field to determine if payment settings should be visible'
    )
    
    @api.depends('cloudrefit_business_id_live')
    def _compute_has_payment_integrations(self):
        for rec in self:
            gateways_json = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.payment_gateways', '[]')
            try:
                gateways = json.loads(gateways_json)
                rec.has_payment_integrations = len(gateways) > 0
            except Exception:
                rec.has_payment_integrations = False

    cloudrefit_plugin_version = fields.Char(
        string="Plugin Version",
        compute="_compute_cloudrefit_plugin_version",
        readonly=True,
        help="Current version of the CloudRefit ZATCA Gateway plugin",
    )
    
    cloudrefit_webhook_url = fields.Char(
        string="Webhook Endpoint",
        compute="_compute_cloudrefit_webhook_url",
        readonly=True,
        help="The URL that CloudRefit Gateway will use to push background updates to your Odoo."
    )
    
    def _compute_cloudrefit_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        webhook_url = f"{base_url.rstrip('/')}/cloudrefit/webhook" if base_url else 'Please configure web.base.url in System Parameters'
        for record in self:
            record.cloudrefit_webhook_url = webhook_url

    # === Version Checker Fields ===
    cloudrefit_update_severity = fields.Selection([
        ('none', 'None'),
        ('info', 'Info'),
        ('minor', 'Minor'),
        ('major', 'Major'),
        ('critical', 'Critical'),
        ('urgent', 'Urgent'),
        ('blocked', 'Blocked')
    ], string='Update Severity', compute='_compute_cloudrefit_update_fields')
    cloudrefit_update_title = fields.Char(string='Update Title', compute='_compute_cloudrefit_update_fields')
    cloudrefit_update_message = fields.Char(string='Update Message', compute='_compute_cloudrefit_update_fields')
    cloudrefit_latest_version = fields.Char(string='Latest Version', compute='_compute_cloudrefit_update_fields')
    cloudrefit_update_checked_at = fields.Char(string='Last Checked At', compute='_compute_cloudrefit_update_fields')
    cloudrefit_changelog_url = fields.Char(string='Changelog URL', compute='_compute_cloudrefit_update_fields')

    def _compute_cloudrefit_update_fields(self):
        ICP = self.env['ir.config_parameter'].sudo()
        for rec in self:
            rec.cloudrefit_update_severity = ICP.get_param('cloudrefit_invoicing.update_severity', 'none')
            rec.cloudrefit_update_title = ICP.get_param('cloudrefit_invoicing.update_title', '')
            rec.cloudrefit_update_message = ICP.get_param('cloudrefit_invoicing.update_message', '')
            rec.cloudrefit_latest_version = ICP.get_param('cloudrefit_invoicing.latest_version', '')
            rec.cloudrefit_update_checked_at = ICP.get_param('cloudrefit_invoicing.update_checked_at', '')
            rec.cloudrefit_changelog_url = ICP.get_param('cloudrefit_invoicing.changelog_url', '')

    def action_check_now(self):
        # Auto-save any pending settings changes
        self.execute()
        
        # Check for updates
        self.env['cloudrefit.version.checker'].check_for_updates()
        
        # Reload the page to exit "dirty" state and show the new data
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # === Connection Health Indicator Fields (Live) ===
    gateway_health_status_live = fields.Char(
        string='Live Gateway Health Status',
        compute='_compute_health_fields_live',
        readonly=True,
        store=False,
    )
    gateway_health_last_check_live = fields.Char(
        string='Live Last Health Check',
        compute='_compute_health_fields_live',
        readonly=True,
        store=False,
    )
    gateway_health_message_live = fields.Char(
        string='Live Health Check Message',
        compute='_compute_health_fields_live',
        readonly=True,
        store=False,
    )

    # === Connection Health Indicator Fields (Sandbox) ===
    gateway_health_status_sandbox = fields.Char(
        string='Sandbox Gateway Health Status',
        compute='_compute_health_fields_sandbox',
        readonly=True,
        store=False,
    )
    gateway_health_last_check_sandbox = fields.Char(
        string='Sandbox Last Health Check',
        compute='_compute_health_fields_sandbox',
        readonly=True,
        store=False,
    )
    gateway_health_message_sandbox = fields.Char(
        string='Sandbox Health Check Message',
        compute='_compute_health_fields_sandbox',
        readonly=True,
        store=False,
    )

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICP = self.env['ir.config_parameter'].sudo()
        res.update(
            cloudrefit_zatca_download_xml=ICP.get_param('cloudrefit_invoicing.zatca_download_xml', 'True') == 'True',
            is_cloudrefit_live_enabled=ICP.get_param('cloudrefit_invoicing.live_enabled', 'False') == 'True',
            is_cloudrefit_sandbox_enabled=ICP.get_param('cloudrefit_invoicing.sandbox_enabled', 'False') == 'True',
            cloudrefit_show_sandbox_settings_ui=ICP.get_param('cloudrefit_invoicing.show_sandbox_settings', 'False') == 'True',
            cloudrefit_auto_generate_payment_links=ICP.get_param('cloudrefit_invoicing.auto_generate_payment_links', 'False') == 'True',
            cloudrefit_use_default_payment_gateway=ICP.get_param('cloudrefit_invoicing.use_default_payment_gateway', 'True') == 'True',
        )
        return res

    def set_values(self):
        """Override set_values to detect credential changes and reset connection status."""
        old_live = {
            'url': self._cr_get_param('cloudrefit_invoicing.gateway_url_live', ''),
            'api': self._cr_get_param('cloudrefit_invoicing.api_key_live', ''),
            'secret': self._cr_get_param('cloudrefit_invoicing.signing_secret_live', ''),
            'unit': self._cr_get_param('cloudrefit_invoicing.unit_id_live', ''),
        }
        old_sandbox = {
            'url': self._cr_get_param('cloudrefit_invoicing.gateway_url_sandbox', ''),
            'api': self._cr_get_param('cloudrefit_invoicing.api_key_sandbox', ''),
            'secret': self._cr_get_param('cloudrefit_invoicing.signing_secret_sandbox', ''),
            'unit': self._cr_get_param('cloudrefit_invoicing.unit_id_sandbox', ''),
        }

        super(ResConfigSettings, self).set_values()

        ICP = self.env['ir.config_parameter'].sudo()
        company = self.env.company
        
        # When saving credentials via the UI, they write to the base config_parameter keys.
        # If there are stale company-suffixed keys from an older version, they will shadow 
        # the new credentials. We must delete the suffixed keys to ensure the new ones take effect.
        if company:
            for key in ['gateway_url_live', 'api_key_live', 'signing_secret_live', 'unit_id_live',
                        'gateway_url_sandbox', 'api_key_sandbox', 'signing_secret_sandbox', 'unit_id_sandbox']:
                # Read the new value from the base key
                new_val = ICP.get_param(f'cloudrefit_invoicing.{key}')
                # Write it to the company-suffixed key to ensure consistency
                if new_val is not False:
                    ICP.set_param(f'cloudrefit_invoicing.{key}_{company.id}', new_val)

        # Manually save booleans
        ICP.set_param('cloudrefit_invoicing.zatca_download_xml', str(self.cloudrefit_zatca_download_xml))
        ICP.set_param('cloudrefit_invoicing.live_enabled', str(self.is_cloudrefit_live_enabled))
        ICP.set_param('cloudrefit_invoicing.sandbox_enabled', str(self.is_cloudrefit_sandbox_enabled))
        ICP.set_param('cloudrefit_invoicing.show_sandbox_settings', str(self.cloudrefit_show_sandbox_settings_ui))
        ICP.set_param('cloudrefit_invoicing.auto_generate_payment_links', str(self.cloudrefit_auto_generate_payment_links))
        ICP.set_param('cloudrefit_invoicing.use_default_payment_gateway', str(self.cloudrefit_use_default_payment_gateway))

        live_changed = (
            (self.cloudrefit_gateway_url_live or '') != old_live['url'] or
            (self.cloudrefit_api_key_live or '') != old_live['api'] or
            (self.cloudrefit_signing_secret_live or '') != old_live['secret'] or
            (self.cloudrefit_unit_id_live or '') != old_live['unit']
        )
        if live_changed:
            self._set_health_status('untested', 'Credentials changed, please test connection', mode='live')
            self._reset_mode_on_failure(mode='live', error_message='Credentials changed')

        sandbox_changed = (
            (self.cloudrefit_gateway_url_sandbox or '') != old_sandbox['url'] or
            (self.cloudrefit_api_key_sandbox or '') != old_sandbox['api'] or
            (self.cloudrefit_signing_secret_sandbox or '') != old_sandbox['secret'] or
            (self.cloudrefit_unit_id_sandbox or '') != old_sandbox['unit']
        )
        if sandbox_changed:
            self._set_health_status('untested', 'Credentials changed, please test connection', mode='sandbox')
            self._reset_mode_on_failure(mode='sandbox', error_message='Credentials changed')

    @api.model
    def default_get(self, fields_list):
        """Override to pre-populate health & can_enable fields — TransientModel compute is unreliable."""
        defaults = super().default_get(fields_list)

        # Health status fields
        health_live = self._cr_get_param('cloudrefit_invoicing.health_status_live', 'untested')
        defaults['gateway_health_status_live'] = health_live
        defaults['gateway_health_last_check_live'] = self._cr_get_param('cloudrefit_invoicing.health_last_check_live', '')
        defaults['gateway_health_message_live'] = self._cr_get_param('cloudrefit_invoicing.health_last_message_live', '')

        health_sandbox = self._cr_get_param('cloudrefit_invoicing.health_status_sandbox', 'untested')
        defaults['gateway_health_status_sandbox'] = health_sandbox
        defaults['gateway_health_last_check_sandbox'] = self._cr_get_param('cloudrefit_invoicing.health_last_check_sandbox', '')
        defaults['gateway_health_message_sandbox'] = self._cr_get_param('cloudrefit_invoicing.health_last_message_sandbox', '')

        # Business ID fields (read via both _cr_get_param and ICP base key)
        biz_id_live = self._cr_get_param('cloudrefit_invoicing.business_id_live', '')
        if not biz_id_live:
            biz_id_live = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.business_id_live', '')
        defaults['cloudrefit_business_id_live'] = biz_id_live
        
        biz_name_live = self._cr_get_param('cloudrefit_invoicing.business_name_live', '')
        if not biz_name_live:
            biz_name_live = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.business_name_live', '')
        defaults['cloudrefit_business_name_live'] = biz_name_live

        biz_id_sandbox = self._cr_get_param('cloudrefit_invoicing.business_id_sandbox', '')
        if not biz_id_sandbox:
            biz_id_sandbox = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.business_id_sandbox', '')
        defaults['cloudrefit_business_id_sandbox'] = biz_id_sandbox

        biz_name_sandbox = self._cr_get_param('cloudrefit_invoicing.business_name_sandbox', '')
        if not biz_name_sandbox:
            biz_name_sandbox = self.env['ir.config_parameter'].sudo().get_param('cloudrefit_invoicing.business_name_sandbox', '')
        defaults['cloudrefit_business_name_sandbox'] = biz_name_sandbox

        # can_enable fields (pre-computed because TransientModel compute is unreliable)
        live_filled = (
            biz_id_live
            and defaults.get('cloudrefit_gateway_url_live')
            and defaults.get('cloudrefit_api_key_live')
            and defaults.get('cloudrefit_signing_secret_live')
            and defaults.get('cloudrefit_unit_id_live')
        )
        defaults['can_enable_live'] = bool(live_filled and health_live == 'connected')
        if not defaults['can_enable_live']:
            defaults['is_cloudrefit_live_enabled'] = False

        sandbox_filled = (
            biz_id_sandbox
            and defaults.get('cloudrefit_gateway_url_sandbox')
            and defaults.get('cloudrefit_api_key_sandbox')
            and defaults.get('cloudrefit_signing_secret_sandbox')
            and defaults.get('cloudrefit_unit_id_sandbox')
        )
        defaults['can_enable_sandbox'] = bool(sandbox_filled and health_sandbox == 'connected')
        if not defaults['can_enable_sandbox']:
            defaults['is_cloudrefit_sandbox_enabled'] = False

        return defaults

    @api.depends_context('company')
    def _compute_health_fields_live(self):
        """Read live health status from ir.config_parameter."""
        for rec in self:
            rec.gateway_health_status_live = rec._cr_get_param('cloudrefit_invoicing.health_status_live', 'untested')
            rec.gateway_health_last_check_live = rec._cr_get_param('cloudrefit_invoicing.health_last_check_live', '')
            rec.gateway_health_message_live = rec._cr_get_param('cloudrefit_invoicing.health_last_message_live', '')

    @api.depends_context('company')
    def _compute_health_fields_sandbox(self):
        """Read sandbox health status from ir.config_parameter."""
        for rec in self:
            rec.gateway_health_status_sandbox = rec._cr_get_param('cloudrefit_invoicing.health_status_sandbox', 'untested')
            rec.gateway_health_last_check_sandbox = rec._cr_get_param('cloudrefit_invoicing.health_last_check_sandbox', '')
            rec.gateway_health_message_sandbox = rec._cr_get_param('cloudrefit_invoicing.health_last_message_sandbox', '')

    # === Plugin Version ===
    def _compute_cloudrefit_plugin_version(self):
        """Read version from installed module record (fallback to __manifest__.py)."""
        version = "unknown"

        # Primary: read from ir.module.module (most accurate post-install)
        try:
            module = self.env['ir.module.module'].sudo().search(
                [('name', '=', 'cloudrefit_invoicing')], limit=1
            )
            if module:
                ver = module.latest_version or module.installed_version
                if ver:
                    version = ver
        except Exception:
            pass

        # Fallback: read from __manifest__.py directly (no get_resource_path)
        if version == "unknown":
            try:
                import os
                import re
                manifest_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    '__manifest__.py'
                )
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                match = re.search(r"'version'\s*:\s*'([^']+)'", content)
                if match:
                    version = match.group(1)
            except Exception:
                pass

        for record in self:
            record.cloudrefit_plugin_version = version

    # === Health Check Helper ===
    def _set_health_status(self, status, message, mode='live'):
        """Store health check results in ir.config_parameter for the given mode."""
        self._cr_set_param(f'cloudrefit_invoicing.health_status_{mode}', status)
        self._cr_set_param(f'cloudrefit_invoicing.health_last_check_{mode}', datetime.datetime.now().isoformat())
        self._cr_set_param(f'cloudrefit_invoicing.health_last_message_{mode}', message)

    def _reset_mode_on_failure(self, mode='live', error_message=''):
        """On connection failure: conditionally clear business_id and disable mode.

        Only resets credentials on credential/config errors (invalid business_id,
        bad certificate, 401/403, etc.).  Transient network errors (timeout,
        connection refused, 502/503/504) are logged but do NOT reset, so a
        temporary gateway hiccup doesn't permanently disable ZATCA features.

        **IMPORTANT**: The enable checkbox value is written ONLY to the *base key*
        (via ``ICP.set_param``), NOT to the company-suffixed key (``_cr_set_param``).
        This prevents a stale ``'False'`` at the suffixed key from shadowing a
        later ``'True'`` written by Odoo's standard ``execute()`` save mechanism.

        If a stale suffixed key already exists (from a previous version), we
        explicitly clear it so ``_cr_get_param`` falls through to the base key.

        Business_id however IS written to both locations because it is read by
        both ``_cr_get_param`` (credential system) and ``ICP.get_param``
        (Odoo's ``config_parameter`` field resolution).

        Args:
            mode: ``'live'`` or ``'sandbox'``.
            error_message: the error text to inspect; transient errors skip reset.
        """
        error_lower = (error_message or '').lower()
        # Only reset on credential/config errors, not transient network issues
        credential_errors = ['business_id', 'invalid', 'not found', 'unauthorized',
                             'forbidden', 'certificate', 'credential', '401', '403']
        transient_errors = ['timeout', 'connection refused', 'dns', 'temporary',
                            '502', '503', '504', 'try again', 'retry']

        is_transient = any(t in error_lower for t in transient_errors)
        is_credential = any(c in error_lower for c in credential_errors)

        if is_transient and not is_credential:
            _logger.warning(
                "action=reset_mode_on_failure_transient mode=%s error_message=%s — "
                "NOT resetting credentials (transient network error)",
                mode, error_message,
            )
            return  # Don't reset on transient errors

        biz_param = f'cloudrefit_invoicing.business_id_{mode}'
        enable_param = f'cloudrefit_invoicing.{mode}_enabled'
        biz_field = f'cloudrefit_business_id_{mode}'
        enable_field = f'cloudrefit_{mode}_enabled'
        can_enable_field = f'can_enable_{mode}'

        ICP = self.env['ir.config_parameter'].sudo()

        # Clear business_id in both storage locations
        self._cr_set_param(biz_param, '')
        ICP.set_param(biz_param, '')
        self[biz_field] = False

        # Force enable checkbox to False — base key ONLY.
        # Clear any stale suffixed key so _cr_get_param falls through to base.
        company = self.env.company
        if company:
            ICP.set_param(f'{enable_param}_{company.id}', '')
        ICP.set_param(enable_param, 'False')
        self[enable_field] = False

        # can_enable_* is now False because business_id is empty
        self[can_enable_field] = False

        _logger.warning(
            "action=reset_mode_on_failure mode=%s error_message=%s — "
            "cleared business_id and disabled checkbox",
            mode, error_message,
        )

    @api.depends('cloudrefit_business_id_live', 'cloudrefit_gateway_url_live',
                 'cloudrefit_api_key_live', 'cloudrefit_signing_secret_live',
                 'cloudrefit_unit_id_live', 'gateway_health_status_live')
    def _compute_can_enable_live(self):
        """Enable Live toggle is editable only when all credentials are filled
        AND a connection test has succeeded (meaning business_id_live is also set).

        When the toggle is NOT editable, we also force the field to False
        so the checkbox appears unchecked + disabled (instead of checked + disabled)."""
        for rec in self:
            all_filled = (
                rec.cloudrefit_business_id_live
                and rec.cloudrefit_gateway_url_live
                and rec.cloudrefit_api_key_live
                and rec.cloudrefit_signing_secret_live
                and rec.cloudrefit_unit_id_live
            )
            connected = rec.gateway_health_status_live == 'connected'
            can_enable = bool(all_filled and connected)
            rec.can_enable_live = can_enable
            if not can_enable:
                rec.is_cloudrefit_live_enabled = False

    @api.depends('cloudrefit_business_id_sandbox', 'cloudrefit_gateway_url_sandbox',
                 'cloudrefit_api_key_sandbox', 'cloudrefit_signing_secret_sandbox',
                 'cloudrefit_unit_id_sandbox', 'gateway_health_status_sandbox')
    def _compute_can_enable_sandbox(self):
        """Enable Sandbox toggle is editable only when all credentials are filled
        AND a connection test has succeeded (meaning business_id_sandbox is also set).

        When the toggle is NOT editable, we also force the field to False
        so the checkbox appears unchecked + disabled (instead of checked + disabled)."""
        for rec in self:
            all_filled = (
                rec.cloudrefit_business_id_sandbox
                and rec.cloudrefit_gateway_url_sandbox
                and rec.cloudrefit_api_key_sandbox
                and rec.cloudrefit_signing_secret_sandbox
                and rec.cloudrefit_unit_id_sandbox
            )
            connected = rec.gateway_health_status_sandbox == 'connected'
            can_enable = bool(all_filled and connected)
            rec.can_enable_sandbox = can_enable
            if not can_enable:
                rec.is_cloudrefit_sandbox_enabled = False

    def _auto_save_business_id(self, response_data, mode='live'):
        """Extract and persist business_id, name, and units from a successful ping response."""
        _logger.info(
            "action=auto_save_business_id_enter mode=%s data_keys=%s",
            mode, list(response_data.keys()) if response_data else None,
        )
        bid = response_data.get('business_id')
        bname = response_data.get('business_name', '')
        units = response_data.get('technical_units', [])
        payment_gateways = response_data.get('payment_gateways', [])
        
        ICP = self.env['ir.config_parameter'].sudo()

        if bid:
            param_key = f'cloudrefit_invoicing.business_id_{mode}'
            name_key = f'cloudrefit_invoicing.business_name_{mode}'
            units_key = f'cloudrefit_invoicing.technical_units_{mode}'
            pg_key = 'cloudrefit_invoicing.payment_gateways'
            
            _logger.info("action=auto_save_business_id mode=%s business_id=%s name=%s units=%d", mode, bid, bname, len(units))
            
            self._cr_set_param(param_key, str(bid))
            ICP.set_param(param_key, str(bid))
            self[f'cloudrefit_business_id_{mode}'] = str(bid)

            self._cr_set_param(name_key, str(bname))
            ICP.set_param(name_key, str(bname))
            self[f'cloudrefit_business_name_{mode}'] = str(bname)
                
            # Always update units, even if empty
            units_json = json.dumps(units)
            self._cr_set_param(units_key, units_json)
            ICP.set_param(units_key, units_json)
            
            # Save payment gateways (from Live mode primarily)
            if mode == 'live' or payment_gateways:
                pg_json = json.dumps(payment_gateways)
                ICP.set_param(pg_key, pg_json)
                
                # Check if current selected gateway is still valid
                current_gw = ICP.get_param('cloudrefit_invoicing.selected_payment_gateway_id')
                if current_gw:
                    valid_gws = [str(g['id']) for g in payment_gateways]
                    if current_gw not in valid_gws:
                        ICP.set_param('cloudrefit_invoicing.selected_payment_gateway_id', '')
                        self.cloudrefit_selected_payment_gateway_id = False
            
            # Check if current selected unit is still valid
            current_unit = self._cr_get_param(f'cloudrefit_invoicing.unit_id_{mode}')
            if current_unit:
                valid_units = [str(u['id']) for u in units if str(u.get('mode', '')).upper() == mode.upper()]
                if current_unit not in valid_units:
                    # Clear it because it's no longer in the fetched list
                    self._cr_set_param(f'cloudrefit_invoicing.unit_id_{mode}', '')
                    ICP.set_param(f'cloudrefit_invoicing.unit_id_{mode}', '')
                    self[f'cloudrefit_unit_id_{mode}'] = False
                    _logger.info("Cleared stale unit_id_%s because it was not in the fetched units list", mode)
        else:
            _logger.info(
                "action=auto_save_business_id mode=%s no business_id in response", mode,
            )

    # === Connection Test: Live ===
    def action_test_connection_live(self):
        """Health check for Live gateway — calls /api/plugin/ping with live credentials.

        On success: saves business_id and sets health to 'connected'.
        On failure: clears business_id, forces enable checkbox to False, sets health to 'failed'.
        """
        self.ensure_one()
        self.execute()

        creds = self._get_zatca_credentials()
        gateway_url = self.cloudrefit_gateway_url_live or creds.get('gateway_url_live')
        api_key = self.cloudrefit_api_key_live or creds.get('api_key_live')
        signing_secret = self.cloudrefit_signing_secret_live or creds.get('signing_secret_live')

        reload_action = {'type': 'ir.actions.client', 'tag': 'reload'}

        if not api_key:
            self._reset_mode_on_failure(mode='live', error_message='API Key not configured')
            self._set_health_status('untested', 'API Key not configured', mode='live')
            self.gateway_health_status_live = 'untested'
            self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
            self.gateway_health_message_live = 'API Key not configured'
            return self._cr_notify(
                'warning', 'Live API Key not configured — enter your Live API Key and try again.',
                next_action=reload_action,
            )

        if not api_key.startswith('live_pk_'):
            msg = 'Mode isolation error: API Key is for SANDBOX mode, but request targets LIVE mode.'
            self._reset_mode_on_failure(mode='live', error_message=msg)
            self._set_health_status('failed', msg, mode='live')
            self.gateway_health_status_live = 'failed'
            self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
            self.gateway_health_message_live = 'Mode isolation error'
            return self._cr_notify('danger', msg, next_action=reload_action)

        try:
            body = json.dumps({'action': 'ping'}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            signature = hmac.new(
                signing_secret.encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()

            odoo_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            headers = {
                'X-API-Key': api_key,
                'X-Signature': signature,
                'Content-Type': 'application/json',
                'X-Integration-Url': odoo_url,
                'X-Mode': 'LIVE',
            }

            url = f"{gateway_url.rstrip('/')}/api/plugin/ping"
            _logger.info("Testing live gateway connectivity: %s", url)

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                message = data.get('message', '')
                status_val = data.get('status', '')
                self._auto_save_business_id(data, mode='live')
                if message == 'pong' or status_val == 'ok':
                    self._set_health_status('connected', message or 'Gateway reachable', mode='live')
                    self.gateway_health_status_live = 'connected'
                    self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
                    self.gateway_health_message_live = message or 'Gateway reachable'
                    return self._cr_notify(
                        'success', 'Connected successfully to Live Gateway',
                        next_action=reload_action,
                    )
                else:
                    self._reset_mode_on_failure(mode='live', error_message='Unexpected response from gateway')
                    self._set_health_status('failed', 'Unexpected response from gateway', mode='live')
                    self.gateway_health_status_live = 'failed'
                    self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
                    self.gateway_health_message_live = 'Unexpected response from gateway'
                    return self._cr_notify(
                        'danger', 'Connection failed: Unexpected response from Live Gateway',
                        next_action=reload_action,
                    )
            else:
                message = f"Gateway returned HTTP {response.status_code}: {response.text[:200]}"
                self._reset_mode_on_failure(mode='live', error_message=message)
                self._set_health_status('failed', message, mode='live')
                self.gateway_health_status_live = 'failed'
                self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
                self.gateway_health_message_live = message
                return self._cr_notify(
                    'danger', f'Connection failed: {message}',
                    next_action=reload_action,
                )

        except requests.exceptions.ConnectionError:
            message = f"Cannot reach live gateway at {gateway_url}"
            self._reset_mode_on_failure(mode='live', error_message=message)
            self._set_health_status('failed', message, mode='live')
            self.gateway_health_status_live = 'failed'
            self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
            self.gateway_health_message_live = message
            return self._cr_notify('danger', f'Connection failed: {message}', next_action=reload_action)

        except requests.exceptions.Timeout:
            message = f"Live gateway request timed out at {gateway_url}"
            self._reset_mode_on_failure(mode='live', error_message=message)
            self._set_health_status('failed', message, mode='live')
            self.gateway_health_status_live = 'failed'
            self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
            self.gateway_health_message_live = message
            return self._cr_notify('danger', f'Connection failed: {message}', next_action=reload_action)

        except Exception as e:
            message = str(e)
            self._reset_mode_on_failure(mode='live', error_message=message)
            self._set_health_status('failed', message, mode='live')
            self.gateway_health_status_live = 'failed'
            self.gateway_health_last_check_live = datetime.datetime.now().isoformat()
            self.gateway_health_message_live = message
            return self._cr_notify('danger', f'Connection failed: {message}', next_action=reload_action)

    # === Connection Test: Sandbox ===
    def action_test_connection_sandbox(self):
        """Health check for Sandbox gateway — calls /api/plugin/ping with sandbox credentials.

        On success: saves business_id and sets health to 'connected'.
        On failure: clears business_id, forces enable checkbox to False, sets health to 'failed'.
        """
        self.ensure_one()
        self.execute()

        creds = self._get_zatca_credentials()
        gateway_url = self.cloudrefit_gateway_url_sandbox or creds.get('gateway_url_sandbox')
        api_key = self.cloudrefit_api_key_sandbox or creds.get('api_key_sandbox')
        signing_secret = self.cloudrefit_signing_secret_sandbox or creds.get('signing_secret_sandbox')

        reload_action = {'type': 'ir.actions.client', 'tag': 'reload'}

        if not api_key:
            self._reset_mode_on_failure(mode='sandbox', error_message='API Key not configured')
            self._set_health_status('untested', 'API Key not configured', mode='sandbox')
            self.gateway_health_status_sandbox = 'untested'
            self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
            self.gateway_health_message_sandbox = 'API Key not configured'
            return self._cr_notify(
                'warning', 'Sandbox API Key not configured — enter your Sandbox API Key and try again.',
                next_action=reload_action,
            )

        if not api_key.startswith('test_pk_'):
            msg = 'Mode isolation error: API Key is for LIVE mode, but request targets SANDBOX mode.'
            self._reset_mode_on_failure(mode='sandbox', error_message=msg)
            self._set_health_status('failed', msg, mode='sandbox')
            self.gateway_health_status_sandbox = 'failed'
            self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
            self.gateway_health_message_sandbox = 'Mode isolation error'
            return self._cr_notify('danger', msg, next_action=reload_action)

        try:
            body = json.dumps({'action': 'ping'}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
            signature = hmac.new(
                signing_secret.encode(),
                body.encode(),
                hashlib.sha256,
            ).hexdigest()

            odoo_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
            headers = {
                'X-API-Key': api_key,
                'X-Signature': signature,
                'Content-Type': 'application/json',
                'X-Integration-Url': odoo_url,
                'X-Mode': 'SANDBOX',
            }

            url = f"{gateway_url.rstrip('/')}/api/plugin/ping"
            _logger.info("Testing sandbox gateway connectivity: %s", url)

            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                message = data.get('message', '')
                status_val = data.get('status', '')
                self._auto_save_business_id(data, mode='sandbox')
                if message == 'pong' or status_val == 'ok':
                    self._set_health_status('connected', message or 'Gateway reachable', mode='sandbox')
                    self.gateway_health_status_sandbox = 'connected'
                    self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
                    self.gateway_health_message_sandbox = message or 'Gateway reachable'
                    return self._cr_notify(
                        'success', 'Connected successfully to Sandbox Gateway',
                        next_action=reload_action,
                    )
                else:
                    self._reset_mode_on_failure(mode='sandbox', error_message='Unexpected response from gateway')
                    self._set_health_status('failed', 'Unexpected response from gateway', mode='sandbox')
                    self.gateway_health_status_sandbox = 'failed'
                    self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
                    self.gateway_health_message_sandbox = 'Unexpected response from gateway'
                    return self._cr_notify(
                        'danger', 'Connection failed: Unexpected response from Sandbox Gateway',
                        next_action=reload_action,
                    )
            else:
                message = f"Gateway returned HTTP {response.status_code}: {response.text[:200]}"
                self._reset_mode_on_failure(mode='sandbox', error_message=message)
                self._set_health_status('failed', message, mode='sandbox')
                self.gateway_health_status_sandbox = 'failed'
                self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
                self.gateway_health_message_sandbox = message
                return self._cr_notify(
                    'danger', f'Connection failed: {message}',
                    next_action=reload_action,
                )

        except requests.exceptions.ConnectionError:
            message = f"Cannot reach sandbox gateway at {gateway_url}"
            self._reset_mode_on_failure(mode='sandbox', error_message=message)
            self._set_health_status('failed', message, mode='sandbox')
            self.gateway_health_status_sandbox = 'failed'
            self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
            self.gateway_health_message_sandbox = message
            return self._cr_notify('danger', f'Connection failed: {message}', next_action=reload_action)

        except requests.exceptions.Timeout:
            message = f"Sandbox gateway request timed out at {gateway_url}"
            self._reset_mode_on_failure(mode='sandbox', error_message=message)
            self._set_health_status('failed', message, mode='sandbox')
            self.gateway_health_status_sandbox = 'failed'
            self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
            self.gateway_health_message_sandbox = message
            return self._cr_notify('danger', f'Connection failed: {message}', next_action=reload_action)

        except Exception as e:
            message = str(e)
            self._reset_mode_on_failure(mode='sandbox', error_message=message)
            self._set_health_status('failed', message, mode='sandbox')
            self.gateway_health_status_sandbox = 'failed'
            self.gateway_health_last_check_sandbox = datetime.datetime.now().isoformat()
            self.gateway_health_message_sandbox = message
            return self._cr_notify('danger', f'Connection failed: {message}', next_action=reload_action)

    # === Save & Connect Action ===
    def action_save_and_connect(self):
        """Save all settings AND verify gateway connectivity (both modes if applicable)."""
        self.ensure_one()

        # Step 1: Save the settings
        self.execute()

        # Step 2: Run connection tests
        creds = self._get_zatca_credentials()

        # Test live if live credentials are configured
        if creds.get('api_key_live'):
            self.action_test_connection_live()

        # Test sandbox if sandbox credentials are configured
        if creds.get('api_key_sandbox'):
            self.action_test_connection_sandbox()

        # Step 3: Return an action that reloads the settings page
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'res.config.settings',
            'view_mode': 'form',
            'view_id': self.env.ref('cloudrefit_invoicing.res_config_settings_view_form_zatca').id,
            'target': 'self',
            'context': self.env.context,
        }
    @api.onchange('cloudrefit_api_key_live', 'cloudrefit_signing_secret_live', 'cloudrefit_unit_id_live', 'is_cloudrefit_live_enabled',
                  'cloudrefit_api_key_sandbox', 'cloudrefit_signing_secret_sandbox', 'cloudrefit_unit_id_sandbox', 'is_cloudrefit_sandbox_enabled')
    def _onchange_auto_save_cloudrefit_fields(self):
        """Silently persist critical fields to ir.config_parameter on blur/change without triggering a page reload."""
        ICP = self.env['ir.config_parameter'].sudo()
        company = self.env.company

        # Helper to save string fields
        def _save_param(key, val):
            if val is not False and val is not None:
                ICP.set_param(f'cloudrefit_invoicing.{key}', str(val))
                if company:
                    ICP.set_param(f'cloudrefit_invoicing.{key}_{company.id}', str(val))
            else:
                ICP.set_param(f'cloudrefit_invoicing.{key}', '')
                if company:
                    ICP.set_param(f'cloudrefit_invoicing.{key}_{company.id}', '')

        _save_param('api_key_live', self.cloudrefit_api_key_live)
        _save_param('signing_secret_live', self.cloudrefit_signing_secret_live)
        _save_param('unit_id_live', self.cloudrefit_unit_id_live)
        
        _save_param('api_key_sandbox', self.cloudrefit_api_key_sandbox)
        _save_param('signing_secret_sandbox', self.cloudrefit_signing_secret_sandbox)
        _save_param('unit_id_sandbox', self.cloudrefit_unit_id_sandbox)

        # Helper for booleans
        if self.is_cloudrefit_live_enabled is not None:
            ICP.set_param('cloudrefit_invoicing.live_enabled', str(self.is_cloudrefit_live_enabled))
        if self.is_cloudrefit_sandbox_enabled is not None:
            ICP.set_param('cloudrefit_invoicing.sandbox_enabled', str(self.is_cloudrefit_sandbox_enabled))
