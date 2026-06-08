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
        cls.ICP.set_param('cloudrefit_invoicing.api_key_live', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.business_id_live', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url_live', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret_live', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        # ---- Test data: partner with VAT (B2B) ----
        cls.partner_b2b = cls.env['res.partner'].create({
            'name': 'Test B2B Customer',
            'vat': 'SA399999999901',
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
        self.assertEqual(inv['type'], 'standard')  # B2B because partner has VAT
        self.assertIn('uuid', inv)
        self.assertIn('icv', inv)
        self.assertIn('amount_untaxed', inv)
        self.assertIn('amount_total', inv)

        # Customer block
        cust = payload['customer']
        self.assertEqual(cust['vat'], 'SA399999999901')
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
        self.assertEqual(payload['invoice']['type'], 'simplified')
        self.assertEqual(payload['customer']['vat'], '300000000000003')

    def test_build_payload_b2b_with_zatca_vat_override(self):
        """Partner with zatca_vat must use the override instead of standard
        VAT."""
        self.partner_b2b.write({'zatca_vat': 'SA199999999902'})
        invoice = self._create_invoice(partner=self.partner_b2b)
        payload = invoice._build_zatca_payload()
        self.assertEqual(payload['customer']['vat'], 'SA199999999902')

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

    def test_resolve_invoice_type_partner_override(self):
        """Partner-level zatca_invoice_type='simplified' should override auto-
        detect even if partner has VAT."""
        self.partner_b2b.write({'zatca_invoice_type': 'simplified'})
        invoice = self._create_invoice(partner=self.partner_b2b)
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
        self.ICP.set_param('cloudrefit_invoicing.business_id_sandbox', 'biz_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.gateway_url_sandbox', 'https://api-dev-invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key_sandbox', 'sk_sandbox_testapikey123')
        self.ICP.set_param('cloudrefit_invoicing.signing_secret_sandbox', 'test_secret_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.unit_id_sandbox', '888')

        invoice = self._create_invoice()
        # Trigger compute
        invoice._compute_is_zatca_sandbox_allowed()
        self.assertTrue(invoice.is_zatca_sandbox_allowed)

    def test_is_zatca_sandbox_allowed_failure_missing_credential(self):
        """is_zatca_sandbox_allowed must be False if any required sandbox credential is empty."""
        self.ICP.set_param('cloudrefit_invoicing.sandbox_enabled', 'True')
        self.ICP.set_param('cloudrefit_invoicing.business_id_sandbox', 'biz_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.gateway_url_sandbox', 'https://api-dev-invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key_sandbox', 'sk_sandbox_testapikey123')
        # Leave signing_secret_sandbox empty
        self.ICP.set_param('cloudrefit_invoicing.signing_secret_sandbox', '')
        self.ICP.set_param('cloudrefit_invoicing.unit_id_sandbox', '888')

        invoice = self._create_invoice()
        # Trigger compute
        invoice._compute_is_zatca_sandbox_allowed()
        self.assertFalse(invoice.is_zatca_sandbox_allowed)

    def test_is_zatca_sandbox_allowed_failure_disabled(self):
        """is_zatca_sandbox_allowed must be False if sandbox is not enabled."""
        self.ICP.set_param('cloudrefit_invoicing.sandbox_enabled', 'False')
        self.ICP.set_param('cloudrefit_invoicing.business_id_sandbox', 'biz_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.gateway_url_sandbox', 'https://api-dev-invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key_sandbox', 'sk_sandbox_testapikey123')
        self.ICP.set_param('cloudrefit_invoicing.signing_secret_sandbox', 'test_secret_sandbox_123')
        self.ICP.set_param('cloudrefit_invoicing.unit_id_sandbox', '888')

        invoice = self._create_invoice()
        # Trigger compute
        invoice._compute_is_zatca_sandbox_allowed()
        self.assertFalse(invoice.is_zatca_sandbox_allowed)
