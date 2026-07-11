import logging
import json
import os

from odoo import models, fields, api
from odoo.exceptions import UserError
import requests
import uuid
import base64
import time

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = ['account.move', 'cloudrefit.notification.helper', 'cloudrefit.settings.helper', 'cloudrefit.zatca.credentials']

    # === Core ZATCA Fields (no sandbox/live suffixes) ===
    zatca_status = fields.Selection([
        ('not_signed', 'Not Signed'),
        ('signed', 'Signed'),
        ('reported', 'Reported'),
        ('cleared', 'Cleared'),
        ('failed', 'Failed'),
    ], string='ZATCA Status', default='not_signed', copy=False,
       help='Status of the last ZATCA submission for this invoice')

    zatca_hash = fields.Char(string='ZATCA Invoice Hash', readonly=True, copy=False,
                             help='Digital hash returned by ZATCA after successful signing')
    zatca_qr_code = fields.Text(string='ZATCA QR Code', readonly=True, copy=False,
                                help='Base64-encoded QR code from ZATCA, embedded in the PDF report')
    zatca_error = fields.Text(string='ZATCA Error', readonly=True, copy=False)
    zatca_signed_xml = fields.Text(string='ZATCA Signed XML', readonly=True, copy=False,
                                   help='Full signed XML returned by ZATCA. Embedded in PDF when printing.')
    
    zatca_signed_xml_file = fields.Binary(
        string='Download Signed XML',
        compute='_compute_zatca_signed_xml_file',
        readonly=True
    )
    zatca_signed_xml_filename = fields.Char(
        string='XML Filename',
        compute='_compute_zatca_signed_xml_file',
    )

    zatca_invoice_type = fields.Selection([
        ('standard', 'Standard (B2B)'),
        ('simplified', 'Simplified (B2C)'),
    ], string='ZATCA Invoice Type Used', readonly=True, copy=False)

    # === Utility Fields ===
    zatca_uuid = fields.Char(string='ZATCA UUID', readonly=True, copy=False, index=True,
                             help='Unique identifier (UUID v4) assigned to this invoice for ZATCA compliance')
    zatca_retry_count = fields.Integer(string='ZATCA Retry Count', default=0, copy=False,
                                       help='Number of times this invoice has been retried for ZATCA submission')
    zatca_exec_mode = fields.Selection([
        ('sandbox', 'Sandbox'),
        ('live', 'Live'),
    ], string='ZATCA Target Used', readonly=True, copy=False,
       help='The execution mode used for the last ZATCA signing attempt')

    # === Async Job Tracking Fields ===
    zatca_job_uuid = fields.Char(string='ZATCA Job UUID', readonly=True, copy=False,
                                 help='UUID of the asynchronous ZATCA signing job. Used to poll for completion.')
    zatca_job_status = fields.Selection([
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ], string='ZATCA Job Status', readonly=True, copy=False,
       help='Current status of the asynchronous ZATCA signing job')

    zatca_pushed_at = fields.Datetime(
        string='ZATCA Pushed At', readonly=True, copy=False,
        help='Timestamp when the invoice was sent to ZATCA for processing.'
    )

    mode_override = fields.Selection([
        ('auto', 'Use Global Setting'),
        ('sandbox', 'Sandbox'),
        ('live', 'Live'),
    ], string='Target Mode', default='auto')

    zatca_invoice_type_override = fields.Selection([
        ('auto', 'Auto-Detect (Based on VAT)'),
        ('standard', 'Standard (B2B)'),
        ('simplified', 'Simplified (B2C)'),
    ], string='Force ZATCA Type', default='auto',
        help='Override the auto-detected ZATCA invoice type for this specific invoice.')

    is_zatca_push_allowed = fields.Boolean(
        compute='_compute_is_zatca_push_allowed', string="Is ZATCA Push Allowed",
        help="Whether this invoice meets all conditions to be pushed to ZATCA"
    )

    zatca_show_quick_push = fields.Boolean(
        compute='_compute_zatca_show_quick_push',
        help="Technical field to control visibility of quick-push buttons"
    )

    is_zatca_sandbox_allowed = fields.Boolean(
        compute='_compute_is_zatca_sandbox_allowed', string="Is ZATCA Sandbox Allowed",
        help="Whether all sandbox credentials are configured and sandbox is enabled"
    )

    zatca_15_days_warning = fields.Boolean(
        compute='_compute_zatca_15_days_warning',
        string="15 Days Warning",
        help="Indicates if the invoice is older than 15 days and cannot be pushed to ZATCA Live."
    )

    zatca_config_warning = fields.Char(
        compute='_compute_is_zatca_push_allowed',
        string="ZATCA Config Warning",
        help="Dynamically displays missing ZATCA configuration warnings"
    )

    # === Update Banner Fields ===
    cloudrefit_effective_severity = fields.Selection([
        ('none', 'None'),
        ('info', 'Info'),
        ('minor', 'Minor'),
        ('major', 'Major'),
        ('critical', 'Critical'),
        ('urgent', 'Urgent'),
        ('blocked', 'Blocked')
    ], string='Effective Update Severity', compute='_compute_cloudrefit_update_banner_fields')
    cloudrefit_update_title = fields.Char(string='Update Title', compute='_compute_cloudrefit_update_banner_fields')
    cloudrefit_show_banner = fields.Boolean(compute='_compute_cloudrefit_update_banner_fields')
    cloudrefit_banner_dismissible = fields.Boolean(compute='_compute_cloudrefit_update_banner_fields')
    cloudrefit_show_push_modal = fields.Boolean(compute='_compute_cloudrefit_update_banner_fields')
    cloudrefit_push_blocked = fields.Boolean(compute='_compute_cloudrefit_update_banner_fields')

    def _compute_cloudrefit_update_banner_fields(self):
        ICP = self.env['ir.config_parameter'].sudo()
        severity = ICP.get_param('cloudrefit_invoicing.update_severity', 'none')
        title = ICP.get_param('cloudrefit_invoicing.update_title', '')
        latest_version = ICP.get_param('cloudrefit_invoicing.latest_version', '')
        dismissed_version = ICP.get_param('cloudrefit_invoicing.dismissed_version', '')
        
        # Check if the current major/minor version has been dismissed
        is_dismissed = False
        if severity in ('minor', 'major') and dismissed_version == latest_version:
            is_dismissed = True

        for rec in self:
            rec.cloudrefit_update_title = title
            rec.cloudrefit_effective_severity = severity if not is_dismissed else 'none'
            rec.cloudrefit_show_banner = severity in ('major', 'critical', 'urgent', 'blocked') and not is_dismissed
            rec.cloudrefit_banner_dismissible = severity == 'major'
            rec.cloudrefit_show_push_modal = severity == 'urgent'
            rec.cloudrefit_push_blocked = severity == 'blocked'

    def action_dismiss_update(self):
        """Dismiss the current update banner."""
        ICP = self.env['ir.config_parameter'].sudo()
        latest_version = ICP.get_param('cloudrefit_invoicing.latest_version', '')
        if latest_version:
            ICP.set_param('cloudrefit_invoicing.dismissed_version', latest_version)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    @api.model_create_multi
    def create(self, vals_list):
        moves = super(AccountMove, self).create(vals_list)
        import uuid as py_uuid
        
        # Automatically and deterministically assign zatca_uuid to all invoices and refunds upon creation
        for move in moves.filtered(lambda m: m.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund')):
            if not move.zatca_uuid:
                # Use UUIDv5 with Odoo database name and move ID for absolute determinism
                namespace = py_uuid.uuid5(py_uuid.NAMESPACE_OID, move.env.cr.dbname)
                move.zatca_uuid = str(py_uuid.uuid5(namespace, str(move.id)))
        
        return moves

    # -----------------------------------------------------------
    #  COMPUTES
    # -----------------------------------------------------------
    @api.depends('invoice_date', 'state', 'zatca_status')
    def _compute_zatca_15_days_warning(self):
        for move in self:
            if move.state == 'posted' and move.invoice_date and move.zatca_status not in ('reported', 'cleared'):
                delta = fields.Date.context_today(move) - move.invoice_date
                move.zatca_15_days_warning = delta.days > 15
            else:
                move.zatca_15_days_warning = False

    @api.depends('zatca_signed_xml', 'name')
    def _compute_zatca_signed_xml_file(self):
        for move in self:
            if move.zatca_signed_xml:
                move.zatca_signed_xml_file = base64.b64encode(move.zatca_signed_xml.encode('utf-8'))
                safe_name = (move.name or 'invoice').replace('/', '_')
                move.zatca_signed_xml_filename = f"{safe_name}_ZATCA.xml"
            else:
                move.zatca_signed_xml_file = False
                move.zatca_signed_xml_filename = False

    # -----------------------------------------------------------
    #  SANDBOX ZATCA SUBMISSION
    # -----------------------------------------------------------
    @api.depends_context('company')
    def _compute_is_zatca_push_allowed(self):
        for move in self:
            move_with_ctx = move.with_company(move.company_id)
            allowed, reason = move_with_ctx._check_zatca_push_allowed()
            move.is_zatca_push_allowed = allowed
            if not allowed and reason and 'not fully configured' in reason:
                move.zatca_config_warning = reason
            else:
                move.zatca_config_warning = False

    @api.depends('state', 'zatca_status', 'move_type', 'is_zatca_push_allowed')
    def _compute_zatca_show_quick_push(self):
        for move in self:
            move.zatca_show_quick_push = (
                move.is_zatca_push_allowed
                and move.state == 'posted'
                and move.move_type in ('out_invoice', 'out_refund')
            )

    @api.depends('state', 'company_id', 'move_type')
    @api.depends_context('company')
    def _compute_is_zatca_sandbox_allowed(self):
        """Check if sandbox credentials are configured and sandbox is enabled."""
        for move in self:
            creds = move.with_company(move.company_id)._get_zatca_credentials()
            missing = move._validate_zatca_credentials(creds, mode='sandbox')
            move.is_zatca_sandbox_allowed = len(missing) == 0
            if not move.is_zatca_sandbox_allowed:
                _logger.info(
                    "action=sandbox_not_allowed invoice_id=%s company=%s "
                    "sandbox_enabled=%s biz_id=%s gateway=%s api_key=%s "
                    "signing_secret=%s unit_id=%s missing=%s",
                    move.id, creds.get('company_name'),
                    creds.get('sandbox_enabled'),
                    creds.get('business_id_sandbox'),
                    creds.get('gateway_url_sandbox'),
                    creds.get('api_key_sandbox'),
                    creds.get('signing_secret_sandbox'),
                    creds.get('unit_id_sandbox'),
                    missing,
                )

    def _check_zatca_push_allowed(self):
        """Check if this invoice can be pushed to ZATCA (live only).

        Returns:
            tuple: (allowed: bool, reason: str)
        """
        self.ensure_one()
        if self.state != 'posted':
            return False, 'Invoice is not posted'
        if self.zatca_status in ('reported', 'cleared') and self.zatca_exec_mode == 'live':
            return False, 'Already signed to ZATCA Live'
        if not self.invoice_date:
            return False, 'Invoice date is missing'
        delta = fields.Date.context_today(self) - self.invoice_date
        if delta.days > 15:
            return False, 'Invoice date older than 15 days'

        # For LIVE pushes only: credit/debit notes must reference a successfully reported parent.
        # Sandbox pushes bypass this check so developers can test notes freely.
        if self.move_type in ('out_refund', 'in_refund'):
            if not self.reversed_entry_id:
                return False, 'Credit/Debit Note is not linked to an original invoice.'
            # Only enforce this for live — sandbox may test against unreported originals
            # (this check is for the live-push button guard only)

        creds = self.with_company(self.company_id)._get_zatca_credentials()
        missing = self._validate_zatca_credentials(creds, mode='live')
        if missing:
            _logger.info(
                "action=diag_push_not_allowed invoice_id=%s company=%s "
                "live_enabled=%s biz_id=%s gateway=%s api_key=%s "
                "signing_secret=%s unit_id=%s missing=%s",
                self.id, creds.get('company_name'),
                creds.get('live_enabled'),
                creds.get('business_id_live'),
                creds.get('gateway_url_live'),
                creds.get('api_key_live'),
                creds.get('signing_secret_live'),
                creds.get('unit_id_live'),
                missing,
            )
            return False, 'ZATCA Live not fully configured: ' + ', '.join(missing)
        return True, ''

    def action_zatca_sign(self):
        """Manually trigger ZATCA signing."""
        for move in self:
            if move.zatca_status in ('reported', 'cleared') and move.zatca_exec_mode == 'live':
                raise UserError('This invoice is already reported to ZATCA Live.')
            move._zatca_sign_invoice()

    def action_zatca_sign_invoice(self):
        """Alias for action_zatca_sign — used by the tree view quick-push button."""
        return self.action_zatca_sign()

    # -----------------------------------------------------------
    #  SANDBOX ZATCA SUBMISSION
    # -----------------------------------------------------------
    def action_push_to_sandbox_zatca(self):
        """Submit invoice to ZATCA Sandbox directly and persist results."""
        self.ensure_one()
        if self.zatca_status in ('reported', 'cleared') and self.zatca_exec_mode == 'live':
            raise UserError('This invoice is already reported to ZATCA Live and cannot be pushed to Sandbox.')
        if self.zatca_status in ('reported', 'cleared') and self.zatca_exec_mode == 'sandbox':
            raise UserError('This invoice is already reported to ZATCA Sandbox.')
        self._zatca_sign_invoice(force_mode='sandbox')

    def action_post(self):
        result = super().action_post()
        
        # Ensure deterministic UUIDs are generated for invoices/refunds upon posting
        import uuid as py_uuid
        for move in self.filtered(lambda m: m.move_type in ('out_invoice', 'out_refund', 'in_invoice', 'in_refund')):
            if not move.zatca_uuid:
                # Use UUIDv5 to ensure it is always identical for the same database and invoice ID
                namespace = py_uuid.uuid5(py_uuid.NAMESPACE_OID, move.env.cr.dbname)
                move.zatca_uuid = str(py_uuid.uuid5(namespace, str(move.id)))


        return result

    def _resolve_invoice_type(self):
        """
        Resolve invoice type with 3-layer config:
        1. Global default from res.config.settings
        2. Invoice-level override
        3. Auto-detect: B2B if customer is Company, else B2C
        """
        self.ensure_one()
        partner = self.partner_id

        # Layer 1: global default
        creds = self.with_company(self.company_id)._get_zatca_credentials()
        global_default = creds.get('default_invoice_type', 'auto')
        if global_default != 'auto':
            return global_default

        # Layer 2: Invoice-level override
        if self.zatca_invoice_type_override and self.zatca_invoice_type_override != 'auto':
            return self.zatca_invoice_type_override

        # Layer 3: auto-detect
        return 'standard' if partner.is_company else 'simplified'

    def _resolve_mode(self):
        """Resolve target mode (sandbox vs live) — defaults to 'live'."""
        self.ensure_one()
        if self.mode_override and self.mode_override != 'auto':
            return self.mode_override

        creds = self.with_company(self.company_id)._get_zatca_credentials()
        mode = creds.get('mode', 'live')
        if mode == 'none':
            _logger.warning("cloudrefit_mode is 'none' (not configured), falling back to 'live'")
            mode = 'live'
        return mode

    def _build_zatca_payload(self, mode=None):
        """Map Odoo invoice fields to the Gateway's expected JSON format.

        Args:
            mode: Optional mode override ('sandbox' or 'live'). If provided,
                  this value is used instead of calling _resolve_mode().
        """
        self.ensure_one()
        partner = self.partner_id
        invoice_type = self._resolve_invoice_type()

        lines = []
        for line in self.invoice_line_ids.filtered(lambda l: l.display_type == 'product'):
            # Skip zero-quantity lines
            if line.quantity == 0:
                _logger.debug("action=%s invoice_id=%s line_id=%s quantity=0 skipping",
                              'build_payload', self.id, line.id)
                continue
            # Log debug for fully discounted lines but include them
            if line.price_subtotal == 0:
                _logger.debug("action=%s invoice_id=%s line_id=%s price_subtotal=0 including",
                              'build_payload', self.id, line.id)
            tax = line.tax_ids[:1]
            tax_percent = tax.amount if tax else 0.0
            tax_category = 'S' if tax_percent > 0 else 'Z'
            
            tax_amount = sum(line.tax_ids.mapped(
                lambda t: line.price_subtotal * t.amount / 100
            ))
            lines.append({
                'name': line.name,
                'quantity': float(line.quantity),
                'price_unit': float(line.price_unit),
                'subtotal': float(line.price_subtotal),
                'tax_amount': float(tax_amount),
                'tax_percent': float(tax_percent),
                'tax_category': tax_category,
            })

        # Warn if no line items after filtering
        resolved_mode = mode or self._resolve_mode()
        if not lines:
            _logger.warning("action=%s invoice_id=%s mode=%s no_line_items",
                            'build_payload', self.id, resolved_mode)

        # Ensure a stable, ZATCA-compliant UUID v4/v5 exists for this invoice
        import uuid as py_uuid
        if not self.zatca_uuid:
            namespace = py_uuid.uuid5(py_uuid.NAMESPACE_OID, self.env.cr.dbname)
            self.zatca_uuid = str(py_uuid.uuid5(namespace, str(self.id)))

        # Resolve unit_id based on the resolved mode, using centralized credentials
        mode = mode or self._resolve_mode()
        creds = self.with_company(self.company_id)._get_zatca_credentials()
        self._assert_zatca_credentials(creds, mode=mode)

        unit_id_str = creds.get(f'unit_id_{mode}', '')
        unit_id = int(unit_id_str) if unit_id_str and unit_id_str.isdigit() else None

        if unit_id is None:
            raise UserError(
                'CloudRefit ZATCA Technical Unit ID (%s) is not configured for '
                'company "%s". Please configure it in Settings \u2192 CloudRefit ZATCA.'
                % (mode.title(), creds['company_name'])
            )

        # Validate required fields before sending
        if not self.name:
            raise UserError('Invoice number is missing. Cannot sign with ZATCA.')
        if not self.invoice_date:
            raise UserError('Invoice date is missing. Cannot sign with ZATCA.')
            
        # ZATCA Identity and Address Pre-Flight Check
        missing = []
        if invoice_type == 'standard':
            if not bool(partner.vat) and not bool(partner.zatca_id_type and partner.zatca_id_value):
                missing.append("- VAT Number OR an ID (Type + Value)")
            if partner.country_id.code == 'SA':
                if not partner.building_no:
                    missing.append("- Building Number (Required for Saudi B2B)")
                if not partner.district:
                    missing.append("- District (Required for Saudi B2B)")
                if not partner.zip:
                    missing.append("- Postal Code (Zip) (Required for Saudi B2B)")
            if not partner.street:
                missing.append("- Street")
            if not partner.city:
                missing.append("- City")
            if not partner.country_id:
                missing.append("- Country")
        elif invoice_type == 'simplified':
            has_mobile = hasattr(partner, 'mobile') and bool(partner.mobile)
            has_phone = bool(partner.phone)
            if not has_mobile and not has_phone:
                missing.append("- Mobile or Phone Number")
                
        if missing:
            raise UserError(
                f"Cannot push to ZATCA! The customer '{partner.name}' is missing required fields for a {invoice_type.title()} invoice:\n" +
                "\n".join(missing) +
                "\n\nPlease update the customer record before pushing."
            )
            
        # ZATCA Credit Note Reason Pre-Flight Check (BR-KSA-17)
        if self.move_type in ('out_refund', 'in_refund') and not self.ref:
            raise UserError("Reason is required for Credit/Debit Notes (ZATCA BR-KSA-17). Please enter a reason in the Reference field.")

        # Ensure UUID has dashes for Gateway validation
        formatted_uuid = self.zatca_uuid
        if formatted_uuid and len(formatted_uuid) == 32 and '-' not in formatted_uuid:
            formatted_uuid = str(py_uuid.UUID(formatted_uuid))

        is_credit = self.move_type in ('out_refund', 'in_refund')
        is_debit = hasattr(self, 'debit_origin_id') and getattr(self, 'debit_origin_id')
        
        # Format the type exactly as the Gateway expects: e.g. STANDARD_CREDIT_NOTE
        if is_credit:
            zatca_type_code = f"{invoice_type}_credit_note".upper()
        elif is_debit:
            zatca_type_code = f"{invoice_type}_debit_note".upper()
        else:
            zatca_type_code = invoice_type.upper()

        payload = {
            'mode': mode,
            'unit_id': unit_id,
            'invoice': {
                'number': self.name,
                'date': str(self.invoice_date) + ' 12:00:00',
                'uuid': formatted_uuid,
                'type': zatca_type_code,
                'amount_untaxed': float(self.amount_untaxed),
                'tax_total': float(self.amount_tax),
                'amount_total': float(self.amount_total),
            },
            'lines': lines,
            'customer': {
                'name': partner.name,
                'vat': partner.vat or '300000000000003',
                'address': partner.street or '',
                'city': partner.city or 'Riyadh',
            },
        }

        # Credit/Debit Note Handling
        if is_credit or is_debit:
            origin_move = self.reversed_entry_id if is_credit else getattr(self, 'debit_origin_id')
            if not origin_move:
                raise UserError('Credit/Debit Note must be linked to an original invoice.')
            
            # Ensure origin move has a UUID deterministically
            if not origin_move.zatca_uuid:
                namespace = py_uuid.uuid5(py_uuid.NAMESPACE_OID, self.env.cr.dbname)
                origin_move.zatca_uuid = str(py_uuid.uuid5(namespace, str(origin_move.id)))
                
            payload['invoice']['origin_number'] = origin_move.name
            payload['invoice']['origin_uuid'] = origin_move.zatca_uuid
            payload['invoice']['adjustment_reason'] = self.ref or self.name or 'Correction of previous invoice'

        return payload

    def _zatca_sign_invoice(self, force_mode=None):
        """Call the CloudRefit Gateway to sign this invoice.

        Orchestrator that delegates to small, focused helpers.
        """
        self.ensure_one()
        # Pass force_mode into payload builder so credentials are resolved for
        # the correct mode from the start — avoids Live credential checks on Sandbox pushes.
        payload = self._build_zatca_payload(mode=force_mode)

        exec_mode = payload.get('mode', 'live')
        api_client = self.env['cloudrefit.zatca.api.client']
        headers = api_client._build_headers(payload, exec_mode)

        # Notify user that submission is starting
        self._cr_notify('info', 'Submitting to ZATCA...')

        response, status_code = self._send_zatca_sign_request(payload, headers, exec_mode)

        if status_code == 201:
            self._handle_zatca_sync_response(response, self, exec_mode)
        elif status_code == 202:
            self._handle_zatca_async_response(response, self, exec_mode)
            # Notify user about async queuing
            self._cr_notify(
                'warning',
                'Invoice queued for ZATCA processing. Job will complete shortly.',
                sticky=True,
            )
        else:
            self._handle_zatca_error_response(response, status_code, self, exec_mode)

    def _send_zatca_sign_request(self, payload, headers, exec_mode):
        """Makes the HTTP POST to the sign endpoint with retry on retry-able status codes.

        Returns (response, status_code).
        """
        creds = self.with_company(self.company_id)._get_zatca_credentials()
        business_id = creds.get(f'business_id_{exec_mode}')
        gateway_url = creds.get(f'gateway_url_{exec_mode}')

        if not business_id:
            raise UserError(
                'CloudRefit ZATCA Business ID is not configured for company '
                '"%s". Please configure it in Settings \u2192 CloudRefit ZATCA.'
                % creds['company_name']
            )

        url = f"{gateway_url.rstrip('/')}/api/v1/invoices/{business_id}/sign"

        max_retries = 2
        retry_delay = 5
        response = None

        # Log request details for debugging
        safe_headers = headers.copy()
        if 'X-API-Key' in safe_headers:
            safe_headers['X-API-Key'] = '***'
        if 'X-Signature' in safe_headers:
            safe_headers['X-Signature'] = '***'

        _logger.info(
            "action=send_zatca_sign_request invoice_id=%s mode=%s url=%s headers=%s payload=%s",
            self.id, exec_mode, url, safe_headers,
            json.dumps(payload, ensure_ascii=False) if payload else None
        )

        for attempt in range(max_retries + 1):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=30)
            except requests.exceptions.ConnectionError:
                if attempt < max_retries:
                    _logger.warning(
                        "action=%s invoice_id=%s mode=%s attempt=%s/%s connection_error_retrying",
                        'send_sign_request', self.id, exec_mode, attempt + 1, max_retries
                    )
                    time.sleep(retry_delay)
                    continue
                raise UserError(f'Cannot connect to CloudRefit Gateway at {gateway_url}.')
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    _logger.warning(
                        "action=%s invoice_id=%s mode=%s attempt=%s/%s timeout_retrying",
                        'send_sign_request', self.id, exec_mode, attempt + 1, max_retries
                    )
                    time.sleep(retry_delay)
                    continue
                raise UserError('CloudRefit Gateway request timed out.')

            if response.status_code not in (429, 502, 503, 504):
                return response, response.status_code

            if attempt < max_retries:
                sleep_time = 10 if response.status_code == 429 else retry_delay
                _logger.warning(
                    "action=%s invoice_id=%s mode=%s http_status=%s attempt=%s/%s retry_after=%ss",
                    'send_sign_request', self.id, exec_mode, response.status_code,
                    attempt + 1, max_retries, sleep_time
                )
                time.sleep(sleep_time)
            else:
                _logger.error(
                    "action=%s invoice_id=%s mode=%s http_status=%s max_retries_exceeded=%s",
                    'send_sign_request', self.id, exec_mode, response.status_code, max_retries
                )
                return response, response.status_code

        return response, response.status_code

    def _handle_zatca_sync_response(self, response, move, exec_mode):
        """Handle 201 (synchronous) response: extract ZATCA status and update invoice.

        Returns True if handled successfully.
        """
        signed_data = response.json().get('signedData', {})
        invoice_type = move._resolve_invoice_type()
        zatca_status = 'cleared' if invoice_type == 'standard' else 'reported'

        self._update_invoice_after_zatca_sign(
            move,
            status=zatca_status,
            zatca_hash=signed_data.get('hash', ''),
            qr_code=signed_data.get('qrImage', ''),
            signed_xml=signed_data.get('xml', ''),
            error_msg=False,
            invoice_type=invoice_type,
            exec_mode=exec_mode,
        )
        return True

    def _handle_zatca_async_response(self, response, move, exec_mode):
        """Handle 202 (asynchronous) response: store job UUID and return immediately.

        The cron job _zatca_retry_failed will poll for job completion,
        avoiding blocking Odoo workers.
        """
        job_data = response.json()
        job_uuid = job_data.get('job_uuid') or job_data.get('uuid')
        if not job_uuid:
            raise UserError('Gateway returned 202 Accepted but did not provide a job UUID.')

        move.write({
            'zatca_job_uuid': job_uuid,
            'zatca_job_status': 'pending',
            'zatca_exec_mode': exec_mode,
            'zatca_pushed_at': fields.Datetime.now(),
        })

        _logger.info(
            "action=async_queued invoice_id=%s job_uuid=%s mode=%s",
            move.id, job_uuid, exec_mode
        )

    def _handle_zatca_error_response(self, response, status_code, move, exec_mode):
        """Handle error responses: log error, set error status on invoice, and raise UserError."""
        error_detail = response.text
        try:
            error_detail = response.json().get('message', error_detail)
        except (json.JSONDecodeError, KeyError) as e:
            _logger.warning(
                "action=%s invoice_id=%s mode=%s error=%s raw_response=%.200s",
                'sign_invoice_parse_error', move.id, exec_mode, str(e),
                response.text[:200] if response.text else ''
            )

        self._update_invoice_after_zatca_sign(
            move,
            status='failed',
            zatca_hash='',
            qr_code='',
            signed_xml='',
            error_msg=f"[{status_code}] {error_detail}",
            invoice_type='',
            exec_mode=exec_mode,
        )
        raise UserError(f'ZATCA signing failed: {error_detail}')

    def _update_invoice_after_zatca_sign(
        self, move, status, zatca_hash, qr_code, signed_xml, error_msg,
        invoice_type, exec_mode
    ):
        """Unified writer for ZATCA result fields.

        Writes core fields directly (no environment suffix pattern).
        """
        if status == 'failed':
            vals = {
                'zatca_status': 'failed',
                'zatca_error': error_msg,
                'zatca_retry_count': move.zatca_retry_count + 1,
            }
        else:
            vals = {
                'zatca_status': status,
                'zatca_exec_mode': exec_mode,
                'zatca_hash': zatca_hash,
                'zatca_qr_code': qr_code,
                'zatca_signed_xml': signed_xml,
                'zatca_error': False,
                'zatca_retry_count': 0,
                'zatca_invoice_type': invoice_type,
            }
        move.write(vals)

    @api.model
    def _zatca_retry_failed(self):
        """Cron job: Poll in-flight async jobs and retry failed invoices."""
        # --- Part 1: Poll in-flight async jobs ---
        in_flight = self.search([
            ('zatca_job_status', 'in', ['pending', 'processing']),
            ('zatca_job_uuid', '!=', False),
            ('move_type', 'in', ['out_invoice', 'out_refund']),
        ])
        api_client = self.env['cloudrefit.zatca.api.client']

        for move in in_flight:
            try:
                # Switch to the invoice's company context for credential resolution
                move_with_ctx = move.with_company(move.company_id)
                creds = move_with_ctx._get_zatca_credentials()
                exec_mode = move_with_ctx.zatca_exec_mode or move_with_ctx._resolve_mode()
                business_id = creds.get(f'business_id_{exec_mode}')
                if not business_id:
                    _logger.error("action=retry_poll invoice_id=%s company=%s mode=%s error=business_id_missing",
                                  move.id, creds['company_name'], exec_mode)
                    continue

                endpoint_url = f"/api/v1/invoices/{business_id}/status/{move.zatca_job_uuid}"
                if creds.get('zatca_download_xml', True):
                    endpoint_url += "?return_signed_zatcaxml=true"
                    
                poll_response = api_client.call_gateway(
                    endpoint=endpoint_url,
                    method='GET',
                    action='verify',
                    mode=exec_mode,
                )
                if poll_response.status_code == 200:
                    poll_data = poll_response.json()
                    status = poll_data.get('status')
                    if status == 'completed':
                        signed_data = poll_data.get('signedData', {})
                        invoice_type = move_with_ctx._resolve_invoice_type()
                        zatca_status = 'cleared' if invoice_type == 'standard' else 'reported'
                        self._update_invoice_after_zatca_sign(
                            move,
                            status=zatca_status,
                            zatca_hash=signed_data.get('hash', ''),
                            qr_code=signed_data.get('qrImage', ''),
                            signed_xml=signed_data.get('xml', ''),
                            error_msg=False,
                            invoice_type=invoice_type,
                            exec_mode=exec_mode,
                        )
                        move.write({'zatca_job_status': 'completed'})
                        _logger.info(
                            "action=async_completed invoice_id=%s job_uuid=%s",
                            move.id, move.zatca_job_uuid
                        )
                    elif status == 'failed':
                        error_detail = poll_data.get('error', 'Background signing job failed.')
                        move.write({
                            'zatca_job_status': 'failed',
                            'zatca_status': 'failed',
                            'zatca_error': error_detail,
                        })
                        _logger.error(
                            "action=async_failed invoice_id=%s job_uuid=%s error=%s",
                            move.id, move.zatca_job_uuid, error_detail
                        )
                    elif status in ('pending', 'processing'):
                        move.write({'zatca_job_status': 'processing'})
                        _logger.debug(
                            "action=async_processing invoice_id=%s job_uuid=%s",
                            move.id, move.zatca_job_uuid
                        )
            except Exception as e:
                _logger.error("action=retry_poll_error invoice_id=%s error=%s", move.id, str(e))

    def action_generate_payment_link(self):
        """Generates a payment link from the CloudRefit Gateway and opens it."""
        self.ensure_one()
        payload = self._build_zatca_payload(mode='live')  # Payment links use live mode payload
        
        exec_mode = payload.get('mode', 'live')
        api_client = self.env['cloudrefit.zatca.api.client']
        headers = api_client._build_headers(payload, exec_mode)

        creds = self.with_company(self.company_id)._get_zatca_credentials()
        business_id = creds.get(f'business_id_{exec_mode}')
        gateway_url = creds.get(f'gateway_url_{exec_mode}')

        if not business_id:
            raise UserError(
                'CloudRefit ZATCA Business ID is not configured for company '
                '"%s". Please configure it in Settings \u2192 CloudRefit ZATCA.'
                % creds['company_name']
            )

        url = f"{gateway_url.rstrip('/')}/api/v1/invoices/{business_id}/payment-links"
        
        _logger.info("action=generate_payment_link invoice_id=%s url=%s", self.id, url)
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception as e:
            raise UserError(f'Cannot connect to CloudRefit Gateway: {str(e)}')

        if response.status_code == 200:
            data = response.json()
            payment_url = data.get('payment_url')
            if payment_url:
                # Optionally store payment_url on the move if needed, 
                # but returning action to open URL is sufficient
                return {
                    'type': 'ir.actions.act_url',
                    'url': payment_url,
                    'target': 'new',
                }
            else:
                raise UserError('Gateway did not return a payment URL.')
        else:
            raise UserError(f"Gateway Error ({response.status_code}): {response.text}")

    def action_zatca_refresh_status(self):
        """Manually poll the gateway for the status of a pending invoice job."""
        self.ensure_one()
        if self.zatca_job_status not in ('pending', 'processing') or not self.zatca_job_uuid:
            return

        api_client = self.env['cloudrefit.zatca.api.client']
        creds = self._get_zatca_credentials()
        exec_mode = self.zatca_exec_mode or self._resolve_mode()
        business_id = creds.get(f'business_id_{exec_mode}')
        
        reload_action = {'type': 'ir.actions.client', 'tag': 'reload'}

        if not business_id:
            return self.env['cloudrefit.notification.helper']._cr_notify('danger', 'ZATCA Business ID not configured.', next_action=reload_action)

        try:
            endpoint_url = f"/api/v1/invoices/{business_id}/status/{self.zatca_job_uuid}"
            if creds.get('zatca_download_xml', True):
                endpoint_url += "?return_signed_zatcaxml=true"
                
            poll_response = api_client.call_gateway(
                endpoint=endpoint_url,
                method='GET',
                action='verify',
                mode=exec_mode,
            )
            if poll_response.status_code == 200:
                poll_data = poll_response.json()
                status = poll_data.get('status')
                if status == 'completed':
                    signed_data = poll_data.get('signedData', {})
                    invoice_type = self._resolve_invoice_type()
                    zatca_status = 'cleared' if invoice_type == 'standard' else 'reported'
                    self._update_invoice_after_zatca_sign(
                        self,
                        status=zatca_status,
                        zatca_hash=signed_data.get('hash', ''),
                        qr_code=signed_data.get('qrImage', ''),
                        signed_xml=signed_data.get('xml', ''),
                        error_msg=False,
                        invoice_type=invoice_type,
                        exec_mode=exec_mode,
                    )
                    self.write({'zatca_job_status': 'completed'})
                    return self.env['cloudrefit.notification.helper']._cr_notify('success', 'ZATCA processing completed.', next_action=reload_action)
                elif status == 'failed':
                    error_detail = poll_data.get('error', 'Background signing job failed.')
                    self.write({
                        'zatca_job_status': 'failed',
                        'zatca_status': 'failed',
                        'zatca_error': error_detail,
                    })
                    return self.env['cloudrefit.notification.helper']._cr_notify('danger', f'ZATCA processing failed: {error_detail}', next_action=reload_action)
                else:
                    self.write({'zatca_job_status': 'processing'})
                    return self.env['cloudrefit.notification.helper']._cr_notify('info', 'ZATCA job is still processing. Please try again in a few seconds.', next_action=reload_action)
            else:
                return self.env['cloudrefit.notification.helper']._cr_notify('danger', f'Failed to fetch status: HTTP {poll_response.status_code}', next_action=reload_action)
        except Exception as e:
            return self.env['cloudrefit.notification.helper']._cr_notify('danger', f'Error fetching status: {str(e)}', next_action=reload_action)

        # --- Part 2: Retry failed invoices (existing logic) ---
        failed_moves = self.search([
            ('zatca_status', '=', 'failed'),
            ('zatca_retry_count', '<', 3),
            ('move_type', 'in', ['out_invoice', 'out_refund']),
            ('state', '=', 'posted'),
        ])
        for move in failed_moves:
            # Switch to the invoice's company context for per-company credential resolution
            move_with_ctx = move.with_company(move.company_id)
            # Exponential backoff: min(2^retry_count * 60, 3600) seconds since last attempt
            backoff_seconds = min(2 ** move.zatca_retry_count * 60, 3600)
            _logger.info("action=%s invoice_id=%s retry_count=%s backoff=%ss mode=%s",
                         'retry_cron', move.id, move.zatca_retry_count, backoff_seconds, move_with_ctx._resolve_mode())
            try:
                move_with_ctx._zatca_sign_invoice()
            except (requests.RequestException, ValueError) as e:
                _logger.error("action=%s invoice_id=%s retry_count=%s mode=%s error=%s",
                              'retry_cron', move.id, move.zatca_retry_count, move_with_ctx._resolve_mode(), str(e))
                move.write({'zatca_error': str(e)})

    @api.model
    def _zatca_sync_payments(self):
        """Cron job: Poll gateway for payments of open invoices to balance ledger."""
        open_invoices = self.search([
            ('state', '=', 'posted'),
            ('payment_state', 'in', ['not_paid', 'partial']),
            ('move_type', '=', 'out_invoice'),
            ('zatca_status', 'in', ['reported', 'cleared']),
        ])
        if not open_invoices:
            return

        api_client = self.env['cloudrefit.zatca.api.client']
        for move in open_invoices:
            try:
                move_with_ctx = move.with_company(move.company_id)
                creds = move_with_ctx._get_zatca_credentials()
                exec_mode = move_with_ctx.zatca_exec_mode or move_with_ctx._resolve_mode()
                business_id = creds.get(f'business_id_{exec_mode}')
                if not business_id:
                    continue

                endpoint_url = f"/api/v1/invoices/{business_id}/sync/{move.zatca_uuid}"
                poll_response = api_client.call_gateway(
                    endpoint=endpoint_url,
                    method='GET',
                    action='verify',
                    mode=exec_mode,
                )
                if poll_response.status_code == 200:
                    data = poll_response.json()
                    due_amount = data.get('due_amount')
                    if due_amount is not None:
                        diff = move.amount_residual - due_amount
                        if diff > 0.01:
                            payment_methods = self.env['account.payment.method'].search([('payment_type', '=', 'inbound')], limit=1)
                            journal = self.env['account.journal'].search([('type', 'in', ['bank', 'cash']), ('company_id', '=', move.company_id.id)], limit=1)
                            if not journal:
                                _logger.warning("action=sync_payment_error invoice_id=%s error=no_journal_found", move.id)
                                continue
                            
                            payment_vals = {
                                'date': fields.Date.today(),
                                'amount': diff,
                                'payment_type': 'inbound',
                                'partner_type': 'customer',
                                'ref': f"Platform Auto-Sync: {move.zatca_uuid}",
                                'journal_id': journal.id,
                                'currency_id': move.currency_id.id,
                                'partner_id': move.partner_id.id,
                            }
                            if hasattr(self.env['account.payment'], 'payment_method_id') and payment_methods:
                                payment_vals['payment_method_id'] = payment_methods.id
                                
                            payment = self.env['account.payment'].create(payment_vals)
                            payment.action_post()
                            
                            # Reconcile if possible
                            lines_to_reconcile = (payment.line_ids + move.line_ids).filtered(
                                lambda l: (hasattr(l.account_id, 'account_type') and l.account_id.account_type in ('asset_receivable', 'liability_payable') and not l.reconciled)
                                or (hasattr(l.account_id, 'internal_type') and getattr(l.account_id, 'internal_type') in ('receivable', 'payable') and not l.reconciled)
                            )
                            if len(lines_to_reconcile) >= 2:
                                lines_to_reconcile.reconcile()

                            _logger.info("action=sync_payment invoice_id=%s amount=%s", move.id, diff)
            except Exception as e:
                _logger.error("action=sync_payment_error invoice_id=%s error=%s", move.id, str(e))
