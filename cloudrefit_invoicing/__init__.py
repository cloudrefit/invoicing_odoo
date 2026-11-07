import logging

from odoo import api, SUPERUSER_ID

from . import controllers
from . import models
from . import wizard

_logger = logging.getLogger(__name__)


def post_init_hook(*args, **kwargs):
    """Run legacy credential migration and trigger initial version check."""
    # Odoo 19+ passes env directly: post_init_hook(env)
    # Odoo <=18 passes (cr, registry): post_init_hook(cr, registry)
    if args and hasattr(args[0], 'cr'):
        env = args[0]
    elif args and len(args) >= 1:
        cr = args[0]
        env = api.Environment(cr, SUPERUSER_ID, {})
    else:
        env = api.Environment(api.Environment.manage(), SUPERUSER_ID, {})
    
    try:
        from odoo.addons.cloudrefit_invoicing.data.migrate_legacy_credentials import run_migration
        result = run_migration(env)
        _logger.info("action=post_init_migration result=%s", result)
    except (ImportError, ModuleNotFoundError):
        _logger.info("action=post_init_migration status=skipped reason=no_legacy_credentials_script")
    
    # Trigger an immediate version check so users don't have to wait 24h
    try:
        env['cloudrefit.version.checker'].check_for_updates()
        _logger.info("action=post_init_version_check status=success")
    except Exception as e:
        _logger.warning("action=post_init_version_check status=failed error=%s", str(e))
