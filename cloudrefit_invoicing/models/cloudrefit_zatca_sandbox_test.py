import logging
import json
import time
import xml.etree.ElementTree as ET

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Polling configuration for async jobs
_SANDBOX_POLLING_TIMEOUT_SECONDS = 30
_SANDBOX_POLLING_INTERVAL = 2.0


class CloudrefitZatcaSandboxTest(models.TransientModel):
    _name = 'cloudrefit.zatca.sandbox.test'
    _description = 'ZATCA Sandbox Testing Wizard'
    _inherit = ['cloudrefit.notification.helper', 'cloudrefit.settings.helper', 'cloudrefit.zatca.credentials']

    # === Step 1: Select Invoice ===
    invoice_id = fields.Many2one(
        'account.move', required=True, string="Invoice",
        domain="[('state', 'in', ['posted', 'draft']), ('move_type', 'in', ['out_invoice', 'out_refund'])]",
    )
    mode = fields.Selection([('sandbox', 'Sandbox')], default='sandbox', readonly=True)

    # === Wizard State ===
    state = fields.Selection([
        ('select', 'Select Invoice'),
        ('testing', 'Testing...'),
        ('done', 'Done'),
    ], default='select')

    # === Test Results (ephemeral — never persisted) ===
    zatca_status = fields.Char(readonly=True)
    zatca_hash = fields.Char(readonly=True)
    zatca_qr_code = fields.Text(readonly=True)     # Base64 image data
    zatca_signed_xml = fields.Text(readonly=True)
    zatca_error = fields.Text(readonly=True)

    # -----------------------------------------------------------
    #  action_test_sandbox
    # -----------------------------------------------------------
    def action_test_sandbox(self):
        """Submit invoice to ZATCA Sandbox via the CloudRefit Gateway."""
        self.ensure_one()
        self.state = 'testing'
        self = self._reload_wizard_as_record()

        try:
            # Build payload with sandbox mode (avoids checking live Unit ID)
            payload = self.invoice_id._build_zatca_payload(mode='sandbox')

            api_client = self.env['cloudrefit.zatca.api.client']
            creds = self._get_zatca_credentials()
            self._assert_zatca_credentials(creds, mode='sandbox')
            business_id = creds.get('business_id_sandbox')

            response = api_client.call_gateway(
                endpoint=f"/api/v1/invoices/{business_id}/sign",
                method='POST',
                json_data=payload,
                action='send_invoice',
                mode='sandbox',
            )

            if response.status_code == 201:
                self._populate_from_signed_data(response.json().get('signedData', {}))
            elif response.status_code == 202:
                self._poll_async_job(response.json(), business_id)
            else:
                error_detail = response.text
                try:
                    error_detail = response.json().get('message', error_detail)
                except (json.JSONDecodeError, KeyError):
                    pass
                self.zatca_status = 'failed'
                self.zatca_error = f"[{response.status_code}] {error_detail}"

        except Exception as e:
            _logger.exception("action=%s error=%s", 'sandbox_test', str(e))
            self.zatca_status = 'failed'
            self.zatca_error = str(e)

        if self.zatca_status in ('reported', 'cleared'):
            self.invoice_id.message_post(body=f"ZATCA Sandbox Test completed. Status: {self.zatca_status}")

        self.state = 'done'
        return self._reload_wizard()

    # -----------------------------------------------------------
    #  action_print_sandbox_invoice
    # -----------------------------------------------------------
    def action_print_sandbox_invoice(self):
        """Print a dummy invoice PDF by injecting the sandbox QR code into context."""
        self.ensure_one()
        if not self.invoice_id or not self.zatca_qr_code:
            raise UserError('No QR code available to print. Please run the sandbox test first.')

        self.invoice_id.message_post(body="Printed ZATCA Sandbox dummy invoice.")
        
        # We pass the transient data via context so the report QWeb template can pick it up
        action = self.env.ref('account.account_invoices').report_action(self.invoice_id)
        action['context'] = dict(
            self.env.context,
            sandbox_qr_code=self.zatca_qr_code,
            sandbox_hash=self.zatca_hash,
            sandbox_signed_xml=self.zatca_signed_xml
        )
        return action

    # -----------------------------------------------------------
    #  _reload_wizard
    # -----------------------------------------------------------
    def _reload_wizard(self):
        """Return a window action that reopens this wizard at its current state."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
            'context': self.env.context,
        }

    # -----------------------------------------------------------
    #  _reload_wizard_as_record  (internal helper)
    # -----------------------------------------------------------
    def _reload_wizard_as_record(self):
        """After a write to self, re-fetch from DB to get the updated state.

        Used inside action_test_sandbox after setting state='testing' so the
        UI spinner shows before the blocking API call.
        """
        self.env.cr.commit()
        return self.sudo().browse(self.id)

    # -----------------------------------------------------------
    #  _populate_from_signed_data
    # -----------------------------------------------------------
    def _populate_from_signed_data(self, signed_data):
        """Map the gateway's signedData response to wizard fields."""
        self.zatca_status = signed_data.get('status', 'reported')
        self.zatca_hash = signed_data.get('hash', '')
        self.zatca_qr_code = signed_data.get('qrImage', '')
        self.zatca_signed_xml = signed_data.get('xml', '')

    # -----------------------------------------------------------
    #  _poll_async_job
    # -----------------------------------------------------------
    def _poll_async_job(self, response_data, business_id):
        """Poll for async job completion with a simple loop.

        Called when the gateway returns 202 Accepted.
        """
        job_uuid = response_data.get('job_uuid') or response_data.get('uuid')
        if not job_uuid:
            raise UserError('Gateway returned 202 Accepted but did not provide a job UUID.')

        api_client = self.env['cloudrefit.zatca.api.client']
        start_time = time.time()

        while time.time() - start_time < _SANDBOX_POLLING_TIMEOUT_SECONDS:
            time.sleep(_SANDBOX_POLLING_INTERVAL)

            poll_response = api_client.call_gateway(
                endpoint=f"/api/v1/invoices/{business_id}/status/{job_uuid}",
                method='GET',
                action='verify',
                mode='sandbox',
            )

            if poll_response.status_code == 200:
                poll_data = poll_response.json()
                status = poll_data.get('status')
                if status == 'completed':
                    signed_data = poll_data.get('signedData', {})
                    self._populate_from_signed_data(signed_data)
                    return
                elif status == 'failed':
                    error_detail = poll_data.get('error', 'Background signing job failed.')
                    raise UserError(f'Sandbox signing job failed: {error_detail}')
                elif status in ('pending', 'processing'):
                    _logger.debug(
                        "action=%s job_uuid=%s status=%s still_polling",
                        'poll_sandbox_job', job_uuid, status
                    )
                else:
                    _logger.warning(
                        "action=%s job_uuid=%s status=%s unrecognized",
                        'poll_sandbox_job', job_uuid, status
                    )
            elif poll_response.status_code in (429, 502, 503, 504):
                _logger.warning(
                    "action=%s job_uuid=%s poll_status=%s retrying",
                    'poll_sandbox_job', job_uuid, poll_response.status_code
                )
                time.sleep(5)
            else:
                _logger.error(
                    "action=%s job_uuid=%s poll_status=%s unexpected",
                    'poll_sandbox_job', job_uuid, poll_response.status_code
                )
                raise UserError(
                    f'Sandbox job polling returned unexpected status {poll_response.status_code}.'
                )

        raise UserError(
            f'Sandbox background signing job timed out after {_SANDBOX_POLLING_TIMEOUT_SECONDS} seconds.'
        )
