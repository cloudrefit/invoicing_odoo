import logging
import hashlib
import hmac
from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

class CloudRefitWebhookController(http.Controller):

    @http.route('/cloudrefit/webhook', type='json', auth='none', methods=['POST'], csrf=False)
    def handle_webhook(self):
        """
        Receives webhook payloads from the CloudRefit Gateway.
        Unauthenticated at the Odoo layer, but protected by HMAC-SHA256 signature verification.
        """
        headers = request.httprequest.headers
        business_id = headers.get('X-CloudRefit-Business-ID')
        signature = headers.get('X-CloudRefit-Signature')

        if not business_id or not signature:
            _logger.warning("action=webhook_error error=missing_headers business_id=%s", business_id)
            return {'error': 'Missing required headers'}

        # Find which Odoo company belongs to this business_id (Multi-company support)
        companies = request.env['res.company'].sudo().search([])
        target_company = None
        target_secret = None
        
        for company in companies:
            # Business ID is now unified (single field, no mode suffix)
            biz_id = request.env['ir.config_parameter'].sudo().get_param(f'cloudrefit_invoicing.business_id_{company.id}')
            if biz_id == business_id:
                target_company = company
                target_secret = request.env['ir.config_parameter'].sudo().get_param(f'cloudrefit_invoicing.signing_secret_{company.id}')
                break

        if not target_company or not target_secret:
            _logger.warning("action=webhook_error error=company_not_found business_id=%s", business_id)
            return {'error': 'Company not found or not fully configured'}

        # Verify HMAC Signature
        raw_body = request.httprequest.get_data()
        expected_signature = hmac.new(
            target_secret.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            _logger.warning("action=webhook_error error=invalid_signature business_id=%s company=%s", business_id, target_company.name)
            return {'error': 'Invalid signature'}

        # Signature is valid, process payload
        payload = request.get_json_data()
        event_type = payload.get('event_type')
        payload_data = payload.get('data', {})
        
        if not event_type or not payload_data:
            return {'error': 'Invalid payload format'}

        zatca_job_uuid = payload_data.get('job_uuid') or payload_data.get('zatca_job_uuid')
        
        # Search for the invoice within the targeted company
        move = request.env['account.move'].sudo().with_company(target_company.id).search([
            ('company_id', '=', target_company.id),
            ('zatca_job_uuid', '=', zatca_job_uuid)
        ], limit=1)
        
        if not move:
            # Fallback to search by zatca_uuid
            zatca_uuid = payload_data.get('zatca_uuid')
            if zatca_uuid:
                move = request.env['account.move'].sudo().with_company(target_company.id).search([
                    ('company_id', '=', target_company.id),
                    ('zatca_uuid', '=', zatca_uuid)
                ], limit=1)
                
        if not move:
            _logger.warning("action=webhook_error error=invoice_not_found business_id=%s zatca_job_uuid=%s", business_id, zatca_job_uuid)
            return {'error': 'Invoice not found'}

        # Route to the unified update logic
        try:
            # We run this in the sudo environment tied to the company context
            move._apply_cloudrefit_status_update(payload_data, event_type)
            return {'status': 'success'}
        except Exception as e:
            _logger.error("action=webhook_error error=update_failed business_id=%s invoice_id=%s error_msg=%s", business_id, move.id, str(e))
            return {'error': 'Internal server error processing payload'}

    @http.route('/cloudrefit/api/invoice_status/<int:invoice_id>', type='json', auth='user')
    def get_invoice_status(self, invoice_id):
        """
        Internal API for the Javascript polling loop to fetch latest invoice status.
        Requires user authentication and read access to the invoice.
        """
        move = request.env['account.move'].search([('id', '=', invoice_id)])
        if not move:
            return {'error': 'Invoice not found'}
            
        return {
            'status': move.cloudrefit_status or 'not_signed',
            'zatca_status': move.zatca_status or '',
            'payment_url': move.cloudrefit_payment_url or '',
            'updated_at': str(move.write_date) if move.write_date else '',
        }

