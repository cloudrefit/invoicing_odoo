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
        if hasattr(super(), 'set_param'):
            return super().set_param(key, value)
        return self.set_str(key, value)
