"""Tests for the ZATCA configuration settings (res.config.settings).

Covers health check indicators, save-and-connect, and test-connection
functionality.
"""

from odoo.tests.common import TransactionCase, tagged
from odoo.addons.cloudrefit_invoicing.tests.mock_gateway import MockZatcaApiClient


@tagged('post_install', 'zatca_settings')
class TestResConfigSettings(TransactionCase):
    """Verify that ResConfigSettings extension manages gateway connectivity
    and health status correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.ICP = cls.env['ir.config_parameter'].sudo()
        cls.ICP.set_param('cloudrefit_invoicing.api_key_live', 'sk_live_testapikey1234567890')
        cls.ICP.set_param('cloudrefit_invoicing.signing_secret_live', 'test_secret_key_12345')
        cls.ICP.set_param('cloudrefit_invoicing.gateway_url_live', 'https://api.invoicing.cloudrefit.com')
        cls.ICP.set_param('cloudrefit_invoicing.business_id_live', 'biz_test_001')
        cls.ICP.set_param('cloudrefit_invoicing.unit_id_live', '999')
        cls.ICP.set_param('cloudrefit_invoicing.mode', 'live')

        # Reset health status for a clean slate
        cls.ICP.set_param('cloudrefit_invoicing.health_status_live', 'untested')
        cls.ICP.set_param('cloudrefit_invoicing.health_last_check_live', '')
        cls.ICP.set_param('cloudrefit_invoicing.health_last_message_live', '')
        cls.ICP.set_param('cloudrefit_invoicing.health_status_sandbox', 'untested')
        cls.ICP.set_param('cloudrefit_invoicing.health_last_check_sandbox', '')
        cls.ICP.set_param('cloudrefit_invoicing.health_last_message_sandbox', '')

        # Patch gateway client
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

        # Create a fresh settings record for each test
        self.settings = self.env['res.config.settings'].create({
            'cloudrefit_gateway_url_live': 'https://api.invoicing.cloudrefit.com',
            'cloudrefit_api_key_live': 'sk_live_testapikey1234567890',
            'cloudrefit_signing_secret_live': 'test_secret_key_12345',
            'cloudrefit_business_id_live': 'biz_test_001',
            'cloudrefit_unit_id_live': 999,
            'cloudrefit_default_invoice_type': 'auto',
        })

        # Reset health status for each test
        self.ICP.set_param('cloudrefit_invoicing.health_status_live', 'untested')
        self.ICP.set_param('cloudrefit_invoicing.health_last_check_live', '')
        self.ICP.set_param('cloudrefit_invoicing.health_last_message_live', '')
        self.ICP.set_param('cloudrefit_invoicing.health_status_sandbox', 'untested')
        self.ICP.set_param('cloudrefit_invoicing.health_last_check_sandbox', '')
        self.ICP.set_param('cloudrefit_invoicing.health_last_message_sandbox', '')

    # ------------------------------------------------------------------ #
    #  Health fields — computed from ir.config_parameter
    # ------------------------------------------------------------------ #

    def test_health_fields_untested_default_live(self):
        """When no health check has been performed, live health status must
        be 'untested'."""
        self.ICP.set_param('cloudrefit_invoicing.health_status_live', 'untested')
        self.settings.invalidate_recordset(['gateway_health_status_live'])
        self.assertEqual(self.settings.gateway_health_status_live, 'untested')
        self.assertEqual(self.settings.gateway_health_last_check_live, '')
        self.assertEqual(self.settings.gateway_health_message_live, '')

    def test_health_fields_reflect_parameters_live(self):
        """Live health compute fields must reflect values written to
        ir.config_parameter."""
        self.ICP.set_param('cloudrefit_invoicing.health_status_live', 'connected')
        self.ICP.set_param('cloudrefit_invoicing.health_last_check_live', '2025-01-01T00:00:00')
        self.ICP.set_param('cloudrefit_invoicing.health_last_message_live', 'Gateway reachable')

        self.settings.invalidate_recordset([
            'gateway_health_status_live', 'gateway_health_last_check_live', 'gateway_health_message_live',
        ])

        self.assertEqual(self.settings.gateway_health_status_live, 'connected')
        self.assertEqual(self.settings.gateway_health_last_check_live, '2025-01-01T00:00:00')
        self.assertEqual(self.settings.gateway_health_message_live, 'Gateway reachable')

    def test_health_fields_untested_default_sandbox(self):
        """When no health check has been performed, sandbox health status must
        be 'untested'."""
        self.assertEqual(self.settings.gateway_health_status_sandbox, 'untested')
        self.assertEqual(self.settings.gateway_health_last_check_sandbox, '')
        self.assertEqual(self.settings.gateway_health_message_sandbox, '')

    # ------------------------------------------------------------------ #
    #  _set_health_status
    # ------------------------------------------------------------------ #

    def test_set_health_status_stores_values_live(self):
        """_set_health_status must persist status and message to
        ir.config_parameter for live mode."""
        self.settings._set_health_status('connected', 'All good', mode='live')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'connected',
        )
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_last_message_live'), 'All good',
        )
        # Last check should be a non-empty ISO timestamp
        self.assertTrue(
            self.ICP.get_param('cloudrefit_invoicing.health_last_check_live'),
        )

    def test_set_health_status_stores_values_sandbox(self):
        """_set_health_status must persist status and message to
        ir.config_parameter for sandbox mode."""
        self.settings._set_health_status('connected', 'Sandbox reachable', mode='sandbox')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_sandbox'), 'connected',
        )
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_last_message_sandbox'), 'Sandbox reachable',
        )

    # ------------------------------------------------------------------ #
    #  action_test_connection_live
    # ------------------------------------------------------------------ #

    def test_test_connection_live_success(self):
        """When the live gateway ping returns 200, health must be set to
        'connected' and business_id auto-saved."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            200,
            {'message': 'pong', 'status': 'ok', 'business_id': 'biz_auto_001'},
        )

        result = self.settings.action_test_connection_live()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'connected',
        )
        # business_id should be auto-saved from the ping response (live)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_live'), 'biz_auto_001',
        )

    def test_test_connection_live_success_without_business_id(self):
        """When the live gateway ping returns 200 without business_id,
        health must be set to 'connected' but business_id unchanged."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            200,
            {'message': 'pong', 'status': 'ok'},
        )

        # Set an existing business_id_live
        self.ICP.set_param('cloudrefit_invoicing.business_id_live', 'biz_existing')

        result = self.settings.action_test_connection_live()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'connected',
        )
        # business_id_live should remain unchanged
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_live'), 'biz_existing',
        )

    def test_test_connection_live_failure(self):
        """When the live gateway ping fails (500), health must be set to
        'failed'."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            500,
            {'error': 'Internal error'},
        )

        result = self.settings.action_test_connection_live()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'failed',
        )

    def test_test_connection_live_http_401(self):
        """A 401 (Unauthorized) must set live health to 'failed'."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            401,
            {'error': 'Unauthorized'},
        )

        self.settings.action_test_connection_live()
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'failed',
        )

    # ------------------------------------------------------------------ #
    #  action_test_connection_sandbox
    # ------------------------------------------------------------------ #

    def test_test_connection_sandbox_success(self):
        """When the sandbox gateway ping returns 200, health must be set to
        'connected' and business_id auto-saved."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            200,
            {'message': 'pong', 'status': 'ok', 'business_id': 'biz_sandbox_001'},
        )

        # Configure sandbox credentials on the settings record
        self.settings.write({
            'cloudrefit_gateway_url_sandbox': 'https://sandbox.invoicing.cloudrefit.com',
            'cloudrefit_api_key_sandbox': 'sk_sandbox_testkey',
            'cloudrefit_signing_secret_sandbox': 'sandbox_secret_key',
        })
        self.ICP.set_param('cloudrefit_invoicing.gateway_url_sandbox', 'https://sandbox.invoicing.cloudrefit.com')
        self.ICP.set_param('cloudrefit_invoicing.api_key_sandbox', 'sk_sandbox_testkey')
        self.ICP.set_param('cloudrefit_invoicing.signing_secret_sandbox', 'sandbox_secret_key')

        result = self.settings.action_test_connection_sandbox()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_sandbox'), 'connected',
        )
        # business_id_sandbox should be auto-saved from the ping response
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_sandbox'), 'biz_sandbox_001',
        )

    def test_test_connection_sandbox_failure(self):
        """When the sandbox gateway ping fails, health must be set to
        'failed'."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            500,
            {'error': 'Internal error'},
        )

        # Configure sandbox credentials
        self.settings.write({
            'cloudrefit_api_key_sandbox': 'sk_sandbox_testkey',
            'cloudrefit_signing_secret_sandbox': 'sandbox_secret_key',
        })
        self.ICP.set_param('cloudrefit_invoicing.api_key_sandbox', 'sk_sandbox_testkey')
        self.ICP.set_param('cloudrefit_invoicing.signing_secret_sandbox', 'sandbox_secret_key')

        result = self.settings.action_test_connection_sandbox()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_sandbox'), 'failed',
        )

    # ------------------------------------------------------------------ #
    #  action_save_and_connect
    # ------------------------------------------------------------------ #

    def test_save_and_connect_live_success(self):
        """action_save_and_connect must save settings and run live connection
        test, ending with health='connected'."""
        MockZatcaApiClient.reset()
        MockZatcaApiClient.register_response(
            'GET', '/api/plugin/ping',
            200,
            {'message': 'pong', 'status': 'ok'},
        )

        result = self.settings.action_save_and_connect()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'connected',
        )
        # Settings should be persisted
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.api_key_live'),
            'sk_live_testapikey1234567890',
        )

    def test_save_and_connect_no_api_key(self):
        """When no API key is configured, health must remain 'untested'."""
        settings_no_key = self.env['res.config.settings'].create({
            'cloudrefit_gateway_url_live': 'https://api.invoicing.cloudrefit.com',
            'cloudrefit_api_key_live': False,
        })

        result = settings_no_key.action_save_and_connect()
        self.assertIsNotNone(result)
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.health_status_live'), 'untested',
        )

    # ------------------------------------------------------------------ #
    #  _auto_save_business_id
    # ------------------------------------------------------------------ #

    def test_auto_save_business_id_saves_new_live(self):
        """_auto_save_business_id must persist a new business_id_live value."""
        self.settings._auto_save_business_id({'business_id': 'biz_new_001'}, mode='live')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_live'), 'biz_new_001',
        )

    def test_auto_save_business_id_saves_new_sandbox(self):
        """_auto_save_business_id must persist a new business_id_sandbox value."""
        self.settings._auto_save_business_id({'business_id': 'biz_sb_001'}, mode='sandbox')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_sandbox'), 'biz_sb_001',
        )

    def test_auto_save_business_id_skips_if_missing_live(self):
        """_auto_save_business_id must not change business_id_live if not
        present in response."""
        self.ICP.set_param('cloudrefit_invoicing.business_id_live', 'biz_existing')
        self.settings._auto_save_business_id({'message': 'pong'}, mode='live')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_live'), 'biz_existing',
        )

    def test_auto_save_business_id_skips_if_same_live(self):
        """_auto_save_business_id must not rewrite if business_id_live is
        unchanged."""
        self.ICP.set_param('cloudrefit_invoicing.business_id_live', 'biz_same')
        self.settings._auto_save_business_id({'business_id': 'biz_same'}, mode='live')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_live'), 'biz_same',
        )

    def test_auto_save_business_id_skips_if_same_sandbox(self):
        """_auto_save_business_id must not rewrite if business_id_sandbox is
        unchanged."""
        self.ICP.set_param('cloudrefit_invoicing.business_id_sandbox', 'biz_same_sb')
        self.settings._auto_save_business_id({'business_id': 'biz_same_sb'}, mode='sandbox')
        self.assertEqual(
            self.ICP.get_param('cloudrefit_invoicing.business_id_sandbox'), 'biz_same_sb',
        )

    # ------------------------------------------------------------------ #
    #  Notification helper
    # ------------------------------------------------------------------ #

    def test_cr_notify_structure(self):
        """_cr_notify must return a valid client action dict."""
        action = self.settings._cr_notify('success', 'Test message')
        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'display_notification')
        self.assertEqual(action['params']['type'], 'success')
        self.assertEqual(action['params']['message'], 'Test message')
