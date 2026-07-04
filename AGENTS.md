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

---

## ZATCA Validation Sync Rule

The platform enforces ZATCA buyer/seller identity rules in **two independent layers**:

| Layer | Language | File |
| :--- | :--- | :--- |
| Gateway API + Dashboard UI | TypeScript | `packages/shared/src/validation.ts` |
| Odoo Plugin | Python | `cloudrefit_invoicing/models/res_partner.py`, `account_move.py` |

Because these are separate languages, they **cannot share code**. Any rule change must be applied manually to both.

> **RULE:** When ZATCA validation rules change in `packages/shared/src/validation.ts` (e.g. VAT regex, building_no pattern, postal code format, new required fields), the corresponding Python constraints in `res_partner.py` and `account_move.py` MUST be updated in the **same release**.

### Release Checklist — Any Version Bump Touching Validation

Before pushing a version that modifies any of the following in the TypeScript layer, check the Python layer:

- [ ] VAT regex (`/^3\d{13}3$/`) — verify same pattern in `res_partner.py`
- [ ] Building No pattern (`/^\d{4}$/`) — verify same in `res_partner.py`
- [ ] Postal Code pattern (`/^\d{5}$/`) — verify same in `res_partner.py`
- [ ] District required check — verify non-empty check exists in `res_partner.py`
- [ ] Other ID type enum values — verify same selection list in `res_partner.py`
- [ ] Other ID value format (`/^[a-zA-Z0-9]*$/`) — verify same in `res_partner.py`
- [ ] B2C required fields (name + phone) — verify same in `res_partner.py`
- [ ] Any new field added to `isZatcaCustomerComplete()` — add corresponding check to `_check_zatca_compliance()` and `_build_zatca_payload()`

> Failing to keep these in sync will cause Odoo to accept data that the Gateway API will later reject at invoice push time, creating confusing user-facing errors.
