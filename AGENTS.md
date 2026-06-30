# AI Agent Rules — CloudRefit Odoo Plugin

## Before Any `git push` to the `19.0` Branch

1. Check if `cloudrefit_invoicing/__manifest__.py` version has changed in this commit.
2. If YES — you MUST review and update `RELEASE.json` before pushing:
   - Update `version` to match `__manifest__.py`
   - Update `release_date` to today's date
   - Set `severity` based on the nature of changes:
     - Bug fixes only → `minor`
     - Important fixes / new features → `major`
     - Security or compliance issues → `critical`
     - Imminent ZATCA deadline → `urgent`
     - Breaking API change → `blocked` + set `min_compatible_version`
   - Update `title` and `message` to describe the changes clearly
3. If manifest version did NOT change — `RELEASE.json` does not need updating.

> Never push a `__manifest__.py` version bump without a corresponding `RELEASE.json` update.
