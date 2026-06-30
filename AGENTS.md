# AI Agent Rules — CloudRefit Odoo Plugin

## Before Any `git push` to the `19.0` Branch

1. Check if `cloudrefit_invoicing/__manifest__.py` version has changed in this commit.
2. If YES — you MUST review and update `RELEASE.json` before pushing:
   - Update `version` to match `__manifest__.py`
   - Update `release_date` to today's date
   - Set `severity` based on the nature of changes:
     - Bug fixes only → `minor`
     - Important fixes / new features → `major` (Also update `latest_major_version`)
     - Security or compliance issues → `critical` (Also update `latest_critical_version`)
     - Imminent ZATCA deadline → `urgent` (Also update `latest_urgent_version`)
     - Breaking API change → `blocked` (Also update `latest_blocked_version` and `min_compatible_version`)
   - Update `title` and `message` to describe the changes clearly
   - NEVER clear out older historical versions (e.g., if releasing `minor`, leave `latest_blocked_version` untouched).
3. If manifest version did NOT change — `RELEASE.json` does not need updating.

> Never push a `__manifest__.py` version bump without a corresponding `RELEASE.json` update.
