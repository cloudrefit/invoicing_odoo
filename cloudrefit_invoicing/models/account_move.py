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

        # Live strictly requires credit/debit notes to reference a successfully reported parent
        if self.move_type in ('out_refund', 'in_refund'):
            if not self.reversed_entry_id:
                return False, 'Credit/Debit Note is not linked to an original invoice.'
            if self.reversed_entry_id.zatca_status not in ('reported', 'cleared'):
                return False, 'Original invoice was not successfully reported to ZATCA Live.'

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
        """Submit invoice to ZATCA Sandbox for testing.
        
        Opens the ephemeral sandbox wizard to run the test and display results
        without persisting data to the database.
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'ZATCA Sandbox Test',
            'res_model': 'cloudrefit.zatca.sandbox.test',
            'view_mode': 'form',
            'target': 'new',
            'context': dict(self.env.context, default_invoice_id=self.id),
        }

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
        Resolve invoice type with 4-layer config:
        1. Per-partner override (zatca_invoice_type on res.partner)
        2. Global default from res.config.settings
        3. Invoice-level override
        4. Auto-detect: B2B if customer has VAT, else B2C
        """
        self.ensure_one()
        partner = self.partner_id

        # Layer 1: per-partner override
        if partner.zatca_invoice_type and partner.zatca_invoice_type != 'auto':
            return partner.zatca_invoice_type

        # Layer 2: global default
        creds = self.with_company(self.company_id)._get_zatca_credentials()
        global_default = creds.get('default_invoice_type', 'auto')
        if global_default != 'auto':
            return global_default

        # Layer 3: Invoice-level override
        if self.zatca_invoice_type_override and self.zatca_invoice_type_override != 'auto':
            return self.zatca_invoice_type_override

        # Layer 4: auto-detect
        return 'standard' if partner.vat else 'simplified'

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
        if invoice_type == 'standard' and not (partner.zatca_vat or partner.vat):
            raise UserError('Customer VAT number is missing for standard (B2B) invoice. Cannot sign with ZATCA.')

        # Ensure UUID has dashes for Gateway validation
        formatted_uuid = self.zatca_uuid
        if formatted_uuid and len(formatted_uuid) == 32 and '-' not in formatted_uuid:
            formatted_uuid = str(py_uuid.UUID(formatted_uuid))

        payload = {
            'mode': mode,
            'unit_id': unit_id,
            'invoice': {
                'number': self.name,
                'date': str(self.invoice_date) + ' 12:00:00',
                'uuid': formatted_uuid,
                'type': invoice_type,
                'amount_untaxed': float(self.amount_untaxed),
                'tax_total': float(self.amount_tax),
                'amount_total': float(self.amount_total),
            },
            'lines': lines,
            'customer': {
                'name': partner.name,
                'vat': partner.zatca_vat or partner.vat or '300000000000003',
                'address': partner.street or '',
                'city': partner.city or 'Riyadh',
            },
        }

        # Credit Note Handling
        if self.move_type in ('out_refund', 'in_refund'):
            origin_move = self.reversed_entry_id
            if not origin_move:
                raise UserError('Credit Note must be linked to an original invoice (reversed_entry_id).')
            
            # Ensure origin move has a UUID deterministically
            if not origin_move.zatca_uuid:
                namespace = py_uuid.uuid5(py_uuid.NAMESPACE_OID, self.env.cr.dbname)
                origin_move.zatca_uuid = str(py_uuid.uuid5(namespace, str(origin_move.id)))
                
            payload['invoice']['origin_number'] = origin_move.zatca_uuid

        return payload

    def _zatca_sign_invoice(self, force_mode=None):
        """Call the CloudRefit Gateway to sign this invoice.

        Orchestrator that delegates to small, focused helpers.
        """
        self.ensure_one()
        payload = self._build_zatca_payload()
        if force_mode:
            payload['mode'] = force_mode

        exec_mode = payload.get('mode', 'live')
        api_client = self.env['cloudrefit.zatca.api.client']
        headers = api_client._build_headers(payload, exec_mode)

        # Notify user that submission is starting
        self._cr_notify('info', 'Submitting to ZATCA...')

        response, status_code = self._send_zatca_sign_request(payload, headers, exec_mode)

        if status_code == 201:
            self._handle_zatca_sync_response(response, self)
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

    def _handle_zatca_sync_response(self, response, move):
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
            exec_mode=move._resolve_mode(),
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
        })

        _logger.info(
            "action=async_queued invoice_id=%s job_uuid=%s",
            move.id, job_uuid
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
                exec_mode = move_with_ctx._resolve_mode()
                business_id = creds.get(f'business_id_{exec_mode}')
                if not business_id:
                    _logger.error("action=retry_poll invoice_id=%s company=%s mode=%s error=business_id_missing",
                                  move.id, creds['company_name'], exec_mode)
                    continue

                poll_response = api_client.call_gateway(
                    endpoint=f"/api/v1/invoices/{business_id}/jobs/{move.zatca_job_uuid}",
                    method='GET',
                    action='verify',
                    mode=move_with_ctx._resolve_mode(),
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
                            exec_mode=move_with_ctx._resolve_mode(),
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
                            "action=async_polling invoice_id=%s job_uuid=%s status=%s",
                            move.id, move.zatca_job_uuid, status
                        )
                else:
                    _logger.warning(
                        "action=async_poll_error invoice_id=%s job_uuid=%s http_status=%s",
                        move.id, move.zatca_job_uuid, poll_response.status_code
                    )
            except Exception as e:
                _logger.error(
                    "action=async_poll_exception invoice_id=%s job_uuid=%s error=%s",
                    move.id, move.zatca_job_uuid, str(e)
                )

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
