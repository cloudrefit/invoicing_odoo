import logging

from odoo import api, SUPERUSER_ID

from . import models

_logger = logging.getLogger(__name__)


def post_init_hook(cr, registry):
    """Run legacy credential migration and trigger initial version check."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    from odoo.addons.cloudrefit_invoicing.data.migrate_legacy_credentials import run_migration
    result = run_migration(env)
    _logger.info("action=post_init_migration result=%s", result)
    
    # Trigger an immediate version check so users don't have to wait 24h
    try:
        env['cloudrefit.version.checker'].check_for_updates()
        _logger.info("action=post_init_version_check status=success")
    except Exception as e:
        _logger.warning("action=post_init_version_check status=failed error=%s", str(e))
