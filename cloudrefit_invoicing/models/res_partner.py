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

    # ZATCA Specific Address Fields
    building_no = fields.Char(string='Building Number')
    district = fields.Char(string='District')

    # ZATCA Other ID Fields
    zatca_id_type = fields.Selection([
        ('CRN', 'Commercial Registration (CRN)'),
        ('TIN', 'Tax Identification Number (TIN)'),
        ('NAT', 'National ID (NAT)'),
        ('PAS', 'Passport ID (PAS)'),
        ('GCC', 'GCC ID (GCC)'),
        ('IQA', 'Iqama (IQA)'),
        ('MOM', 'MOMRAH (MOM)'),
        ('MLS', 'MHRSD (MLS)'),
        ('SAG', 'MISA (SAG)'),
        ('700', '700 Number (700)'),
        ('OTH', 'Other (OTH)'),
    ], string='Other ID Type')
    zatca_id_value = fields.Char(string='Other ID Value')

    @api.constrains('vat', 'zatca_id_type', 'zatca_id_value', 'country_id', 'building_no', 'district', 'zip')
    def _check_zatca_identity_and_address(self):
        from odoo.exceptions import ValidationError
        import re

        for partner in self:
            if not partner.is_company:
                continue

            # OR Logic: Must have a valid VAT OR a valid Other ID.
            has_vat = False
            if partner.vat:
                vat_val = partner.vat.strip()
                if not partner.country_id or partner.country_id.code == 'SA':
                    if re.match(r'^3\d{13}3$', vat_val):
                        has_vat = True
                else:
                    has_vat = True # Foreign VAT

            has_other_id = False
            if partner.zatca_id_type and partner.zatca_id_value:
                has_other_id = True

            if not has_vat and not has_other_id:
                raise ValidationError(
                    "For B2B customers, you must provide either a valid ZATCA VAT (Saudi VAT must be 15 digits starting/ending with 3) "
                    "OR an Other ID (Type + Value)."
                )

            # Saudi Address check
            if partner.country_id and partner.country_id.code == 'SA':
                missing_fields = []
                if not partner.building_no:
                    missing_fields.append("Building Number")
                if not partner.district:
                    missing_fields.append("District")
                if not partner.zip:
                    missing_fields.append("Postal Code/Zip")
                
                if missing_fields:
                    raise ValidationError(
                        f"Saudi B2B customers require the following address fields: {', '.join(missing_fields)}"
                    )


