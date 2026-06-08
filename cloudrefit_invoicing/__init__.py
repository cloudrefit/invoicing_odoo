import logging

from odoo import api, SUPERUSER_ID

from . import models

_logger = logging.getLogger(__name__)


def post_init_hook(cr, registry):
    """Run legacy credential migration once on module install/upgrade."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.cloudrefit_invoicing.data.migrate_legacy_credentials import run_migration
    result = run_migration(env)
    _logger.info("action=post_init_migration result=%s", result)
