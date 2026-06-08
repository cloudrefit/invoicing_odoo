"""Migrate legacy cloudrefit_zatca.* config params to cloudrefit_invoicing.* on upgrade.

Odoo calls this script during a module **upgrade** (only).  It mirrors the
``post_init_hook`` in ``__init__.py``, which runs on **install** only.

.. note::
    The ``post_init_hook`` (install) and this migration script (upgrade) share
    the same core logic via ``run_migration()``, so both code paths stay in
    sync.

Version correspondence
----------------------
This script lives under ``migrations/19.0.2.34/`` — the target (new) version.
Odoo will execute it when upgrading from any older module version to 19.0.2.34.
"""

import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Run the legacy → modern credential migration.

    Args:
        cr: Database cursor (provided by Odoo's migration framework).
        version: The **currently installed** module version (the version being
                 upgraded *from*), or ``None`` on a fresh install.
    """
    _logger.info(
        "action=migration_start module=cloudrefit_invoicing "
        "target_version=19.0.2.34 from_version=%s",
        version,
    )

    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.cloudrefit_invoicing.data.migrate_legacy_credentials import (
        run_migration,
    )

    result = run_migration(env)
    _logger.info("action=migration_complete result=%s", result)
