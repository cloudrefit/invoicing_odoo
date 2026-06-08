import logging
from odoo import api, models, fields

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # Per-partner ZATCA overrides (takes precedence over global defaults)
    zatca_invoice_type = fields.Selection([
        ('auto', 'Auto (use company default)'),
        ('simplified', 'Simplified (B2C)'),
        ('standard', 'Standard (B2B)'),
    ], string='ZATCA Invoice Type',
       default='auto',
       help='Override the global ZATCA invoice type for this customer.',
    )
    zatca_vat = fields.Char(
        string='ZATCA VAT Override',
        help='Override the VAT number used in ZATCA invoices for this customer. '
             'Leave empty to use the standard VAT field.',
    )

