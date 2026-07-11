from odoo import models, api
import requests
import logging

_logger = logging.getLogger(__name__)

class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _create_payments(self):
        payments = super(AccountPaymentRegister, self)._create_payments()
        for payment in payments:
            if payment.partner_type == 'customer':
                # The wizard knows which lines/invoices it is paying via self.line_ids
                moves = self.line_ids.mapped('move_id').filtered(lambda m: m.zatca_uuid)
                for move in moves:
                    self.env['account.payment']._push_payment_to_cloudrefit(payment, move)
        return payments

class AccountPayment(models.Model):
    _inherit = 'account.payment'

    def _push_payment_to_cloudrefit(self, payment, move):
        try:
            move_with_ctx = move.with_company(move.company_id)
            creds = move_with_ctx._get_zatca_credentials()
            exec_mode = move_with_ctx.zatca_exec_mode or move_with_ctx._resolve_mode()
            
            gateway_url = creds.get(f'gateway_url_{exec_mode}')
            business_id = creds.get(f'business_id_{exec_mode}')
            api_key = creds.get(f'api_key_{exec_mode}')
                
            if not all([gateway_url, business_id, api_key]):
                _logger.warning(f"CloudRefit {exec_mode} credentials missing, cannot push payment.")
                return

            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
                'X-Mode': exec_mode.upper()
            }

            url = f"{gateway_url.rstrip('/')}/api/v1/invoices/{business_id}/payments/{move.zatca_uuid}"
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
