import logging
from odoo import api, models, fields

_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    country_id = fields.Many2one(
        'res.country',
        default=lambda self: self.env.ref('base.sa', raise_if_not_found=False)
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
    ], string='ID Type', default='CRN')
    zatca_id_value = fields.Char(string='ID Value')

    # Soft Warnings for UI
    zatca_id_warning = fields.Char(compute='_compute_zatca_id_warning', store=False, readonly=True)
    zatca_vat_warning = fields.Char(compute='_compute_zatca_vat_warning', store=False, readonly=True)

    @api.depends('zatca_id_type', 'zatca_id_value', 'country_id')
    def _compute_zatca_id_warning(self):
        import re
        for partner in self:
            warning = ""
            if partner.zatca_id_type and partner.zatca_id_value:
                val = partner.zatca_id_value.strip()
                country_code = partner.country_id.code if partner.country_id else ''
                
                if partner.zatca_id_type == 'NAT' and (not val.startswith('1') or len(val) != 10):
                    warning = "Warning: NAT must be 10 digits starting with 1."
                elif partner.zatca_id_type == 'CRN' and country_code == 'SA' and not re.match(r'^[17]\d{9}$', val):
                    warning = "Warning: Saudi CRN must be 10 digits starting with 1 or 7."
            partner.zatca_id_warning = warning

    @api.depends('vat', 'country_id', 'is_company', 'zatca_id_value')
    def _compute_zatca_vat_warning(self):
        import re
        for partner in self:
            warning = ""
            if partner.vat and (not partner.country_id or partner.country_id.code == 'SA'):
                vat_val = partner.vat.strip()
                if not re.match(r'^3\d{13}3$', vat_val):
                    warning = "Warning: ZATCA VAT must be 15 digits starting and ending with 3."
            elif partner.is_company and not partner.vat and not partner.zatca_id_value:
                warning = "Warning: B2B customers require either a VAT number or an ID Value."
            partner.zatca_vat_warning = warning
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('company_type') == 'person':
                vals['is_company'] = False
            elif vals.get('company_type') == 'company':
                vals['is_company'] = True
        return super(ResPartner, self).create(vals_list)

    def write(self, vals):
        if vals.get('company_type') == 'person':
            vals['is_company'] = False
        elif vals.get('company_type') == 'company':
            vals['is_company'] = True
        return super(ResPartner, self).write(vals)

    @api.constrains('vat', 'zatca_id_type', 'zatca_id_value', 'country_id', 'building_no', 'district', 'zip', 'street', 'city', 'phone')
    def _check_zatca_identity_and_address(self):
        from odoo.exceptions import ValidationError
        import re

        for partner in self:
            # 1. VAT Format Validation (Global for both B2B and B2C if provided)
            if partner.vat:
                vat_val = partner.vat.strip()
                if not partner.country_id or partner.country_id.code == 'SA':
                    if not re.match(r'^3\d{13}3$', vat_val):
                        raise ValidationError("ZATCA VAT (Saudi Arabia) must be exactly 15 digits, starting and ending with '3'.")

            # 2. B2B Specific Validations
            if partner.is_company and partner.company_type != 'person':
                has_vat = bool(partner.vat)
                has_other_id = bool(partner.zatca_id_type and partner.zatca_id_value)

                if not has_vat and not has_other_id:
                    raise ValidationError(
                        "For B2B (Company) customers, you must provide either a valid ZATCA VAT "
                        "OR an ID (Type + Value)."
                    )

                # Global B2B Address check
                missing_global = []
                if not partner.street:
                    missing_global.append("Street")
                if not partner.city:
                    missing_global.append("City")
                if not partner.country_id:
                    missing_global.append("Country")
                if missing_global:
                    raise ValidationError(
                        f"B2B customers require the following address fields: {', '.join(missing_global)}"
                    )

            # Saudi Address check
            if partner.is_company and partner.country_id and partner.country_id.code == 'SA':
                missing_fields = []
                if not partner.building_no or not re.match(r'^\d{4}$', partner.building_no.strip()):
                    missing_fields.append("Building Number (Must be 4 digits)")
                if not partner.district or not partner.district.strip():
                    missing_fields.append("District")
                if not partner.zip or not re.match(r'^\d{5}$', partner.zip.strip()):
                    missing_fields.append("Postal Code/Zip (Must be 5 digits)")
                
                if missing_fields:
                    raise ValidationError(
                        f"Saudi B2B (Company) customers require the following address fields: {', '.join(missing_fields)}"
                    )

            # 3. B2C (Person) Specific Validations
            if not partner.is_company or partner.company_type == 'person':
                has_mobile = hasattr(partner, 'mobile') and bool(partner.mobile)
                has_phone = bool(partner.phone)
                if not has_mobile and not has_phone:
                    raise ValidationError(
                        "For B2C (Individual) customers, you must provide a Mobile or Phone number."
                    )



