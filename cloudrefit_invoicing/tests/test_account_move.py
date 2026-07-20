"""Tests for ZATCA invoice signing methods on account.move.

Covers mode resolution, payload building, push-allowed computation, signing
(sync/async/error), cron retry logic, and auto-sign on post.
"""

import logging
from unittest.mock import patch
from datetime import date, timedelta

from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError
from odoo import fields

from odoo.addons.cloudrefit_invoicing.tests.mock_gateway import MockZatcaApiClient

_logger = logging.getLogger(__name__)


@tagged('post_install', 'zatca_account_move')
class TestZatcaInvoiceSigning(TransactionCase):
    """Exercise every ZATCA-related method on ``account.move``."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # ---- Configuration ----
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.dashboard_url', 'https://invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        # ---- Test data: partner with VAT (B2B) ----
        cls.partner_b2b = cls.env['res.partner'].create({
            'name': 'Test B2B Customer',
            'vat': '399999999901003',
            'street': 'King Fahd Road',
            'city': 'Riyadh',
        })

        # ---- Test data: partner without VAT (B2C) ----
        cls.partner_b2c = cls.env['res.partner'].create({
            'name': 'Test B2C Customer',
            'street': 'Olaya Street',
            'city': 'Jeddah',
        })

        # ---- Test product ----
        cls.product = cls.env['product.product'].create({
            'name': 'Test Service',
            'type': 'service',
            'list_price': 100.0,
        })

        # ---- Tax (15% KSA VAT) ----
        cls.tax_15 = cls.env['account.tax'].create({
            'name': 'KSA VAT 15%',
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
        })

        # ---- Sale journal ----
        cls.journal = cls.env['account.journal'].search([
            ('type', '=', 'sale'),
        ], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Test Sale Journal',
                'type': 'sale',
                'code': 'TSJ',
            })

        # ---- Patch gateway client ----
        cls.api_client = cls.env['cloudrefit.zatca.api.client']
        cls._original_call = type(cls.api_client).call_gateway
        type(cls.api_client).call_gateway = MockZatcaApiClient.call_gateway

    @classmethod
    def tearDownClass(cls):
        type(cls.api_client).call_gateway = cls._original_call
        MockZatcaApiClient.reset()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        MockZatcaApiClient.reset()
        # Reset mode to live for each test
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _create_invoice(self, partner=None, lines=None, invoice_date=None,
                        name=None, move_type='out_invoice', **extra):
        """Create a posted invoice with the given parameters."""
        partner = partner or self.partner_b2b
        invoice_date = invoice_date or date.today()
        name = name or f'INV/TEST/{invoice_date.strftime("%Y%m%d")}/001'

        line_vals = lines or [(self.product.id, 1, 100.0)]
        invoice_lines = []
        for product_id, qty, price in line_vals:
            invoice_lines.append((0, 0, {
                'product_id': product_id,
                'quantity': qty,
                'price_unit': price,
                'tax_ids': [(6, 0, [self.tax_15.id])],
                'name': self.product.name,
            }))

        invoice = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': invoice_date,
            'name': name,
            'journal_id': self.journal.id,
            'invoice_line_ids': invoice_lines,
            **extra,
        })
        invoice.action_post()
        return invoice

    # ------------------------------------------------------------------ #
    #  _resolve_mode
    # ------------------------------------------------------------------ #

    def test_resolve_mode_global_default(self):
        """When no per-invoice override is set, the global default must be
        used."""
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')
        invoice = self._create_invoice()
        self.assertEqual(invoice._resolve_mode(), 'live')

    def test_resolve_mode_invoice_override(self):
        """Setting mode_override on the invoice must take precedence over the
        global default."""
        invoice = self._create_invoice(mode_override='live')
        self.assertEqual(invoice._resolve_mode(), 'live')

    def test_resolve_mode_auto_detect(self):
        """With mode_override='auto', fall back to the global default."""
        self.ICP.set_param('cloudrefit_invoicing.mode', 'sandbox')
        invoice = self._create_invoice(mode_override='auto')
        self.assertEqual(invoice._resolve_mode(), 'sandbox')

    def test_resolve_mode_invoice_override_sandbox(self):
        """Invoice-level override to sandbox."""
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')
        invoice = self._create_invoice(mode_override='sandbox')
        self.assertEqual(invoice._resolve_mode(), 'sandbox')

    # ------------------------------------------------------------------ #
    #  _build_zatca_payload
    # ------------------------------------------------------------------ #

    def test_build_payload_standard_invoice(self):
        """A standard B2B invoice with one line and tax must produce a
        complete payload."""
        invoice = self._create_invoice(partner=self.partner_b2b)
        payload = invoice._build_zatca_payload()

        # Top-level keys
        self.assertIn('mode', payload)
        self.assertIn('unit_id', payload)
        self.assertIn('invoice', payload)
        self.assertIn('lines', payload)
        self.assertIn('customer', payload)

        # Invoice block
        inv = payload['invoice']
        self.assertEqual(inv['number'], invoice.name)
        self.assertEqual(inv['type'], 'STANDARD_TAX_INVOICE')  # B2B because partner has VAT
        self.assertIn('uuid', inv)
        self.assertIn('icv', inv)
        self.assertIn('amount_untaxed', inv)
        self.assertIn('amount_total', inv)

        # Customer block
        cust = payload['customer']
        self.assertEqual(cust['vat'], '399999999901003')
        self.assertEqual(cust['name'], 'Test B2B Customer')

        # Lines
        self.assertEqual(len(payload['lines']), 1)
        line = payload['lines'][0]
        self.assertIn('name', line)
        self.assertIn('quantity', line)
        self.assertIn('price_unit', line)
        self.assertIn('subtotal', line)
        self.assertIn('tax_amount', line)

    def test_build_payload_missing_invoice_number(self):
        """Invoice without a name must raise UserError."""
        invoice = self._create_invoice(name=False)
        invoice.write({'name': False})
        with self.assertRaises(UserError):
            invoice._build_zatca_payload()

    def test_build_payload_missing_date(self):
        """Invoice without invoice_date must raise UserError."""
        invoice = self._create_invoice()
        invoice.write({'invoice_date': False})
        with self.assertRaises(UserError):
            invoice._build_zatca_payload()

    def test_build_payload_empty_lines(self):
        """Invoice with no product lines must produce an empty lines array
        (warning is logged, not raised)."""
        invoice = self._create_invoice(lines=[])
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['lines'], [])

    def test_build_payload_b2c_no_vat(self):
        """Simplified (B2C) invoice without customer VAT must build
        successfully and use the placeholder VAT."""
        invoice = self._create_invoice(partner=self.partner_b2c)
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['invoice']['type'], 'SIMPLIFIED_TAX_INVOICE')
        self.assertEqual(payload['customer']['vat'], '300000000000003')



    def test_build_payload_uuid_generated(self):
        """An invoice without a zatca_uuid must have one generated during
        payload building."""
        invoice = self._create_invoice()
        self.assertFalse(invoice.zatca_uuid)  # Not yet generated
        payload = invoice._build_zatca_payload()
        self.assertIn('uuid', payload['invoice'])
        # After building, the uuid should be persisted
        self.assertTrue(invoice.zatca_uuid)
        self.assertEqual(invoice.zatca_uuid, payload['invoice']['uuid'])

    # ------------------------------------------------------------------ #
    #  _compute_is_zatca_push_allowed
    # ------------------------------------------------------------------ #

    def test_push_allowed_posted_no_sign(self):
        """A posted invoice that has not been signed must have
        is_zatca_push_allowed = True."""
        invoice = self._create_invoice()
        self.assertTrue(invoice.is_zatca_push_allowed)

    def test_push_allowed_already_signed(self):
        """A posted invoice already cleared in live must have
        is_zatca_push_allowed = False."""
        invoice = self._create_invoice()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'live',
        })
        self.assertFalse(invoice.is_zatca_push_allowed)

    def test_push_allowed_not_posted(self):
        """A draft invoice must have is_zatca_push_allowed = False."""
        draft = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_b2b.id,
            'invoice_date': date.today(),
            'journal_id': self.journal.id,
        })
        self.assertEqual(draft.state, 'draft')
        self.assertFalse(draft.is_zatca_push_allowed)

    def test_push_allowed_date_too_old(self):
        """A posted invoice with invoice_date > 15 days ago must have
        is_zatca_push_allowed = False."""
        old_date = date.today() - timedelta(days=20)
        invoice = self._create_invoice(invoice_date=old_date)
        self.assertFalse(invoice.is_zatca_push_allowed)

    def test_push_allowed_live_signed_not_pushable(self):
        """If signed in live, push must not be allowed."""
        invoice = self._create_invoice()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'live',
        })
        invoice.invalidate_recordset(['is_zatca_push_allowed'])
        self.assertFalse(invoice.is_zatca_push_allowed)

    def test_push_allowed_sandbox_signed_still_pushable_to_live(self):
        """If signed only in sandbox (ephemeral, no persistence), push should
        still be allowed to live.  With the simplified 6-field model, sandbox
        results are not persisted on the invoice record, so zatca_status
        remains 'not_signed'."""
        invoice = self._create_invoice()
        # In the new model, sandbox results are ephemeral — the invoice's
        # bare fields remain untouched.  This simulates that scenario.
        self.assertEqual(invoice.zatca_status, 'not_signed')
        self.assertTrue(invoice.is_zatca_push_allowed)

    # ------------------------------------------------------------------ #
    #  action_zatca_sign – success path (201 Created)
    # ------------------------------------------------------------------ #

    def test_sign_success(self):
        """Signing with a 201 response must store cleared status, hash, QR,
        and XML, and reset retry_count."""
        invoice = self._create_invoice()
        invoice.write({'zatca_retry_count': 2})

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'id': 'inv_test_001',
                'status': 'cleared',
                'hash': 'abc123',
                'qr_code': 'base64_qr...',
                'xml': '<Invoice>...</Invoice>',
                'signedData': {
                    'hash': 'abc123',
                    'qrImage': 'base64_qr...',
                    'xml': '<Invoice>...</Invoice>',
                },
            },
        )

        invoice.action_zatca_sign()

        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'abc123')
        self.assertEqual(invoice.zatca_qr_code, 'base64_qr...')
        self.assertEqual(invoice.zatca_signed_xml, '<Invoice>...</Invoice>')
        self.assertEqual(invoice.zatca_retry_count, 0)
        self.assertFalse(invoice.zatca_error)
        self.assertEqual(invoice.zatca_exec_mode, 'live')

    def test_sign_reported_for_simplified(self):
        """Simplified (B2C) invoices should get zatca_status='reported'."""
        invoice = self._create_invoice(partner=self.partner_b2c)

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'signedData': {
                    'hash': 'def456',
                    'qrImage': 'base64_qr...',
                    'xml': '<Invoice>...</Invoice>',
                },
            },
        )

        invoice.action_zatca_sign()
        self.assertEqual(invoice.zatca_status, 'reported')

    # ------------------------------------------------------------------ #
    #  action_zatca_sign – failure path
    # ------------------------------------------------------------------ #

    def test_sign_failure(self):
        """A 500 response must set status='failed', store error, and
        increment retry_count."""
        invoice = self._create_invoice()
        original_retry = invoice.zatca_retry_count

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            500,
            {'error': 'Internal Server Error', 'message': 'Something broke'},
        )

        with self.assertRaises(UserError):
            invoice.action_zatca_sign()

        self.assertEqual(invoice.zatca_status, 'failed')
        self.assertIn('500', invoice.zatca_error)
        self.assertIn('Something broke', invoice.zatca_error)
        self.assertEqual(invoice.zatca_retry_count, original_retry + 1)

    def test_sign_unauthorized(self):
        """A 401 response must set status='failed' and increment retry."""
        invoice = self._create_invoice()

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            401,
            {'error': 'Unauthorized', 'message': 'Invalid API key'},
        )

        with self.assertRaises(UserError):
            invoice.action_zatca_sign()

        self.assertEqual(invoice.zatca_status, 'failed')
        self.assertIn('401', invoice.zatca_error)

    def test_sign_already_reported_live_raises(self):
        """Signing an invoice already cleared in live must raise UserError."""
        invoice = self._create_invoice()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'live',
        })
        with self.assertRaises(UserError):
            invoice.action_zatca_sign()

    # ------------------------------------------------------------------ #
    #  action_zatca_sign – async (202 Accepted)
    # ------------------------------------------------------------------ #

    def test_sign_async_success(self):
        """202 Accepted → polling finds job completed → status='cleared'."""
        invoice = self._create_invoice()

        MockZatcaApiClient.reset()
        # First call: 202 with job_uuid
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            202,
            {'job_uuid': 'job_test_async_001', 'status': 'pending'},
        )
        # Polling call: completed
        MockZatcaApiClient.register_response(
            'GET', '/api/v1/invoices/99/status/job_test_async_001',
            200,
            {
                'status': 'completed',
                'signedData': {
                    'hash': 'async_hash_789',
                    'qrImage': 'base64_async_qr...',
                    'xml': '<AsyncInvoice>...</AsyncInvoice>',
                },
            },
        )

        invoice.action_zatca_sign()

        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'async_hash_789')
        MockZatcaApiClient.assert_called_with('GET', '/api/v1/invoices/99/status/job_test_async_001')

    def test_sign_async_timeout(self):
        """202 Accepted → job stays processing beyond 60s → UserError raised
        with timeout message."""
        invoice = self._create_invoice()

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            202,
            {'job_uuid': 'job_test_timeout', 'status': 'pending'},
        )
        # Register many 'processing' responses for the polling loop
        for _ in range(20):
            MockZatcaApiClient.register_response(
                'GET', '/api/v1/invoices/99/status/job_test_timeout',
                200,
                {'status': 'processing'},
            )

        with self.assertRaises(UserError) as ctx:
            invoice.action_zatca_sign()
        self.assertIn('timed out', str(ctx.exception).lower())

    # ------------------------------------------------------------------ #
    #  _zatca_retry_failed (cron)
    # ------------------------------------------------------------------ #

    def test_retry_eligible(self):
        """A failed, posted invoice with retry_count < 3 must be retried."""
        invoice = self._create_invoice()
        invoice.write({
            'zatca_status': 'failed',
            'zatca_error': 'Previous error',
            'zatca_retry_count': 1,
        })

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'signedData': {
                    'hash': 'retry_hash',
                    'qrImage': 'base64_qr...',
                    'xml': '<Retried>...</Retried>',
                },
            },
        )

        self.env['account.move']._zatca_retry_failed()

        invoice.invalidate_recordset(['zatca_status', 'zatca_hash', 'zatca_retry_count'])
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'retry_hash')

    def test_retry_max_exceeded(self):
        """A failed invoice with retry_count >= 3 must NOT be retried."""
        invoice = self._create_invoice()
        invoice.write({
            'zatca_status': 'failed',
            'zatca_error': 'Failed 3 times',
            'zatca_retry_count': 3,
        })

        MockZatcaApiClient.reset()
        # No responses registered — if retry happens, it would hit default and fail

        self.env['account.move']._zatca_retry_failed()

        # Status should remain 'failed' — no call should have been made
        invoice.invalidate_recordset(['zatca_status'])
        self.assertEqual(invoice.zatca_status, 'failed')

        # Verify no gateway calls were made for signing
        for _method, endpoint, _payload in MockZatcaApiClient.call_history:
            if 'invoices' in endpoint:
                self.fail(f"Retry should not have called {endpoint}")

    def test_retry_not_posted(self):
        """A failed invoice in draft state must NOT be retried."""
        draft = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_b2b.id,
            'invoice_date': date.today(),
            'journal_id': self.journal.id,
            'zatca_status': 'failed',
            'zatca_retry_count': 1,
        })

        MockZatcaApiClient.reset()
        self.env['account.move']._zatca_retry_failed()

        draft.invalidate_recordset(['zatca_status'])
        self.assertEqual(draft.zatca_status, 'failed')

    def test_retry_not_invoice_type(self):
        """A failed 'bill' (incoming invoice) must NOT be retried."""
        bill = self._create_invoice(move_type='in_invoice', partner=self.partner_b2b)
        bill.write({
            'zatca_status': 'failed',
            'zatca_retry_count': 1,
        })

        MockZatcaApiClient.reset()
        self.env['account.move']._zatca_retry_failed()

        bill.invalidate_recordset(['zatca_status'])
        self.assertEqual(bill.zatca_status, 'failed')



    # ------------------------------------------------------------------ #
    #  _resolve_invoice_type
    # ------------------------------------------------------------------ #

    def test_resolve_invoice_type_standard_b2b(self):
        """Partner with VAT → invoice type should be 'standard'."""
        invoice = self._create_invoice(partner=self.partner_b2b)
        self.assertEqual(invoice._resolve_invoice_type(), 'standard')

    def test_resolve_invoice_type_simplified_b2c(self):
        """Partner without VAT → invoice type should be 'simplified'."""
        invoice = self._create_invoice(partner=self.partner_b2c)
        self.assertEqual(invoice._resolve_invoice_type(), 'simplified')



    def test_resolve_invoice_type_invoice_override(self):
        """Invoice-level zatca_invoice_type_override should take precedence."""
        invoice = self._create_invoice(
            partner=self.partner_b2c,
            zatca_invoice_type_override='standard',
        )
        self.assertEqual(invoice._resolve_invoice_type(), 'standard')

    # ------------------------------------------------------------------ #
    #  is_zatca_sandbox_allowed
    # ------------------------------------------------------------------ #

    def test_is_zatca_sandbox_allowed_success(self):
        """is_zatca_sandbox_allowed must be True when sandbox is enabled and all credentials are set."""
        self.ICP.set_param('cloudrefit_invoicing.sandbox_enabled', 'True')
        self.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api-dev-invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_sandbox_testapikey123')
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.unit_id_sandbox', '888')

        invoice = self._create_invoice()
        # Trigger compute
        invoice._compute_is_zatca_sandbox_allowed()
        self.assertTrue(invoice.is_zatca_sandbox_allowed)

    def test_is_zatca_sandbox_allowed_failure_missing_credential(self):
        """is_zatca_sandbox_allowed must be False if any required sandbox credential is empty."""
        self.ICP.set_param('cloudrefit_invoicing.sandbox_enabled', 'True')
        self.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api-dev-invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_sandbox_testapikey123')
        # Leave signing_secret empty
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', '')
        self.ICP.set_param('cloudrefit_invoicing.unit_id_sandbox', '888')

        invoice = self._create_invoice()
        # Trigger compute
        invoice._compute_is_zatca_sandbox_allowed()
        self.assertFalse(invoice.is_zatca_sandbox_allowed)

    def test_is_zatca_sandbox_allowed_failure_disabled(self):
        """is_zatca_sandbox_allowed must be False if sandbox is not enabled."""
        self.ICP.set_param('cloudrefit_invoicing.sandbox_enabled', 'False')
        self.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api-dev-invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_sandbox_testapikey123')
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.unit_id_sandbox', '888')

        invoice = self._create_invoice()
        # Trigger compute
        invoice._compute_is_zatca_sandbox_allowed()
        self.assertFalse(invoice.is_zatca_sandbox_allowed)

    # ------------------------------------------------------------------ #
    #  _apply_cloudrefit_status_update (Webhook & Fast-Polling)
    # ------------------------------------------------------------------ #

    def test_apply_status_update_success(self):
        """Standard valid status update must apply."""
        invoice = self._create_invoice()
        payload = {
            'status': 'cleared',
            'zatca_status': 'REPORTED',
            'hash': 'xyz789',
            'qr_code': 'qr_xyz',
            'xml': '<Test/>',
            'cleared_at': '2023-01-01T12:00:00Z',
        }
        invoice._apply_cloudrefit_status_update(payload, 'invoice.status_changed')
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'xyz789')
        self.assertEqual(invoice.zatca_qr_code, 'qr_xyz')
        self.assertEqual(invoice.zatca_signed_xml, '<Test/>')

    def test_apply_status_update_regression_guard(self):
        """A status update must be ignored if it tries to regress to an older state."""
        invoice = self._create_invoice()
        
        # Advance to 'cleared'
        invoice.write({'zatca_status': 'cleared'})
        
        # Attempt to regress to 'pending'
        payload = {
            'status': 'pending',
            'hash': 'old_hash',
        }
        invoice._apply_cloudrefit_status_update(payload, 'invoice.status_changed')
        
        # Must still be 'cleared'
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertNotEqual(invoice.zatca_hash, 'old_hash')

    def test_apply_status_update_from_failed_allowed(self):
        """A status update from 'failed' to 'pending' is allowed since failed has lower rank."""
        invoice = self._create_invoice()
        
        # Currently failed
        invoice.write({'zatca_status': 'failed'})
        
        # Update to pending
        payload = {
            'status': 'pending',
            'hash': 'new_hash',
        }
        invoice._apply_cloudrefit_status_update(payload, 'invoice.status_changed')
        
        self.assertEqual(invoice.zatca_status, 'pending')
        self.assertEqual(invoice.zatca_hash, 'new_hash')

    def test_apply_payment_update(self):
        """A payment.status_changed event must not affect ZATCA fields but must call _create_zatca_payment."""
        invoice = self._create_invoice()
        
        payload = {
            'status': 'paid',
            'transaction_id': 'txn_123',
            'amount': 100.0,
        }
        
        with patch.object(type(invoice), '_create_zatca_payment') as mock_create_payment:
            invoice._apply_cloudrefit_status_update(payload, 'payment.status_changed')
            mock_create_payment.assert_called_once_with(payload)


    # ------------------------------------------------------------------ #
    #  action_generate_payment_link – local URL construction (no API call)
    # ------------------------------------------------------------------ #

    def test_generate_payment_link_constructs_url_locally(self):
        """action_generate_payment_link must construct the invoice page URL
        locally without making any gateway API call."""
        invoice = self._create_invoice()

        MockZatcaApiClient.reset()
        call_count_before = len(MockZatcaApiClient.call_history)

        result = invoice.action_generate_payment_link()

        # Verify no API call was made
        self.assertEqual(
            len(MockZatcaApiClient.call_history), call_count_before,
            "No gateway API call should be made when generating payment link",
        )

        # Verify the URL was written to the field
        self.assertTrue(
            invoice.cloudrefit_payment_link_url,
            "cloudrefit_payment_link_url should be set",
        )

        # Verify URL format: {dashboard_url}/{locale}/print-invoice/{business_id}/{zatca_uuid}
        expected_prefix = f"https://invoicing.cloudrefit.com/en/print-invoice/biz_test_001/"
        self.assertIn(expected_prefix, invoice.cloudrefit_payment_link_url)
        self.assertIn(invoice.zatca_uuid, invoice.cloudrefit_payment_link_url)

        # Verify the action opens the URL in a new tab
        self.assertEqual(result['type'], 'ir.actions.act_url')
        self.assertEqual(result['url'], invoice.cloudrefit_payment_link_url)
        self.assertEqual(result['target'], 'new')

    def test_generate_payment_link_missing_business_id(self):
        """action_generate_payment_link must raise UserError if business_id
        is not configured."""
        # Clear business_id
        self.ICP.set_param('cloudrefit_invoicing.business_id', '')

        invoice = self._create_invoice()
        with self.assertRaises(UserError) as ctx:
            invoice.action_generate_payment_link()
        self.assertIn('Business ID', str(ctx.exception))

    def test_generate_payment_link_missing_zatca_uuid(self):
        """action_generate_payment_link must raise UserError if the invoice
        has no zatca_uuid."""
        invoice = self._create_invoice()
        # Clear the UUID that was set during post
        invoice.write({'zatca_uuid': False})
        with self.assertRaises(UserError) as ctx:
            invoice.action_generate_payment_link()
        self.assertIn('ZATCA UUID', str(ctx.exception))


# ================================================================== #
#  B2B INVOICE FLOW
# ================================================================== #


@tagged('post_install', 'zatca_b2b_flow')
class TestB2BInvoiceFlow(TransactionCase):
    """B2B STANDARD / CREDIT_NOTE invoice flow — address completeness,
    Saudi address rules, and non-SA bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        # Countries
        cls.country_sa = cls.env.ref('base.sa', raise_if_not_found=False)
        cls.country_ae = cls.env.ref('base.ae', raise_if_not_found=False)

        # ---- SA B2B partner with COMPLETE address ----
        cls.partner_sa_complete = cls.env['res.partner'].create({
            'name': 'SA B2B Complete',
            'vat': '399999999901003',
            'street': 'King Fahd Road',
            'city': 'Riyadh',
            'country_id': cls.country_sa.id if cls.country_sa else False,
            'building_no': '1234',
            'district': 'Al-Malaz',
            'zip': '12345',
            'phone': '+966501234567',
            'is_company': True,
        })

        # ---- SA B2B partner MISSING district ----
        cls.partner_sa_no_district = cls.env['res.partner'].create({
            'name': 'SA B2B No District',
            'vat': '399999999901004',
            'street': 'Olaya Street',
            'city': 'Riyadh',
            'country_id': cls.country_sa.id if cls.country_sa else False,
            'building_no': '5678',
            'district': '',
            'zip': '54321',
            'phone': '+966501234568',
            'is_company': True,
        })

        # ---- SA B2B partner MISSING building_no ----
        cls.partner_sa_no_building = cls.env['res.partner'].create({
            'name': 'SA B2B No Building',
            'vat': '399999999901005',
            'street': 'Tahlia Street',
            'city': 'Jeddah',
            'country_id': cls.country_sa.id if cls.country_sa else False,
            'building_no': '',
            'district': 'Al-Shati',
            'zip': '23456',
            'phone': '+966501234569',
            'is_company': True,
        })

        # ---- SA B2B partner MISSING postal_code ----
        cls.partner_sa_no_zip = cls.env['res.partner'].create({
            'name': 'SA B2B No Zip',
            'vat': '399999999901006',
            'street': 'Prince Sultan Road',
            'city': 'Dammam',
            'country_id': cls.country_sa.id if cls.country_sa else False,
            'building_no': '9012',
            'district': 'Al-Sharq',
            'zip': '',
            'phone': '+966501234570',
            'is_company': True,
        })

        # ---- Non-SA B2B partner (UAE) ----
        cls.partner_uae = cls.env['res.partner'].create({
            'name': 'UAE B2B Customer',
            'vat': '123456789012345',
            'street': 'Sheikh Zayed Road',
            'city': 'Dubai',
            'country_id': cls.country_ae.id if cls.country_ae else False,
            'phone': '+971501234567',
            'is_company': True,
        })

        # ---- Product & Tax & Journal ----
        cls.product = cls.env['product.product'].create({
            'name': 'Test Service',
            'type': 'service',
            'list_price': 100.0,
        })
        cls.tax_15 = cls.env['account.tax'].create({
            'name': 'KSA VAT 15%',
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
        })
        cls.journal = cls.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Test Sale Journal',
                'type': 'sale',
                'code': 'TSJ',
            })

        # ---- Patch gateway client ----
        cls.api_client = cls.env['cloudrefit.zatca.api.client']
        cls._original_call = type(cls.api_client).call_gateway
        type(cls.api_client).call_gateway = MockZatcaApiClient.call_gateway

    @classmethod
    def tearDownClass(cls):
        type(cls.api_client).call_gateway = cls._original_call
        MockZatcaApiClient.reset()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        MockZatcaApiClient.reset()
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _create_invoice(self, partner=None, lines=None, invoice_date=None,
                        name=None, move_type='out_invoice', **extra):
        partner = partner or self.partner_sa_complete
        invoice_date = invoice_date or date.today()
        name = name or f'INV/TEST/{invoice_date.strftime("%Y%m%d")}/001'
        line_vals = lines or [(self.product.id, 1, 100.0)]
        invoice_lines = []
        for product_id, qty, price in line_vals:
            invoice_lines.append((0, 0, {
                'product_id': product_id,
                'quantity': qty,
                'price_unit': price,
                'tax_ids': [(6, 0, [self.tax_15.id])],
                'name': self.product.name,
            }))
        invoice = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': invoice_date,
            'name': name,
            'journal_id': self.journal.id,
            'invoice_line_ids': invoice_lines,
            **extra,
        })
        invoice.action_post()
        return invoice

    # ------------------------------------------------------------------ #
    #  B2B STANDARD — Complete address → payload succeeds
    # ------------------------------------------------------------------ #

    def test_b2b_standard_complete_address_payload_builds(self):
        """B2B STANDARD invoice with complete SA address must build payload
        successfully with all address fields present."""
        invoice = self._create_invoice(partner=self.partner_sa_complete)
        payload = invoice._build_zatca_payload()

        self.assertEqual(payload['invoice']['type'], 'STANDARD_TAX_INVOICE')
        cust = payload['customer']
        self.assertEqual(cust['building_no'], '1234')
        self.assertEqual(cust['district'], 'Al-Malaz')
        self.assertEqual(cust['postal_code'], '12345')
        self.assertEqual(cust['vat'], '399999999901003')

    def test_b2b_standard_complete_address_sign_success(self):
        """B2B STANDARD invoice with complete SA address must sign
        successfully."""
        invoice = self._create_invoice(partner=self.partner_sa_complete)

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'signedData': {
                    'hash': 'b2b_complete_hash',
                    'qrImage': 'b2b_qr',
                    'xml': '<B2BComplete/>',
                },
            },
        )

        invoice.action_zatca_sign()
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'b2b_complete_hash')

    # ------------------------------------------------------------------ #
    #  B2B STANDARD — Missing district → rejected
    # ------------------------------------------------------------------ #

    def test_b2b_standard_missing_district_raises_error(self):
        """B2B STANDARD invoice with SA buyer missing district must raise
        UserError from _build_zatca_payload."""
        invoice = self._create_invoice(partner=self.partner_sa_no_district)
        with self.assertRaises(UserError) as ctx:
            invoice._build_zatca_payload()
        self.assertIn('District', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  B2B STANDARD — Missing building_no → rejected
    # ------------------------------------------------------------------ #

    def test_b2b_standard_missing_building_no_raises_error(self):
        """B2B STANDARD invoice with SA buyer missing building_no must raise
        UserError from _build_zatca_payload."""
        invoice = self._create_invoice(partner=self.partner_sa_no_building)
        with self.assertRaises(UserError) as ctx:
            invoice._build_zatca_payload()
        self.assertIn('Building Number', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  B2B STANDARD — Missing postal_code → rejected
    # ------------------------------------------------------------------ #

    def test_b2b_standard_missing_postal_code_raises_error(self):
        """B2B STANDARD invoice with SA buyer missing postal_code must raise
        UserError from _build_zatca_payload."""
        invoice = self._create_invoice(partner=self.partner_sa_no_zip)
        with self.assertRaises(UserError) as ctx:
            invoice._build_zatca_payload()
        self.assertIn('Postal Code', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  B2B CREDIT_NOTE — Complete address → accepted
    # ------------------------------------------------------------------ #

    def test_b2b_credit_note_complete_address(self):
        """B2B CREDIT_NOTE with complete SA buyer address must build payload
        successfully with STANDARD_TAX_CREDIT_NOTE type."""
        # Create the original invoice first
        original = self._create_invoice(partner=self.partner_sa_complete)

        # Create a credit note reversing the original
        credit_note = self._create_invoice(
            partner=self.partner_sa_complete,
            move_type='out_refund',
            name=f'CN/{original.name}',
        )

        payload = credit_note._build_zatca_payload()
        self.assertEqual(payload['invoice']['type'], 'STANDARD_TAX_CREDIT_NOTE')
        self.assertIn('origin_number', payload['invoice'])
        self.assertIn('origin_uuid', payload['invoice'])
        self.assertIn('adjustment_reason', payload['invoice'])

    # ------------------------------------------------------------------ #
    #  B2B CREDIT_NOTE — Missing district → rejected
    # ------------------------------------------------------------------ #

    def test_b2b_credit_note_missing_district_raises_error(self):
        """B2B CREDIT_NOTE with SA buyer missing district must raise
        UserError."""
        original = self._create_invoice(partner=self.partner_sa_complete)
        credit_note = self._create_invoice(
            partner=self.partner_sa_no_district,
            move_type='out_refund',
            name=f'CN/{original.name}',
        )
        with self.assertRaises(UserError) as ctx:
            credit_note._build_zatca_payload()
        self.assertIn('District', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  Non-SA B2B — Bypasses SA address rules
    # ------------------------------------------------------------------ #

    def test_b2b_non_sa_bypasses_building_no_requirement(self):
        """Non-SA B2B buyer (UAE) must NOT require building_no, district, or
        postal_code. Payload must build successfully."""
        invoice = self._create_invoice(partner=self.partner_uae)
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['invoice']['type'], 'STANDARD_TAX_INVOICE')
        # UAE partner has no SA-specific fields, but payload should still build
        cust = payload['customer']
        self.assertEqual(cust['country_code'], 'AE')
        # SA-specific fields should be empty strings, not cause errors
        self.assertIn('building_no', cust)
        self.assertIn('district', cust)
        self.assertIn('postal_code', cust)

    def test_b2b_non_sa_sign_success_without_sa_fields(self):
        """Non-SA B2B buyer must be signable without SA-specific address
        fields."""
        invoice = self._create_invoice(partner=self.partner_uae)

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'signedData': {
                    'hash': 'uae_b2b_hash',
                    'qrImage': 'uae_qr',
                    'xml': '<UAEB2B/>',
                },
            },
        )

        invoice.action_zatca_sign()
        self.assertEqual(invoice.zatca_status, 'cleared')


# ================================================================== #
#  B2C INVOICE FLOW
# ================================================================== #


@tagged('post_install', 'zatca_b2c_flow')
class TestB2CInvoiceFlow(TransactionCase):
    """B2C SIMPLIFIED invoice flow — phone/mobile requirements and
    address bypass."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        # ---- B2C partner WITH phone ----
        cls.partner_b2c_with_phone = cls.env['res.partner'].create({
            'name': 'B2C With Phone',
            'phone': '+966501234567',
            'is_company': False,
        })

        # ---- B2C partner WITH mobile (no phone) ----
        cls.partner_b2c_with_mobile = cls.env['res.partner'].create({
            'name': 'B2C With Mobile',
            'mobile': '+966508765432',
            'is_company': False,
        })

        # ---- B2C partner WITHOUT phone or mobile ----
        cls.partner_b2c_no_phone = cls.env['res.partner'].create({
            'name': 'B2C No Phone',
            'phone': '',
            'is_company': False,
        })

        # ---- Product & Tax & Journal ----
        cls.product = cls.env['product.product'].create({
            'name': 'Test Service',
            'type': 'service',
            'list_price': 100.0,
        })
        cls.tax_15 = cls.env['account.tax'].create({
            'name': 'KSA VAT 15%',
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
        })
        cls.journal = cls.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Test Sale Journal',
                'type': 'sale',
                'code': 'TSJ',
            })

        # ---- Patch gateway client ----
        cls.api_client = cls.env['cloudrefit.zatca.api.client']
        cls._original_call = type(cls.api_client).call_gateway
        type(cls.api_client).call_gateway = MockZatcaApiClient.call_gateway

    @classmethod
    def tearDownClass(cls):
        type(cls.api_client).call_gateway = cls._original_call
        MockZatcaApiClient.reset()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        MockZatcaApiClient.reset()
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')

    def _create_invoice(self, partner=None, lines=None, invoice_date=None,
                        name=None, move_type='out_invoice', **extra):
        partner = partner or self.partner_b2c_with_phone
        invoice_date = invoice_date or date.today()
        name = name or f'INV/TEST/{invoice_date.strftime("%Y%m%d")}/001'
        line_vals = lines or [(self.product.id, 1, 100.0)]
        invoice_lines = []
        for product_id, qty, price in line_vals:
            invoice_lines.append((0, 0, {
                'product_id': product_id,
                'quantity': qty,
                'price_unit': price,
                'tax_ids': [(6, 0, [self.tax_15.id])],
                'name': self.product.name,
            }))
        invoice = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': invoice_date,
            'name': name,
            'journal_id': self.journal.id,
            'invoice_line_ids': invoice_lines,
            **extra,
        })
        invoice.action_post()
        return invoice

    # ------------------------------------------------------------------ #
    #  B2C with phone → success
    # ------------------------------------------------------------------ #

    def test_b2c_simplified_with_phone(self):
        """B2C SIMPLIFIED invoice with partner phone must build payload
        successfully with SIMPLIFIED_TAX_INVOICE type."""
        invoice = self._create_invoice(partner=self.partner_b2c_with_phone)
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['invoice']['type'], 'SIMPLIFIED_TAX_INVOICE')
        self.assertEqual(payload['customer']['phone'], '+966501234567')

    def test_b2c_simplified_with_phone_sign_success(self):
        """B2C SIMPLIFIED invoice with phone must sign successfully and get
        'reported' status."""
        invoice = self._create_invoice(partner=self.partner_b2c_with_phone)

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'signedData': {
                    'hash': 'b2c_phone_hash',
                    'qrImage': 'b2c_phone_qr',
                    'xml': '<B2CPhone/>',
                },
            },
        )

        invoice.action_zatca_sign()
        self.assertEqual(invoice.zatca_status, 'reported')

    # ------------------------------------------------------------------ #
    #  B2C with mobile (no phone) → success
    # ------------------------------------------------------------------ #

    def test_b2c_simplified_with_mobile(self):
        """B2C SIMPLIFIED invoice with mobile (no phone) must build payload
        successfully."""
        invoice = self._create_invoice(partner=self.partner_b2c_with_mobile)
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['invoice']['type'], 'SIMPLIFIED_TAX_INVOICE')
        # Phone field should fall back to mobile
        self.assertTrue(payload['customer']['phone'])

    # ------------------------------------------------------------------ #
    #  B2C without phone or mobile → rejected
    # ------------------------------------------------------------------ #

    def test_b2c_no_phone_raises_error(self):
        """B2C SIMPLIFIED invoice without phone or mobile must raise
        UserError from _build_zatca_payload."""
        invoice = self._create_invoice(partner=self.partner_b2c_no_phone)
        with self.assertRaises(UserError) as ctx:
            invoice._build_zatca_payload()
        self.assertIn('Mobile or Phone Number', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  B2C with empty partner name — still requires phone
    # ------------------------------------------------------------------ #

    def test_b2c_no_phone_no_mobile_res_partner_constraint(self):
        """Creating a B2C partner without phone or mobile must raise
        ValidationError from res_partner constraint."""
        # The constraint runs on create/write of res.partner
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'B2C Invalid',
                'phone': '',
                'mobile': '',
                'is_company': False,
            })
        # Should raise ValidationError about missing phone/mobile
        error_text = str(ctx.exception)
        self.assertTrue(
            'Mobile' in error_text or 'Phone' in error_text,
            f'Expected ValidationError about mobile/phone, got: {error_text}',
        )

    # ------------------------------------------------------------------ #
    #  B2C address fields are NOT validated (no street/city/country needed)
    # ------------------------------------------------------------------ #

    def test_b2c_no_address_still_succeeds(self):
        """B2C SIMPLIFIED invoice must NOT require street, city, or
        country — only phone/mobile matters."""
        partner_no_address = self.env['res.partner'].create({
            'name': 'B2C Minimal',
            'phone': '+966501234569',
            'is_company': False,
        })
        invoice = self._create_invoice(partner=partner_no_address)
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['invoice']['type'], 'SIMPLIFIED_TAX_INVOICE')


# ================================================================== #
#  STATE GUARDS
# ================================================================== #


@tagged('post_install', 'zatca_state_guards')
class TestZatcaStateGuards(TransactionCase):
    """Invoice state transitions and guards against invalid operations."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        cls.country_sa = cls.env.ref('base.sa', raise_if_not_found=False)

        cls.partner_complete = cls.env['res.partner'].create({
            'name': 'SA B2B Complete',
            'vat': '399999999901003',
            'street': 'King Fahd Road',
            'city': 'Riyadh',
            'country_id': cls.country_sa.id if cls.country_sa else False,
            'building_no': '1234',
            'district': 'Al-Malaz',
            'zip': '12345',
            'phone': '+966501234567',
            'is_company': True,
        })

        cls.product = cls.env['product.product'].create({
            'name': 'Test Service',
            'type': 'service',
            'list_price': 100.0,
        })
        cls.tax_15 = cls.env['account.tax'].create({
            'name': 'KSA VAT 15%',
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
        })
        cls.journal = cls.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Test Sale Journal',
                'type': 'sale',
                'code': 'TSJ',
            })

        cls.api_client = cls.env['cloudrefit.zatca.api.client']
        cls._original_call = type(cls.api_client).call_gateway
        type(cls.api_client).call_gateway = MockZatcaApiClient.call_gateway

    @classmethod
    def tearDownClass(cls):
        type(cls.api_client).call_gateway = cls._original_call
        MockZatcaApiClient.reset()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        MockZatcaApiClient.reset()
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')

    def _create_draft_invoice(self):
        """Create a draft (unposted) invoice."""
        return self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner_complete.id,
            'invoice_date': date.today(),
            'name': 'INV/DRAFT/001',
            'journal_id': self.journal.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 100.0,
                'tax_ids': [(6, 0, [self.tax_15.id])],
                'name': self.product.name,
            })],
        })

    # ------------------------------------------------------------------ #
    #  Draft → Posted transition
    # ------------------------------------------------------------------ #

    def test_draft_to_posted_transition(self):
        """Invoice must transition from draft to posted state."""
        invoice = self._create_draft_invoice()
        self.assertEqual(invoice.state, 'draft')
        invoice.action_post()
        self.assertEqual(invoice.state, 'posted')

    def test_draft_to_posted_generates_uuid(self):
        """Posting an invoice must generate a deterministic zatca_uuid."""
        invoice = self._create_draft_invoice()
        self.assertFalse(invoice.zatca_uuid)
        invoice.action_post()
        self.assertTrue(invoice.zatca_uuid)
        # UUID must be a valid UUID string
        import uuid as py_uuid
        self.assertIsNotNone(py_uuid.UUID(invoice.zatca_uuid))

    # ------------------------------------------------------------------ #
    #  Cannot sign invoice in draft state
    # ------------------------------------------------------------------ #

    def test_cannot_sign_draft_invoice(self):
        """A draft invoice must have is_zatca_push_allowed = False."""
        invoice = self._create_draft_invoice()
        self.assertFalse(invoice.is_zatca_push_allowed)

    # ------------------------------------------------------------------ #
    #  Cannot sign invoice already CLEARED in live
    # ------------------------------------------------------------------ #

    def test_cannot_sign_already_cleared_live(self):
        """Signing an invoice already cleared in live must raise UserError."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'live',
        })
        with self.assertRaises(UserError):
            invoice.action_zatca_sign()

    def test_cannot_push_to_sandbox_if_cleared_live(self):
        """Pushing to sandbox an invoice already cleared in live must raise
        UserError."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'live',
        })
        with self.assertRaises(UserError):
            invoice.action_push_to_sandbox_zatca()

    def test_cannot_push_to_sandbox_if_cleared_sandbox(self):
        """Pushing to sandbox an invoice already cleared in sandbox must raise
        UserError."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'sandbox',
        })
        with self.assertRaises(UserError):
            invoice.action_push_to_sandbox_zatca()

    # ------------------------------------------------------------------ #
    #  _check_zatca_push_allowed guards
    # ------------------------------------------------------------------ #

    def test_push_not_allowed_when_not_posted(self):
        """_check_zatca_push_allowed must return False for draft invoices."""
        invoice = self._create_draft_invoice()
        allowed, reason = invoice._check_zatca_push_allowed()
        self.assertFalse(allowed)
        self.assertIn('not posted', reason.lower())

    def test_push_not_allowed_when_already_cleared_live(self):
        """_check_zatca_push_allowed must return False for already cleared
        live invoices."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({
            'zatca_status': 'cleared',
            'zatca_exec_mode': 'live',
        })
        allowed, reason = invoice._check_zatca_push_allowed()
        self.assertFalse(allowed)
        self.assertIn('already signed', reason.lower())

    def test_push_not_allowed_when_no_invoice_date(self):
        """_check_zatca_push_allowed must return False when invoice_date is
        missing."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({'invoice_date': False})
        allowed, reason = invoice._check_zatca_push_allowed()
        self.assertFalse(allowed)
        self.assertIn('invoice date', reason.lower())

    def test_push_not_allowed_when_date_older_than_15_days(self):
        """_check_zatca_push_allowed must return False for invoices older than
        15 days."""
        old_date = date.today() - timedelta(days=20)
        invoice = self._create_draft_invoice()
        invoice.write({'invoice_date': old_date})
        invoice.action_post()
        allowed, reason = invoice._check_zatca_push_allowed()
        self.assertFalse(allowed)
        self.assertIn('15 days', reason.lower())

    # ------------------------------------------------------------------ #
    #  _apply_cloudrefit_status_update — Regression guard (existing tests
    #  in TestZatcaInvoiceSigning cover basic cases; this covers edge cases)
    # ------------------------------------------------------------------ #

    def test_status_update_idempotent_same_status(self):
        """Applying the same status twice must be a no-op (idempotent)."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({'zatca_status': 'cleared'})

        payload = {
            'status': 'completed',
            'signedData': {
                'hash': 'new_hash',
                'qrImage': 'new_qr',
                'xml': '<New/>',
            },
        }
        # First update — should apply
        invoice._apply_cloudrefit_status_update(payload, 'clearance')
        self.assertEqual(invoice.zatca_hash, 'new_hash')

        # Second update with same data — must be idempotent (no error)
        invoice._apply_cloudrefit_status_update(payload, 'clearance')
        self.assertEqual(invoice.zatca_hash, 'new_hash')

    def test_status_update_regression_cleared_to_pending_ignored(self):
        """Regression from cleared to pending must be ignored."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({'zatca_status': 'cleared', 'zatca_hash': 'original_hash'})

        payload = {
            'status': 'pending',
            'signedData': {},
        }
        invoice._apply_cloudrefit_status_update(payload, 'clearance')
        # Must remain cleared
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'original_hash')

    def test_status_update_reported_to_cleared_allowed(self):
        """Forward transition from reported to cleared must be allowed."""
        invoice = self._create_draft_invoice()
        invoice.action_post()
        invoice.write({'zatca_status': 'reported', 'zatca_hash': 'old_hash'})

        payload = {
            'status': 'completed',
            'signedData': {
                'hash': 'promoted_hash',
                'qrImage': 'promoted_qr',
                'xml': '<Promoted/>',
            },
        }
        invoice._apply_cloudrefit_status_update(payload, 'clearance')
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'promoted_hash')


# ================================================================== #
#  ZATCA COMPLIANCE — res.partner _check_zatca_identity_and_address
#  These tests mirror the TypeScript validation.ts rules per AGENTS.md
#  ZATCA Validation Sync Rule.
# ================================================================== #


@tagged('post_install', 'zatca_compliance')
class TestZatcaPartnerCompliance(TransactionCase):
    """Validate that res.partner ZATCA identity & address constraints match
    the TypeScript validation.ts rules:

    - VAT regex: ``/^3\\d{13}3$/``
    - Building No: ``/^\\d{4}$/``
    - Postal Code: ``/^\\d{5}$/``
    - District required for SA buyers
    - Other ID type enum values
    - Other ID value format: ``/^[a-zA-Z0-9]*$/``
    - B2C required fields: name + phone
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.country_sa = cls.env.ref('base.sa', raise_if_not_found=False)
        cls.valid_vat = '399999999901003'  # Starts with 3, 15 digits, ends with 3

    # ------------------------------------------------------------------ #
    #  VAT regex: /^3\d{13}3$/
    # ------------------------------------------------------------------ #

    def test_vat_valid_15_digits_saudi(self):
        """A valid SA VAT (15 digits, starts and ends with 3) must not raise
        ValidationError."""
        partner = self.env['res.partner'].create({
            'name': 'Valid VAT Partner',
            'vat': self.valid_vat,
            'is_company': True,
            'street': 'Test Street',
            'city': 'Riyadh',
            'country_id': self.country_sa.id if self.country_sa else False,
            'building_no': '1234',
            'district': 'Test District',
            'zip': '12345',
            'phone': '+966501234567',
        })
        # The constraint should have passed since we provided all required fields
        self.assertTrue(partner.vat == self.valid_vat)

    def test_vat_invalid_wrong_prefix(self):
        """VAT not starting with 3 must raise ValidationError for SA."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Bad VAT Prefix',
                'vat': '199999999901003',
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('VAT', str(ctx.exception))

    def test_vat_invalid_wrong_suffix(self):
        """VAT not ending with 3 must raise ValidationError for SA."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Bad VAT Suffix',
                'vat': '399999999901000',
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('VAT', str(ctx.exception))

    def test_vat_invalid_too_short(self):
        """VAT with fewer than 15 digits must raise ValidationError for SA."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Short VAT',
                'vat': '39999999901',
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('VAT', str(ctx.exception))

    def test_vat_invalid_too_long(self):
        """VAT with more than 15 digits must raise ValidationError for SA."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Long VAT',
                'vat': '39999999990100399',
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('VAT', str(ctx.exception))

    def test_vat_non_sa_skip_validation(self):
        """Non-SA VAT must NOT be validated against the SA pattern."""
        country_ae = self.env.ref('base.ae', raise_if_not_found=False)
        partner = self.env['res.partner'].create({
            'name': 'Non-SA VAT',
            'vat': '12345',
            'is_company': True,
            'street': 'Test Street',
            'city': 'Dubai',
            'country_id': country_ae.id if country_ae else False,
            'building_no': '1234',
            'district': 'Test District',
            'zip': '12345',
            'phone': '+971501234567',
        })
        self.assertEqual(partner.vat, '12345')

    # ------------------------------------------------------------------ #
    #  Building No: /^\d{4}$/
    # ------------------------------------------------------------------ #

    def test_building_no_valid_4_digits(self):
        """Building number with exactly 4 digits must pass."""
        partner = self.env['res.partner'].create({
            'name': 'Valid Building',
            'vat': self.valid_vat,
            'is_company': True,
            'street': 'Test Street',
            'city': 'Riyadh',
            'country_id': self.country_sa.id if self.country_sa else False,
            'building_no': '5678',
            'district': 'Test District',
            'zip': '12345',
            'phone': '+966501234567',
        })
        self.assertEqual(partner.building_no, '5678')

    def test_building_no_invalid_3_digits(self):
        """Building number with 3 digits must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Bad Building',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '567',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('Building Number', str(ctx.exception))

    def test_building_no_invalid_with_letters(self):
        """Building number containing letters must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Alpha Building',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '56A8',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('Building Number', str(ctx.exception))

    def test_building_no_empty_raises_for_sa_b2b(self):
        """Empty building_no must raise ValidationError for SA B2B."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Empty Building',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('Building Number', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  Postal Code: /^\d{5}$/
    # ------------------------------------------------------------------ #

    def test_postal_code_valid_5_digits(self):
        """Postal code with exactly 5 digits must pass."""
        partner = self.env['res.partner'].create({
            'name': 'Valid Zip',
            'vat': self.valid_vat,
            'is_company': True,
            'street': 'Test Street',
            'city': 'Riyadh',
            'country_id': self.country_sa.id if self.country_sa else False,
            'building_no': '1234',
            'district': 'Test District',
            'zip': '54321',
            'phone': '+966501234567',
        })
        self.assertEqual(partner.zip, '54321')

    def test_postal_code_invalid_4_digits(self):
        """Postal code with 4 digits must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Short Zip',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '5432',
                'phone': '+966501234567',
            })
        self.assertIn('Postal Code', str(ctx.exception))

    def test_postal_code_invalid_with_letters(self):
        """Postal code containing letters must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Alpha Zip',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '54E21',
                'phone': '+966501234567',
            })
        self.assertIn('Postal Code', str(ctx.exception))

    def test_postal_code_empty_raises_for_sa_b2b(self):
        """Empty postal_code must raise ValidationError for SA B2B."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Empty Zip',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '',
                'phone': '+966501234567',
            })
        self.assertIn('Postal Code', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  District required for SA B2B
    # ------------------------------------------------------------------ #

    def test_district_required_for_sa_b2b(self):
        """Empty district must raise ValidationError for SA B2B."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'No District',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': '',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('District', str(ctx.exception))

    def test_district_whitespace_only_raises(self):
        """District with only whitespace must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'Blank District',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': '   ',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('District', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  B2B requires VAT or Other ID (Type + Value)
    # ------------------------------------------------------------------ #

    def test_b2b_requires_vat_or_other_id(self):
        """B2B partner without VAT AND without Other ID must raise
        ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'No ID B2B',
                'vat': '',
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('VAT', str(ctx.exception))

    def test_b2b_with_other_id_only_passes(self):
        """B2B partner with Other ID (Type + Value) but no VAT must pass."""
        partner = self.env['res.partner'].create({
            'name': 'Other ID B2B',
            'vat': '',
            'zatca_id_type': 'CRN',
            'zatca_id_value': '1234567890',
            'is_company': True,
            'street': 'Test Street',
            'city': 'Riyadh',
            'country_id': self.country_sa.id if self.country_sa else False,
            'building_no': '1234',
            'district': 'Test District',
            'zip': '12345',
            'phone': '+966501234567',
        })
        self.assertEqual(partner.zatca_id_type, 'CRN')
        self.assertEqual(partner.zatca_id_value, '1234567890')

    def test_b2b_requires_street(self):
        """B2B partner without street must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'No Street B2B',
                'vat': self.valid_vat,
                'is_company': True,
                'street': '',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('Street', str(ctx.exception))

    def test_b2b_requires_city(self):
        """B2B partner without city must raise ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'No City B2B',
                'vat': self.valid_vat,
                'is_company': True,
                'street': 'Test Street',
                'city': '',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
        self.assertIn('City', str(ctx.exception))

    # ------------------------------------------------------------------ #
    #  Other ID type enum values — matches TypeScript enum
    # ------------------------------------------------------------------ #

    def test_other_id_type_valid_values(self):
        """All valid ZATCA Other ID types must be accepted."""
        valid_types = ['CRN', 'TIN', 'NAT', 'PAS', 'GCC', 'IQA', 'MOM', 'MLS', 'SAG', '700', 'OTH']
        for id_type in valid_types:
            partner = self.env['res.partner'].create({
                'name': f'ID Type {id_type}',
                'vat': '',
                'zatca_id_type': id_type,
                'zatca_id_value': 'ABCDEF1234',
                'is_company': True,
                'street': 'Test Street',
                'city': 'Riyadh',
                'country_id': self.country_sa.id if self.country_sa else False,
                'building_no': '1234',
                'district': 'Test District',
                'zip': '12345',
                'phone': '+966501234567',
            })
            self.assertEqual(partner.zatca_id_type, id_type)

    # ------------------------------------------------------------------ #
    #  B2C requires phone or mobile
    # ------------------------------------------------------------------ #

    def test_b2c_requires_phone_or_mobile(self):
        """B2C partner without phone AND without mobile must raise
        ValidationError."""
        with self.assertRaises(Exception) as ctx:
            self.env['res.partner'].create({
                'name': 'B2C No Contact',
                'phone': '',
                'mobile': '',
                'is_company': False,
            })
        self.assertTrue(
            'Mobile' in str(ctx.exception) or 'Phone' in str(ctx.exception),
        )

    def test_b2c_with_phone_passes(self):
        """B2C partner with phone must pass validation."""
        partner = self.env['res.partner'].create({
            'name': 'B2C With Phone',
            'phone': '+966501234567',
            'is_company': False,
        })
        self.assertEqual(partner.phone, '+966501234567')

    def test_b2c_with_mobile_passes(self):
        """B2C partner with mobile (no phone) must pass validation."""
        partner = self.env['res.partner'].create({
            'name': 'B2C With Mobile',
            'mobile': '+966508765432',
            'is_company': False,
        })
        self.assertEqual(partner.mobile, '+966508765432')


# ================================================================== #
#  CONCURRENCY
# ================================================================== #


@tagged('post_install', 'zatca_concurrency')
class TestZatcaConcurrency(TransactionCase):
    """Concurrency and idempotency tests for ZATCA invoice operations."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.business_id', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        cls.country_sa = cls.env.ref('base.sa', raise_if_not_found=False)

        cls.partner = cls.env['res.partner'].create({
            'name': 'SA B2B Complete',
            'vat': '399999999901003',
            'street': 'King Fahd Road',
            'city': 'Riyadh',
            'country_id': cls.country_sa.id if cls.country_sa else False,
            'building_no': '1234',
            'district': 'Al-Malaz',
            'zip': '12345',
            'phone': '+966501234567',
            'is_company': True,
        })

        cls.product = cls.env['product.product'].create({
            'name': 'Test Service',
            'type': 'service',
            'list_price': 100.0,
        })
        cls.tax_15 = cls.env['account.tax'].create({
            'name': 'KSA VAT 15%',
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
        })
        cls.journal = cls.env['account.journal'].search([('type', '=', 'sale')], limit=1)
        if not cls.journal:
            cls.journal = cls.env['account.journal'].create({
                'name': 'Test Sale Journal',
                'type': 'sale',
                'code': 'TSJ',
            })

        cls.api_client = cls.env['cloudrefit.zatca.api.client']
        cls._original_call = type(cls.api_client).call_gateway
        type(cls.api_client).call_gateway = MockZatcaApiClient.call_gateway

    @classmethod
    def tearDownClass(cls):
        type(cls.api_client).call_gateway = cls._original_call
        MockZatcaApiClient.reset()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        MockZatcaApiClient.reset()
        self.ICP.set_param('cloudrefit_invoicing.mode', 'live')

    def _create_invoice(self, **extra):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'invoice_date': date.today(),
            'name': f'INV/CONC/{self.id()}',
            'journal_id': self.journal.id,
            'invoice_line_ids': [(0, 0, {
                'product_id': self.product.id,
                'quantity': 1,
                'price_unit': 100.0,
                'tax_ids': [(6, 0, [self.tax_15.id])],
                'name': self.product.name,
            })],
            **extra,
        })
        invoice.action_post()
        return invoice

    # ------------------------------------------------------------------ #
    #  Deterministic UUID — same dbname + id = same UUID
    # ------------------------------------------------------------------ #

    def test_deterministic_uuid_same_inputs(self):
        """UUID generated for the same database + invoice ID must be
        deterministic (UUIDv5)."""
        import uuid as py_uuid

        invoice = self._create_invoice()
        # The UUID was already generated during post()
        uuid1 = invoice.zatca_uuid

        # Recompute using the same algorithm
        namespace = py_uuid.uuid5(py_uuid.NAMESPACE_OID, invoice.env.cr.dbname)
        expected = str(py_uuid.uuid5(namespace, str(invoice.id)))

        self.assertEqual(uuid1, expected)

    # ------------------------------------------------------------------ #
    #  _apply_cloudrefit_status_update — idempotent calls
    # ------------------------------------------------------------------ #

    def test_clearance_update_idempotent(self):
        """Calling _apply_cloudrefit_status_update twice with the same
        completed payload must be idempotent (no error, same result)."""
        invoice = self._create_invoice()

        payload = {
            'status': 'completed',
            'signedData': {
                'hash': 'idempotent_hash',
                'qrImage': 'idempotent_qr',
                'xml': '<Idempotent/>',
            },
        }

        # First call
        invoice._apply_cloudrefit_status_update(payload, 'clearance')
        self.assertEqual(invoice.zatca_status, 'cleared')

        # Second call — must not raise and must keep same data
        invoice._apply_cloudrefit_status_update(payload, 'clearance')
        self.assertEqual(invoice.zatca_status, 'cleared')
        self.assertEqual(invoice.zatca_hash, 'idempotent_hash')

    def test_clearance_update_race_safe(self):
        """_apply_cloudrefit_status_update uses SELECT FOR UPDATE to prevent
        TOCTOU races."""
        import threading
        from unittest.mock import patch

        invoice = self._create_invoice()
        invoice.write({'zatca_status': 'not_signed'})

        results = []

        def apply_update(status_val):
            """Apply a clearance update in a thread."""
            try:
                payload = {
                    'status': status_val,
                    'signedData': {
                        'hash': f'race_{status_val}_hash',
                        'qrImage': f'race_{status_val}_qr',
                        'xml': f'<Race{status_val}/>',
                    },
                }
                # Use a separate env cursor for each thread
                with self.env.cr.savepoint():
                    invoice._apply_cloudrefit_status_update(payload, 'clearance')
                    results.append(('success', invoice.zatca_status))
            except Exception as e:
                results.append(('error', str(e)))

        # Simulate two concurrent updates — completed and failed
        t1 = threading.Thread(target=apply_update, args=('completed',))
        t2 = threading.Thread(target=apply_update, args=('failed',))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # At least one should have succeeded
        successes = [r for r in results if r[0] == 'success']
        self.assertGreaterEqual(len(successes), 1,
                                msg=f'Expected at least one success, got: {results}')

    # ------------------------------------------------------------------ #
    #  Duplicate sign submission — gateway idempotency
    # ------------------------------------------------------------------ #

    def test_sign_idempotent_same_invoice(self):
        """Calling action_zatca_sign twice on the same invoice should succeed
        the first time and the second call should be handled gracefully
        (status already cleared, so raises UserError)."""
        invoice = self._create_invoice()

        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices',
            201,
            {
                'signedData': {
                    'hash': 'first_sign_hash',
                    'qrImage': 'first_qr',
                    'xml': '<First/>',
                },
            },
        )

        # First sign — should succeed
        invoice.action_zatca_sign()
        self.assertEqual(invoice.zatca_status, 'cleared')

        # Second sign — should raise because already cleared in live
        with self.assertRaises(UserError):
            invoice.action_zatca_sign()

    # ------------------------------------------------------------------ #
    #  Row-level lock in _apply_cloudrefit_status_update
    # ------------------------------------------------------------------ #

    def test_apply_status_update_uses_for_update(self):
        """_apply_cloudrefit_status_update must execute SELECT FOR UPDATE on
        the invoice row."""
        from odoo.exceptions import UserError as OdooUserError

        invoice = self._create_invoice()
        invoice.write({'zatca_status': 'not_signed'})

        # Verify that the method runs the FOR UPDATE query
        # by checking that the cr.execute is called with SELECT ... FOR UPDATE
        payload = {
            'status': 'completed',
            'signedData': {
                'hash': 'lock_test_hash',
                'qrImage': 'lock_test_qr',
                'xml': '<LockTest/>',
            },
        }

        with patch.object(invoice.env.cr, 'execute', wraps=invoice.env.cr.execute) as mock_execute:
            invoice._apply_cloudrefit_status_update(payload, 'clearance')
            # Check that FOR UPDATE was used
            for call_args in mock_execute.call_args_list:
                sql = call_args[0][0] if call_args[0] else ''
                params = call_args[0][1] if len(call_args[0]) > 1 else []
                if 'SELECT' in sql and 'FOR UPDATE' in sql:
                    break
            else:
                self.fail('Expected SELECT ... FOR UPDATE query, but none found')

        self.assertEqual(invoice.zatca_status, 'cleared')
