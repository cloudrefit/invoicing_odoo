"""
One-time migration script for legacy ZATCA credential fields.

Reads legacy ``ir.config_parameter`` values (from the old ``cloudrefit_zatca.*``
namespace) and populates the modern ``cloudrefit_invoicing.*`` equivalents.

This script is designed to be run manually via the Odoo shell or as a server
action after upgrading to a version that uses the ``cloudrefit_invoicing.*``
namespace.

Legacy → Modern mapping
----------------------
Known credential keys (``cloudrefit_zatca.`` prefix → ``cloudrefit_invoicing.``):

  - api_key           → api_key_live
  - gateway_url       → gateway_url_live
  - business_id       → business_id_live
  - signing_secret    → signing_secret_live
  - unit_id           → unit_id_live
  - enabled           → live_enabled
  - sandbox_enabled   → sandbox_enabled

Suffixed keys (e.g. ``cloudrefit_zatca.gateway_url_2`` where ``_2`` is a
company-id suffix) are handled automatically — the suffix is preserved on
the target key, e.g. ``cloudrefit_invoicing.gateway_url_live_2``.

Migration behaviour
-------------------
- Reads ALL ``ir.config_parameter`` rows WHERE key LIKE ``cloudrefit_zatca.%``.
- For each legacy key, strips the ``cloudrefit_zatca.`` prefix to derive
  the base name, then maps it to the appropriate new key.
- Only creates the new key if it does NOT already exist (never overwrites).
- Logs every migration and skip.
"""

import logging
import re

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping: legacy short-name -> target short-name
# ---------------------------------------------------------------------------
# Keys that were "plain" credentials in the old namespace get a ``_live``
# suffix in the new namespace.  Special cases (enabled flags) are mapped
# explicitly.
_LIVE_CREDENTIAL_KEYS = {
    'api_key',
    'gateway_url',
    'business_id',
    'signing_secret',
    'unit_id',
}

_SPECIAL_MAP = {
    'enabled':          'live_enabled',
    'sandbox_enabled':  'sandbox_enabled',
}

_LEGACY_PREFIX = 'cloudrefit_zatca.'
_NEW_PREFIX = 'cloudrefit_invoicing.'


def _map_key(legacy_key):
    """Map a legacy ``cloudrefit_zatca.xxx`` key to the equivalent
    ``cloudrefit_invoicing.yyy`` key, preserving any company-id suffix.

    Returns ``None`` if the key is not recognised.
    """
    if not legacy_key.startswith(_LEGACY_PREFIX):
        return None

    rest = legacy_key[len(_LEGACY_PREFIX):]  # e.g. "gateway_url_2"

    # Try to match a trailing _<digits> suffix (company-id suffix)
    match = re.match(r'^(.+?)(_\d+)$', rest)
    if match:
        base_name = match.group(1)
        suffix = match.group(2)          # e.g. "_2"
    else:
        base_name = rest
        suffix = ''

    # Determine target short name
    if base_name in _SPECIAL_MAP:
        target_short = _SPECIAL_MAP[base_name] + suffix
    elif base_name in _LIVE_CREDENTIAL_KEYS:
        target_short = base_name + '_live' + suffix
    else:
        _logger.warning(
            "action=unknown_legacy_key legacy_key=%s base_name=%s",
            legacy_key, base_name,
        )
        return None

    return _NEW_PREFIX + target_short


def migrate_legacy_credentials(env):
    """Read legacy ``ir.config_parameter`` values and populate new equivalents.

    Args:
        env: Odoo environment (e.g. ``self.env`` from a model method, or
             ``env`` in an Odoo shell session).

    Returns:
        dict with keys ``migrated`` (list of successful migrations) and
        ``skipped`` (list of params already set or no legacy value).
    """
    ICP = env['ir.config_parameter'].sudo()
    migrated = []
    skipped = []

    # 1. Find ALL legacy keys at once
    env.cr.execute(
        "SELECT key, value FROM ir_config_parameter WHERE key LIKE %s",
        (_LEGACY_PREFIX + '%',),
    )
    legacy_rows = env.cr.fetchall()

    if not legacy_rows:
        _logger.info("action=no_legacy_keys_found prefix=%s", _LEGACY_PREFIX)
        return {'migrated': migrated, 'skipped': skipped}

    _logger.info(
        "action=found_legacy_keys count=%d prefix=%s",
        len(legacy_rows), _LEGACY_PREFIX,
    )

    for legacy_key, legacy_val in legacy_rows:
        new_key = _map_key(legacy_key)
        if new_key is None:
            skipped.append({'key': legacy_key, 'reason': 'unrecognised_key'})
            continue

        # 2. Don't overwrite an existing new-key value
        existing_val = ICP.get_param(new_key)
        if existing_val:
            _logger.info(
                "action=skip_migration reason=target_already_set "
                "legacy_key=%s new_key=%s existing_value=%r",
                legacy_key, new_key, existing_val,
            )
            skipped.append({'key': legacy_key, 'reason': 'target_already_set'})
            continue

        # 3. Perform migration
        ICP.set_param(new_key, legacy_val)
        _logger.info(
            "action=migrated_credential legacy_key=%s new_key=%s value=%r",
            legacy_key, new_key, legacy_val,
        )
        migrated.append({'legacy_key': legacy_key, 'new_key': new_key})

    return {
        'migrated': migrated,
        'skipped': skipped,
    }


def run_migration(env):
    """Convenience wrapper that logs and returns a summary string.

    Call this from an Odoo shell::

        from odoo.addons.cloudrefit_invoicing.data.migrate_legacy_credentials import run_migration
        result = run_migration(env)
        print(result)
    """
    result = migrate_legacy_credentials(env)
    summary_parts = []

    if result['migrated']:
        summary_parts.append(
            f"Migrated {len(result['migrated'])} credential(s): "
            + ', '.join(f"{m['legacy_key']} -> {m['new_key']}" for m in result['migrated'])
        )
    if result['skipped']:
        summary_parts.append(
            f"Skipped {len(result['skipped'])} credential(s): "
            + ', '.join(s['key'] for s in result['skipped'])
        )

    if not summary_parts:
        return "No legacy credentials found to migrate."

    return ' | '.join(summary_parts)
