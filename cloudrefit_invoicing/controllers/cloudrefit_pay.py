import logging
from odoo import http
from odoo.http import request
import werkzeug.utils

_logger = logging.getLogger(__name__)


class CloudRefitPayController(http.Controller):

    @http.route('/cloudrefit/pay/<string:zatca_uuid>', type='http', auth='public', methods=['GET'], csrf=False)
    def pay(self, zatca_uuid, gateway='streampay', **kwargs):
        """Public redirect endpoint — upsert + checkout → redirect to payment gateway."""
        
        # Find the invoice by zatca_uuid
        move = request.env['account.move'].sudo().search([
            ('zatca_uuid', '=', zatca_uuid),
            ('state', 'in', ('draft', 'posted')),
        ], limit=1)
        
        if not move:
            return request.render('cloudrefit_invoicing.payment_error', {
                'title': 'Invoice Not Found',
                'message': 'The invoice you are looking for could not be found.'
            })
        
        try:
            api_client = request.env['cloudrefit.zatca.api.client'].sudo()
            
            # Step 1: Upsert (if draft — ensures platform has latest data)
            if move.state != 'posted':
                api_client.upsert_invoice_for_checkout(move)
            
            # Step 2: Checkout — get redirect URL from platform
            result = api_client.create_checkout_session(move, gateway)
            redirect_url = result.get('redirect_url')
            
            if not redirect_url:
                raise ValueError('No redirect_url in checkout response')
            
            # Step 3: 302 redirect to payment gateway
            return werkzeug.utils.redirect(redirect_url, 302)
            
        except Exception as e:
            error_msg = str(e)
            _logger.warning("Payment redirect failed for invoice %s (uuid=%s): %s", 
                          move.name, zatca_uuid, error_msg)
            
            if 'GATEWAY_INACTIVE' in error_msg:
                return werkzeug.utils.redirect(
                    f"{move.company_id.cloudrefit_dashboard_url}/payment-unavailable", 302
                )
            
            return request.render('cloudrefit_invoicing.payment_error', {
                'title': 'Payment Error',
                'message': 'Unable to process payment at this time. Please try again or contact the business.'
            })
