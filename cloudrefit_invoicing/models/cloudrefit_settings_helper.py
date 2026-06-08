"""CloudRefit Per-Company Settings Helper.

Provides _cr_get_param / _cr_set_param for company-scoped configuration
resolution across all models in the module.
"""

from odoo import models, api
import logging

_logger = logging.getLogger(__name__)


class CloudrefitSettingsHelper(models.AbstractModel):
    _name = 'cloudrefit.settings.helper'
    _description = 'CloudRefit Per-Company Settings Helper'

    @api.model
    def _cr_get_param(self, param_name, default=None):
        """Resolve a config parameter for the current company.

        Strategy (priority order):
        1. param_name_{company_id}  — company-specific key
        2. param_name               — global fallback (backward compat)

        Returns None or default if neither exists.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        company = self.env.company
        if company:
            company_key = f'{param_name}_{company.id}'
            value = ICP.get_param(company_key)
            if value not in (None, '', False):
                return value
        global_value = ICP.get_param(param_name)
        if global_value not in (None, '', False):
            return global_value
        return default

    @api.model
    def _cr_set_param(self, param_name, value):
        """Write a config parameter for the current company only.

        Always writes to param_name_{company_id} when company context exists;
        falls back to the base key only when there is no company.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        company = self.env.company
        if company:
            company_key = f'{param_name}_{company.id}'
            ICP.set_param(company_key, value)
        else:
            ICP.set_param(param_name, value)
