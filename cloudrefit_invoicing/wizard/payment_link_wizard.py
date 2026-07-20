from odoo import models, fields, api
from odoo.exceptions import UserError


class PaymentLinkWizard(models.TransientModel):
    _name = 'cloudrefit.payment.link.wizard'
    _description = 'Payment Link Amount Wizard'

    invoice_id = fields.Many2one(
        'account.move',
        string='Invoice',
        required=True,
    )
    amount = fields.Monetary(
        string='Payment Amount',
        required=True,
        currency_field='currency_id',
        help='Amount for the payment link. Pre-filled with the remaining due amount.',
    )
    currency_id = fields.Many2one(
        'res.currency',
        related='invoice_id.currency_id',
        readonly=True,
    )
    action_type = fields.Selection([
        ('generate', 'Generate'),
        ('regenerate', 'Regenerate'),
    ], string='Action Type', default='generate', required=True)

    include_payment_buttons = fields.Boolean(
        string='Include Payment Buttons',
        default=True,
        help='When unchecked, the invoice will not show payment buttons.',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            invoice = self.env['account.move'].browse(self.env.context['active_id'])
            if 'invoice_id' in fields_list:
                res['invoice_id'] = invoice.id
            if 'amount' in fields_list:
                res['amount'] = invoice.cloudrefit_payment_link_due_amount
            if 'action_type' in fields_list:
                res['action_type'] = self.env.context.get('action_type', 'generate')
            if 'include_payment_buttons' in fields_list:
                res['include_payment_buttons'] = invoice.cloudrefit_show_payment_buttons
        return res

    def action_generate(self):
        self.ensure_one()
        invoice = self.invoice_id
        invoice.cloudrefit_show_payment_buttons = self.include_payment_buttons
        invoice.action_generate_payment_link(
            amount=self.amount,
            include_payment_buttons=self.include_payment_buttons,
        )
        return {'type': 'ir.actions.client', 'tag': 'reload'}
