from odoo import models, api
import requests
import logging

_logger = logging.getLogger(__name__)

class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def action_post(self):
        res = super(AccountPayment, self).action_post()
        for payment in self:
            if payment.partner_type == 'customer' and payment.reconciled_invoice_ids:
                for move in payment.reconciled_invoice_ids:
                    if move.cloudrefit_uuid:
                        self._push_payment_to_cloudrefit(payment, move)
        return res

    def _push_payment_to_cloudrefit(self, payment, move):
        try:
            config = self.env['res.company']._cr_get_credentials('live')
            gateway_url = config.get('url')
            business_id = config.get('biz_id')
            api_key = config.get('api')

            if not all([gateway_url, business_id, api_key]):
                # Fallback to sandbox if live is not configured
                config = self.env['res.company']._cr_get_credentials('sandbox')
                gateway_url = config.get('url')
                business_id = config.get('biz_id')
                api_key = config.get('api')
                
            if not all([gateway_url, business_id, api_key]):
                _logger.warning("CloudRefit credentials missing, cannot push payment.")
                return

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}'
            }

            url = f"{gateway_url.rstrip('/')}/api/v1/invoices/{business_id}/payments/{move.cloudrefit_uuid}"
            payload = {
                'amount': payment.amount,
                'source': 'odoo',
                'provider_transaction_id': f'odoo_payment_{payment.id}'
            }

            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code not in (200, 201):
                _logger.error(f"Failed to push payment to CloudRefit. Status: {response.status_code}, Body: {response.text}")

        except Exception as e:
            _logger.error(f"Error pushing payment to CloudRefit Gateway for invoice {move.name}: {str(e)}")
