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
        secret = creds.get(f'signing_secret_{mode}', '')
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
        api_key = creds.get(f'api_key_{mode}', '')
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

        auto_generate = creds.get('auto_generate_payment_links', False)
        gateway_id = creds.get('payment_gateway_id', '')

        if auto_generate:
            headers['X-Auto-Generate-Payment-Link'] = 'true'
        if gateway_id:
            headers['X-Payment-Gateway-ID'] = str(gateway_id)

        return headers

    @api.model
    def call_gateway(self, endpoint, method='POST', json_data=None, params=None, action='verify', mode='live'):
        """Perform a signed request to the CloudRefit Gateway.

        Raises UserError if the API key is not configured for the requested mode.
        """
        creds = self._get_zatca_credentials()
        gateway_url = creds.get(f'gateway_url_{mode}')
        api_key = creds.get(f'api_key_{mode}')
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
