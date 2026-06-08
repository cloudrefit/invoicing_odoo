"""
Mock ZATCA Gateway Client for testing.

Provides a drop-in replacement for `ZatcaApiClient.call_gateway()` that returns
deterministic responses without making real HTTP requests. Designed for use
with Odoo's `TransactionCase` via monkey-patching.

Usage:
    from odoo.addons.cloudrefit_invoicing.tests.mock_gateway import MockZatcaApiClient

    class TestFoo(TransactionCase):
        @classmethod
        def setUpClass(cls):
            super().setUpClass()
            # Patch the abstract model method
            cls._original_call = type(cls.env['cloudrefit.zatca.api.client']).call_gateway
            type(cls.env['cloudrefit.zatca.api.client']).call_gateway = MockZatcaApiClient.call_gateway

        @classmethod
        def tearDownClass(cls):
            type(cls.env['cloudrefit.zatca.api.client']).call_gateway = cls._original_call
            MockZatcaApiClient.reset()
            super().tearDownClass()
"""

import json


class MockZatcaApiClient:
    """Mock gateway that returns deterministic responses for testing.

    Responses are registered per (method, endpoint_pattern) and matched in order
    of insertion. The first matching response is consumed and returned.
    """

    # Response registry: list of (method, endpoint_pattern, status_code, body_dict)
    _responses = []
    # Call history: list of (method, endpoint, payload_dict)
    call_history = []

    # ------------------------------------------------------------------ #
    #  Registry helpers
    # ------------------------------------------------------------------ #
    @classmethod
    def register_response(cls, method, endpoint_pattern, status_code, body):
        """Register a mock response.

        Args:
            method: HTTP method (e.g. 'POST', 'GET').
            endpoint_pattern: Substring to match against the endpoint URL.
            status_code: HTTP status code to return.
            body: dict to return as the JSON response body.
        """
        cls._responses.append((method, endpoint_pattern, status_code, body))

    @classmethod
    def reset(cls):
        """Clear all registered responses and call history."""
        cls._responses = []
        cls.call_history = []

    # ------------------------------------------------------------------ #
    #  Assertion helpers
    # ------------------------------------------------------------------ #
    @classmethod
    def assert_called_with(cls, method, endpoint_contains):
        """Assert that at least one call matches *method* and an endpoint
        containing *endpoint_contains*."""
        for call_method, call_endpoint, _payload in cls.call_history:
            if call_method == method and endpoint_contains in call_endpoint:
                return
        raise AssertionError(
            f"Expected call ({method}, *{endpoint_contains}*) not found.\n"
            f"Actual calls: {cls.call_history}"
        )

    @classmethod
    def assert_not_called(cls):
        """Assert that no gateway calls have been made."""
        if cls.call_history:
            raise AssertionError(
                f"Expected no gateway calls, but {len(cls.call_history)} were made: "
                f"{cls.call_history}"
            )

    @classmethod
    def assert_called_once(cls):
        """Assert that exactly one gateway call was made."""
        if len(cls.call_history) != 1:
            raise AssertionError(
                f"Expected exactly 1 gateway call, but {len(cls.call_history)} were made: "
                f"{cls.call_history}"
            )

    # ------------------------------------------------------------------ #
    #  Drop-in replacement for ZatcaApiClient.call_gateway
    # ------------------------------------------------------------------ #
    @classmethod
    def call_gateway(cls, self, endpoint, method='POST', json_data=None,
                     params=None, action='verify', mode='live'):
        """Mock implementation — returns registered responses.

        Signature matches ``ZatcaApiClient.call_gateway(self, endpoint, ...)``
        so it can be used as a direct method replacement on the model class.

        Returns:
            A fake ``requests.Response``-like object with ``.status_code``,
            ``.json()``, ``.text``, and ``.ok`` attributes.
        """
        # Record the call
        cls.call_history.append((method, endpoint, json_data))

        # Seek a matching registered response (first match wins)
        for idx, (reg_method, reg_pattern, status_code, body) in enumerate(cls._responses):
            if reg_method == method and reg_pattern in endpoint:
                # Remove the matched response so it's consumed once
                cls._responses.pop(idx)
                return _FakeResponse(status_code, body)

        # Fallback: return a default 200 OK
        return _FakeResponse(200, {'status': 'ok', 'message': 'default mock response'})


class _FakeResponse:
    """Lightweight stand-in for ``requests.Response``."""

    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False)
        self.ok = 200 <= status_code < 300
        self.headers = {'Content-Type': 'application/json'}

    def json(self):
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise Exception(f"HTTP {self.status_code}: {self.text}")

    def __repr__(self):
        return f"<_FakeResponse {self.status_code} {self._body}>"


# ------------------------------------------------------------------ #
#  Default response registration (import-time)
# ------------------------------------------------------------------ #
# These defaults are registered when the module is imported.  Tests can
# override or add additional responses via ``register_response()``.

# Sync (201 Created) — happy path
MockZatcaApiClient.register_response(
    'POST', '/api/v1/invoices',
    201,
    {
        'id': 'inv_test_001',
        'status': 'cleared',
        'hash': 'abc123',
        'qr_code': 'base64...',
        'xml': '<xml>...</xml>',
        'signedData': {
            'hash': 'abc123',
            'qrImage': 'base64...',
            'xml': '<xml>...</xml>',
        },
    },
)

# Async (202 Accepted) — job created, pending
MockZatcaApiClient.register_response(
    'POST', '/api/v1/invoices',
    202,
    {
        'job_uuid': 'job_test_001',
        'status': 'pending',
    },
)

# Job polling — completed
MockZatcaApiClient.register_response(
    'GET', '/api/v1/invoices/99/status/job_test_001',
    200,
    {
        'status': 'completed',
        'signedData': {
            'hash': 'abc123',
            'qrImage': 'base64...',
            'xml': '<xml>...</xml>',
        },
    },
)

# Job polling — still processing
MockZatcaApiClient.register_response(
    'GET', '/api/v1/invoices/99/status/job_test_001',
    200,
    {
        'status': 'processing',
    },
)

# Auth verify — success
MockZatcaApiClient.register_response(
    'POST', '/api/v1/auth/verify',
    200,
    {
        'status': 'verified',
        'business': {
            'id': 'biz_test_001',
            'is_sandbox_active': True,
            'is_live_active': False,
        },
    },
)

# Plugin units — success
MockZatcaApiClient.register_response(
    'GET', '/api/v1/plugin/units',
    200,
    [
        {'id': 'unit_test_001', 'name': 'Test Unit'},
    ],
)

# Gateway ping — success
MockZatcaApiClient.register_response(
    'GET', '/api/plugin/ping',
    200,
    {'message': 'pong', 'status': 'ok'},
)
