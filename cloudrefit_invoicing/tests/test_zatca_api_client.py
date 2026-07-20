"""Tests for the ZATCA API Client abstract model.

Tests cover HMAC signature generation and gateway call behaviour.
"""

import hmac
import hashlib
import json

from odoo.tests.common import TransactionCase, tagged
from odoo.exceptions import UserError

from odoo.addons.cloudrefit_invoicing.tests.mock_gateway import MockZatcaApiClient


@tagged('post_install', 'zatca_api_client')
class TestZatcaApiClient(TransactionCase):
    """Verify that ZatcaApiClient produces correct signatures and handles
    gateway responses appropriately."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Ensure the abstract model is available
        cls.api_client = cls.env['cloudrefit.zatca.api.client']

        # Pre-configure a known signing secret for deterministic HMAC tests
        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.api_key', 'sk_live_test_key')

        # Store original call_gateway and patch with mock
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

    # ------------------------------------------------------------------ #
    #  _sign_payload – HMAC signature generation
    # ------------------------------------------------------------------ #

    def test_build_hmac_signature(self):
        """Verify HMAC-SHA256 signature is generated correctly with known
        input/output."""
        payload = {'action': 'verify', 'api_key': 'sk_test_key'}
        signature = self.api_client._sign_payload(payload)

        # Recompute expected signature
        sorted_json = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        expected = hmac.new(
            b'test_secret_key_12345',
            sorted_json.encode(),
            hashlib.sha256,
        ).hexdigest()

        self.assertEqual(
            signature, expected,
            "HMAC signature does not match expected value",
        )

    def test_build_hmac_signature_deterministic(self):
        """Same payload must produce the same signature every time."""
        payload = {'action': 'verify', 'api_key': 'sk_test_key'}
        sig1 = self.api_client._sign_payload(payload)
        sig2 = self.api_client._sign_payload(payload)
        self.assertEqual(sig1, sig2)

    def test_build_hmac_signature_different_payloads(self):
        """Different payloads must produce different signatures."""
        sig_a = self.api_client._sign_payload({'action': 'ping'})
        sig_b = self.api_client._sign_payload({'action': 'verify'})
        self.assertNotEqual(sig_a, sig_b)

    def test_build_hmac_signature_no_secret(self):
        """If signing secret is not configured, _sign_payload must raise
        UserError."""
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', False)
        with self.assertRaises(UserError):
            self.api_client._sign_payload({'action': 'verify'})
        # Restore secret for subsequent tests
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')

    def test_build_hmac_signature_empty_secret(self):
        """An empty signing secret string should also raise UserError."""
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', '')
        with self.assertRaises(UserError):
            self.api_client._sign_payload({'action': 'verify'})
        self.ICP.set_param('cloudrefit_invoicing.signing_secret', 'test_secret_key_12345')

    # ------------------------------------------------------------------ #
    #  call_gateway – request execution
    # ------------------------------------------------------------------ #

    def test_call_gateway_success(self):
        """A registered 200 response should be returned correctly."""
        MockZatcaApiClient.register_response(
            'POST', '/api/v1/invoices', 200, {'status': 'ok'},
        )
        response = self.api_client.call_gateway(
            endpoint='/api/v1/invoices',
            method='POST',
            json_data={'test': True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_call_gateway_response_parsed(self):
        """Response.json() must return the registered body dict."""
        expected_body = {'id': 'inv_001', 'hash': 'xyz'}
        MockZatcaApiClient.register_response('POST', '/api/v1/invoices', 201, expected_body)
        response = self.api_client.call_gateway(
            endpoint='/api/v1/invoices',
            method='POST',
        )
        self.assertEqual(response.json(), expected_body)

    def test_call_gateway_http_error(self):
        """Mock 500 error must be returned with the correct status code and
        error body."""
        error_body = {'error': 'Internal Server Error'}
        MockZatcaApiClient.register_response('POST', '/api/v1/invoices', 500, error_body)
        response = self.api_client.call_gateway(
            endpoint='/api/v1/invoices',
            method='POST',
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), error_body)

    def test_call_gateway_rate_limit_response(self):
        """429 (rate limit) should be returned correctly, allowing the caller
        to implement retry logic."""
        MockZatcaApiClient.register_response('POST', '/api/v1/invoices', 429, {'error': 'Too Many Requests'})
        response = self.api_client.call_gateway(
            endpoint='/api/v1/invoices',
            method='POST',
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn('Too Many Requests', response.text)

    def test_call_gateway_default_fallback(self):
        """If no matching response is registered, a default 200 OK should be
        returned."""
        MockZatcaApiClient.reset()  # Remove all defaults
        response = self.api_client.call_gateway(
            endpoint='/api/v1/test/unknown',
            method='GET',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'message': 'default mock response'})

    def test_call_gateway_tracks_history(self):
        """Every gateway call must be recorded in call_history with method,
        endpoint, and payload."""
        MockZatcaApiClient.reset()
        self.api_client.call_gateway(
            endpoint='/api/v1/invoices',
            method='POST',
            json_data={'test': True},
        )
        self.assertEqual(len(MockZatcaApiClient.call_history), 1)
        recorded_method, recorded_endpoint, recorded_payload = MockZatcaApiClient.call_history[0]
        self.assertEqual(recorded_method, 'POST')
        self.assertIn('/api/v1/invoices', recorded_endpoint)
        self.assertEqual(recorded_payload, {'test': True})

    def test_call_gateway_sequence_responses(self):
        """Multiple registered responses should be consumed in FIFO order."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response('POST', '/api/v1/invoices', 201, {'status': 'first'})
        MockZatcaApiClient.register_response('POST', '/api/v1/invoices', 202, {'status': 'second'})

        response1 = self.api_client.call_gateway(endpoint='/api/v1/invoices', method='POST')
        response2 = self.api_client.call_gateway(endpoint='/api/v1/invoices', method='POST')

        self.assertEqual(response1.status_code, 201)
        self.assertEqual(response1.json()['status'], 'first')
        self.assertEqual(response2.status_code, 202)
        self.assertEqual(response2.json()['status'], 'second')

    # ------------------------------------------------------------------ #
    #  call_gateway – assertion helpers
    # ------------------------------------------------------------------ #

    def test_assert_called_with_passes(self):
        """assert_called_with must not raise when the expected call exists."""
        MockZatcaApiClient.reset()
        self.api_client.call_gateway(endpoint='/api/v1/invoices', method='POST')
        MockZatcaApiClient.assert_called_with('POST', '/api/v1/invoices')

    def test_assert_called_with_fails(self):
        """assert_called_with must raise when the expected call does not
        exist."""
        MockZatcaApiClient.reset()
        with self.assertRaises(AssertionError):
            MockZatcaApiClient.assert_called_with('GET', '/api/v1/never/called')
