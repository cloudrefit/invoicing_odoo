import logging
from odoo import models, api

_logger = logging.getLogger(__name__)


class IrConfigParameter(models.Model):
    _name = 'ir.config_parameter'
    _inherit = 'ir.config_parameter'

    @api.model
    def get_param(self, key, default=False):
        """Odoo 20 compatibility shim for get_param."""
        if hasattr(super(), 'get_param'):
            return super().get_param(key, default)
        val = self.get_str(key, default=None)
        return default if val is None else val

    @api.model
    def set_param(self, key, value):
        """Odoo 20 compatibility shim for set_param."""
        norm_val = '' if value in (False, None) else str(value)
        if hasattr(super(), 'set_param'):
            res = super().set_param(key, value)
        else:
            res = self.set_str(key, norm_val)

        # Keep company-suffixed key in sync when base cloudrefit_invoicing.<key> is set directly
        company = self.env.company
        if (
            company
            and isinstance(key, str)
            and key.startswith('cloudrefit_invoicing.')
            and not key.endswith(f'_{company.id}')
        ):
            comp_key = f'{key}_{company.id}'
            if hasattr(super(), 'set_param'):
                super().set_param(comp_key, norm_val)
            else:
                self.set_str(comp_key, norm_val)
        return res

