from odoo import models, api
import hmac
import hashlib
import json
import time
import requests
import os
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class ZatcaApiClient(models.AbstractModel):
    _name = 'cloudrefit.zatca.api.client'
    _inherit = ['cloudrefit.settings.helper', 'cloudrefit.zatca.credentials']
    _description = 'ZATCA API Security Client'

    @api.model
    def _get_base_url(self):
        """Live read of web.base.url."""
        return self.env['ir.config_parameter'].sudo().get_param('web.base.url')

    @api.model
    def _get_build_secret(self, mode='live'):
        """Get the signing secret from the centralized credential model.

        Raises UserError if the secret is not configured for the requested mode.
        """
        creds = self._get_zatca_credentials()
        self._assert_zatca_credentials(creds, mode=mode)
        secret = creds.get('signing_secret', '')
        if not secret:
            raise UserError(
                'Signing secret (%s) is not configured for company "%s". '
                'Please go to Settings \u2192 CloudRefit ZATCA and paste your signing secret.'
                % (mode.title(), creds['company_name'])
            )
        return secret

    @api.model
    def _sign_payload(self, payload, mode='live'):
        """HMAC-SHA256 signature for the given payload."""
        # Sort keys to match Node.js implementation
        # ensure_ascii=False is critical to match Node.js JSON.stringify behavior for Unicode
        sorted_json = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        build_secret = self._get_build_secret(mode=mode)
        signature = hmac.new(
            build_secret.encode(),
            sorted_json.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    @api.model
    def _build_headers(self, payload, mode='live'):
        """Build the HMAC-signed authentication headers for a ZATCA API request.

        Constructs the signature payload from the given data and returns
        the full set of HTTP headers needed for a signed gateway request.

        Raises UserError if the API key is not configured for the requested mode.
        """
        creds = self._get_zatca_credentials()
        self._assert_zatca_credentials(creds, mode=mode)
        api_key = creds.get('api_key', '')
        if not api_key:
            raise UserError(
                'API key (%s) is not configured for company "%s". '
                'Please go to Settings \u2192 CloudRefit ZATCA.'
                % (mode.title(), creds['company_name'])
            )

        odoo_url = self._get_base_url()
        timestamp = str(int(time.time()))

        headers = {
            'X-API-Key': api_key,
            'X-Mode': mode.upper(),
            'X-Connector-URL': odoo_url,
            'X-Integration-URL': odoo_url,
            'Content-Type': 'application/json',
        }

        return headers

    @api.model
    def call_gateway(self, endpoint, method='POST', json_data=None, params=None, action='verify', mode='live'):
        """Perform a signed request to the CloudRefit Gateway.

        Raises UserError if the API key is not configured for the requested mode.
        """
        creds = self._get_zatca_credentials()
        gateway_url = creds.get('gateway_url')
        api_key = creds.get('api_key')
        if not api_key:
            raise UserError(
                'API key (%s) is not configured for company "%s". '
                'Please go to Settings \u2192 CloudRefit ZATCA.'
                % (mode.title(), creds['company_name'])
            )

        odoo_url = self._get_base_url()
        timestamp = str(int(time.time()))

        headers = {
            'X-API-Key': api_key,
            'X-Mode': mode.upper(),
            'X-Connector-URL': odoo_url,
            'X-Integration-URL': odoo_url,
            'Content-Type': 'application/json',
        }

        url = f"{gateway_url.rstrip('/')}{endpoint}"

        # Fix for Fastify: Body cannot be empty for application/json POST
        if method in ['POST', 'PUT'] and json_data is None:
            json_data = {}

        # Log request details for debugging
        # Redact api_key and signature to keep logs secure
        safe_headers = headers.copy()
        if 'X-API-Key' in safe_headers:
            safe_headers['X-API-Key'] = '***'
        if 'X-Signature' in safe_headers:
            safe_headers['X-Signature'] = '***'
            
        _logger.info(
            "action=call_gateway url=%s method=%s headers=%s params=%s payload=%s",
            url, method, safe_headers, params,
            json.dumps(json_data, ensure_ascii=False) if json_data else None
        )

        try:
            response = requests.request(
                method=method,
                url=url,
                json=json_data,
                params=params,
                headers=headers,
                timeout=30
            )
            return response
        except requests.exceptions.ConnectionError:
            raise UserError(f'Cannot connect to CloudRefit Gateway at {gateway_url}.')
        except requests.exceptions.Timeout:
            raise UserError('CloudRefit Gateway request timed out.')

    @api.model
    def upsert_invoice_for_checkout(self, move):
        """Upsert a draft invoice to the platform before generating a payment link.

        Sends the full invoice payload to POST /payments/invoices/upsert
        so the platform has the latest draft data before the customer views
        the print-invoice page.

        Args:
            move: An account.move record to upsert.

        Returns:
            bool: True if the upsert succeeded, False otherwise (non-blocking).
        """
        creds = move.with_company(move.company_id)._get_zatca_credentials()
        mode = 'live'  # Payment links always use live mode
        business_id = creds.get(f'business_id_{mode}')
        gateway_url = creds.get(f'gateway_url_{mode}')
        api_key = creds.get('api_key')

        if not business_id or not gateway_url or not api_key:
            _logger.warning(
                "action=upsert_invoice_for_checkout invoice_id=%s error=missing_credentials "
                "business_id=%s gateway_url=%s api_key=%s",
                move.id, bool(business_id), bool(gateway_url), bool(api_key),
            )
            return False

        # Build the payload — reuse _build_zatca_payload logic
        try:
            payload = move._build_zatca_payload()
        except Exception as e:
            _logger.warning(
                "action=upsert_invoice_for_checkout invoice_id=%s error=payload_build_failed reason=%s",
                move.id, str(e),
            )
            return False

        odoo_url = self._get_base_url()
        headers = {
            'X-API-Key': api_key,
            'X-Mode': mode.upper(),
            'X-Connector-URL': odoo_url,
            'X-Integration-URL': odoo_url,
            'Content-Type': 'application/json',
        }

        url = f"{gateway_url.rstrip('/')}/api/v1/payments/invoices/upsert"

        _logger.info(
            "action=upsert_invoice_for_checkout invoice_id=%s business_id=%s url=%s",
            move.id, business_id, url,
        )

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()
            _logger.info(
                "action=upsert_invoice_for_checkout invoice_id=%s created=%s uuid=%s",
                move.id, result.get('created'), result.get('uuid'),
            )
            return True
        except requests.exceptions.RequestException as e:
            _logger.warning(
                "action=upsert_invoice_for_checkout invoice_id=%s error=http_request_failed reason=%s",
                move.id, str(e),
            )
            return False
        except Exception as e:
            _logger.warning(
                "action=upsert_invoice_for_checkout invoice_id=%s error=unexpected reason=%s",
                move.id, str(e),
            )
            return False

    @api.model
    def create_checkout_session(self, move, gateway='streampay'):
        """Create a checkout session via POST /payments/invoices/:uuid/checkout.

        Args:
            move: An account.move record to create a checkout session for.
            gateway: The payment gateway identifier (e.g. 'streampay', 'tamara').

        Returns:
            dict: The JSON response from the gateway, expected to contain 'redirect_url'.

        Raises:
            UserError: If credentials are missing or the request fails.
        """
        creds = move.with_company(move.company_id)._get_zatca_credentials()
        mode = 'live'
        business_id = creds.get(f'business_id_{mode}')
        gateway_url = creds.get(f'gateway_url_{mode}')
        api_key = creds.get('api_key')

        if not business_id or not gateway_url or not api_key:
            raise UserError(
                'CloudRefit Gateway is not fully configured for company "%s". '
                'Please go to Settings → CloudRefit ZATCA.'
                % creds.get('company_name', 'Unknown')
            )

        odoo_url = self._get_base_url()
        headers = {
            'X-API-Key': api_key,
            'X-Mode': mode.upper(),
            'X-Connector-URL': odoo_url,
            'X-Integration-URL': odoo_url,
            'Content-Type': 'application/json',
        }

        zatca_uuid = move.zatca_uuid
        url = f"{gateway_url.rstrip('/')}/api/v1/payments/invoices/{zatca_uuid}/checkout"
        payload = {'gateway': gateway}

        _logger.info(
            "action=create_checkout_session invoice_id=%s business_id=%s gateway=%s url=%s",
            move.id, business_id, gateway, url,
        )

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()
            _logger.info(
                "action=create_checkout_session invoice_id=%s gateway=%s redirect_url=%s",
                move.id, gateway, result.get('redirect_url'),
            )
            return result
        except requests.exceptions.RequestException as e:
            error_msg = str(e)
            _logger.warning(
                "action=create_checkout_session invoice_id=%s gateway=%s error=http_request_failed reason=%s",
                move.id, gateway, error_msg,
            )
            # Try to extract more specific error from response body
            if hasattr(e, 'response') and e.response is not None:
                try:
                    body = e.response.json()
                    error_msg = body.get('message') or body.get('error') or error_msg
                except Exception:
                    pass
            raise UserError(f'Payment gateway request failed: {error_msg}')
        except Exception as e:
            _logger.warning(
                "action=create_checkout_session invoice_id=%s gateway=%s error=unexpected reason=%s",
                move.id, gateway, str(e),
            )
            raise UserError(f'Payment gateway error: {str(e)}')
