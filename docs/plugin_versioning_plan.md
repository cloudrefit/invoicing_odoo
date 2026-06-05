# Odoo Plugin Versioning: Git-Commit-Linked Scheme

> **Note:** The Odoo plugin has been moved to its own repository at [`invoicing_odoo`](https://github.com/cloudrefit/invoicing_odoo). All file paths in this plan reference paths within this repo (`cloudrefit_invoicing/`). The versioning strategy described below is still valid and should be implemented in the new repository.

## 1. Current State Summary

### 1.1 Manifest Version ([`__manifest__.py`](cloudrefit_invoicing/__manifest__.py:3))

```python
'version': '19.0.1.3.0',
```

The version follows a manual 5-segment Odoo convention: `{odoo_series}.{major}.{minor}.{patch}.{subpatch}`. It is manually bumped by developers and has no automated link to git history.

**Version history from [`README.md`](cloudrefit_invoicing/README.md:62):**
- `19.0.1.1.41` — Initial release with ZATCA signing pipeline
- `19.0.1.2.0` — Major refactoring: security hardening, single-page activation, sandbox testing, 68 unit tests
- `19.0.1.3.0` — UI Upload Compatibility (current)

### 1.2 Build Script ([`scripts/build/build_plugin.py`](scripts/build/build_plugin.py))

The build flow:
1. **Reads** version from `__manifest__.py` via regex (line 28): `r"'version':\s*'([^']+)'"`
2. **Injects** version into [`static/description/index.html`](cloudrefit_invoicing/static/description/index.html:55) replacing `Version: [0-9.]+` and `Build: ...` patterns
3. **Packages** everything into `cloudrefit_invoicing_v19.zip`
4. **Optionally restores** modified files via `git checkout --` (`--restore` flag)

**Key observation:** The manifest version is the single source of truth. The build script does NOT modify the manifest — it only reads from it and injects into `index.html`. This means the manifest version in the ZIP is whatever was last committed.

### 1.3 Stale Version in index.html

[`static/description/index.html`](cloudrefit_invoicing/static/description/index.html:55) shows `19.0.0.3.6` which is **stale** — it does not match the manifest's `19.0.1.3.0`. This is because `--restore` was not used after the last build, or the file was not rebuilt. The build script's injection targets lines 54-55 (`Version:`) and line 59 (`Build:`).

### 1.4 Odoo Migration Scripts

Odoo uses version strings to locate migration scripts. The directory [`migrations/19.0.1.3.0/`](cloudrefit_invoicing/migrations/19.0.1.3.0/pre-migrate.py) corresponds to version `19.0.1.3.0`. When Odoo upgrades a module, it runs all migrations from directories whose version falls between the old and new version (using integer-tuple comparison).

**Critical implication:** Migration directory names must match the manifest version. Any new versioning scheme must produce version strings that can be used as migration directory names.

### 1.5 Odoo Version Format Constraints

- Odoo's `version` field in `__manifest__.py` must be a **dot-separated string of integers** (e.g., `19.0.1.3.0`)
- Odoo compares versions as **integer tuples**: `(19, 0, 1, 3, 0)` — purely numeric, left-to-right
- Letters, hyphens, plus signs (`+`), and other non-numeric characters are **NOT allowed**
- There is no hard limit on the number of segments, but 4–5 is typical convention
- The first segment conventionally indicates the Odoo series (e.g., `19.0` for Odoo 19)
- Version changes trigger upgrade detection — Odoo runs pending migration scripts

---

## 2. Proposed Version Format

### 2.1 Format Specification

```
{odoo_series}.{minor}.{commit_count}
```

| Segment | Source | Example | Description |
|---------|--------|---------|-------------|
| `odoo_series` | Fixed constant | `19.0` | Odoo major version this plugin targets |
| `minor` | Manual (in manifest) | `1` | Bumped for breaking changes or major feature releases |
| `commit_count` | Git auto-derived | `247` | Total commits affecting plugin path in the `invoicing_odoo` repo |

### 2.2 Examples

| Scenario | Version |
|----------|---------|
| First build after adoption (247 commits in plugin history) | `19.0.1.247` |
| After 5 more commits | `19.0.1.252` |
| After bumping minor for a breaking change, 260 total commits | `19.0.2.260` |
| Next commit after minor bump | `19.0.2.261` |

### 2.3 Why This Format?

- ✅ **100% Odoo-compatible** — pure dot-separated integers
- ✅ **Monotonically increasing** — Odoo upgrade detection works correctly
- ✅ **Fully automated** — `commit_count` requires zero manual intervention
- ✅ **Traceable to git** — `git rev-list --count HEAD -- cloudrefit_invoicing/` gives exact commit count
- ✅ **Migration-friendly** — migration directories like `migrations/19.0.1.247/` work naturally
- ✅ **Human-readable** — you can tell at a glance the Odoo series, feature generation, and build number

### 2.4 Alternatives Considered

| Format | Example | Verdict |
|--------|---------|---------|
| `19.0.1.{count}+{hash}` | `19.0.1.247+a3f2c91` | ❌ `+` not allowed in Odoo versions |
| `19.0.{YYMMDD}.{count}` | `19.0.250605.3` | ⚠️ Works but obscures feature generation |
| `19.0.{minor}.{patch}` (git describe) | `19.0.1.247` | ❌ Requires git tags on every release |
| `19.0.{count}` | `19.0.247` | ⚠️ Loses feature-generation grouping |
| **`19.0.{minor}.{commit_count}`** | **`19.0.1.247`** | ✅ **Best balance of automation, readability, and compatibility** |

---

## 3. How the Build Script Will Derive the Version

### 3.1 Git Command

```bash
git rev-list --count HEAD -- cloudrefit_invoicing/
```

This counts all commits in the current branch that touched any file under `cloudrefit_invoicing/` (the module source directory within the `invoicing_odoo` repo). It is:
- **Deterministic** — same HEAD always gives the same count
- **Shallow-clone safe** — works even in shallow clones (counts commits in the available history)
- **Path-scoped** — only counts commits relevant to the plugin, not the whole repo

### 3.2 New Build Flow

```
┌─────────────────────────────────────────────────────────────┐
│  1. Read minor version from __manifest__.py placeholder     │
│     (e.g., 'version': '19.0.1.0')                           │
│  2. Run: git rev-list --count HEAD -- cloudrefit_invoicing/ │
│     → commit_count = 247                                    │
│  3. Construct: version = f"19.0.{minor}.{commit_count}"     │
│     → "19.0.1.247"                                          │
│  4. Write computed version INTO __manifest__.py             │
│     (replaces the placeholder)                              │
│  5. Inject version INTO static/description/index.html       │
│     (existing behavior, preserved)                          │
│  6. Package ZIP with the correct version baked in           │
│  7. If --restore: git checkout both manifest + index.html   │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 Edge Case Handling

| Edge Case | Detection | Behavior |
|-----------|-----------|----------|
| **Dirty working tree** | `git diff --quiet` (exit code ≠ 0) | Print warning: "Working tree is dirty — version may not be reproducible." Continue with commit count from HEAD. |
| **No git available** | `subprocess.CalledProcessError` / `FileNotFoundError` | Fall back to reading version from manifest as-is (keep placeholder). Print warning: "Git not available — using static manifest version." |
| **Not in a git repo** | `git rev-parse --git-dir` fails | Same as "no git available" fallback. |
| **Shallow clone** | Transparent; `rev-list --count HEAD` works | Counts only available commits. Acceptable — just means version is a lower bound. |
| **No commits touching plugin** | `commit_count == 0` | Use `1` as minimum (version `19.0.1.1` instead of `19.0.1.0` which may be treated as "unversioned" by Odoo). |

### 3.4 Manifest Placeholder Convention

The `__manifest__.py` in source control will use a **placeholder minor** value:

```python
'version': '19.0.1.0',   # minor=1; commit_count injected at build time
```

The `minor` segment (third number) is the only manually-managed value. Developers bump it when:
- A breaking schema change is introduced (requiring migration scripts)
- A major feature set is completed
- The plugin is forked for a new Odoo series

The `commit_count` segment (fourth number) is ALWAYS overwritten at build time.

---

## 4. Migration Steps

### 4.1 Step-by-Step Transition

```
Phase 1: Prepare Source (one-time manual changes)
─────────────────────────────────────────────────
 1. Update __manifest__.py version to '19.0.1.0'
    (sets minor=1 as the starting generation)
    
 2. Rename migrations directory:
    migrations/19.0.1.3.0/ → migrations/19.0.1.0/
    (This migration already ran for existing installs;
     Odoo won't re-run it because it tracks applied versions
     in ir.module.module.  The rename is for cleanliness.)
    
    ⚠️ IMPORTANT: If there are Odoo instances currently on
    19.0.1.3.0, they will see 19.0.1.{N} (where N > 3) as
    an upgrade and will attempt to run pending migrations.
    Since 19.0.1.3.0 > 19.0.1.0 (tuple comparison), the
    renamed migration directory will NOT be re-executed.
    
    However, if any NEW migration directories are added
    with versions between 19.0.1.3.0 and the new version,
    they WILL run. This is expected behavior.

 3. Update README.md version history to reflect the new scheme

Phase 2: Update Build Script
─────────────────────────────
 4. Refactor build_plugin.py:
    - Add get_commit_count() function using git
    - Add get_minor_version() that reads from manifest
    - Modify inject_version() to compute full version
    - Add __manifest__.py to TARGET_FILES for injection + restore
    - Add edge case handling (dirty tree, no git, etc.)

Phase 3: First Build & Validate
────────────────────────────────
 5. Run build script → verify output shows computed version
 6. Inspect ZIP contents → manifest has correct version
 7. Test in Odoo → module installs, version displays correctly
 8. Test upgrade path → install old version, upgrade to new,
    confirm no spurious migration runs
```

### 4.2 Rollback Plan

If the new scheme causes issues:
1. Revert `__manifest__.py` to manual version `19.0.1.3.0`
2. Rename `migrations/19.0.1.0/` back to `migrations/19.0.1.3.0/`
3. Revert `build_plugin.py` to read-only behavior
4. All changes are confined to 3 files — low risk

---

## 5. Odoo Compatibility Confirmation

### 5.1 Version Comparison

Odoo uses Python's `tuple(int(x) for x in version.split('.'))` for version comparison:

```python
# Example: 19.0.1.247 vs 19.0.1.3
(19, 0, 1, 247) > (19, 0, 1, 3)  # True → triggers upgrade
```

The proposed `19.0.1.{commit_count}` format works perfectly because:
- All segments are pure integers
- `commit_count` is monotonically increasing
- Odoo correctly identifies newer versions

### 5.2 Migration Script Discovery

Odoo scans `migrations/` for subdirectories matching version patterns. With `19.0.1.247`:
- Odoo looks for `migrations/19.0.1.247/` — **finds it if it exists**
- Migration directory names must match exactly

### 5.3 Module Upgrade Detection

When a user updates the plugin ZIP and upgrades in Odoo:
1. Odoo compares the new manifest version with the stored version in `ir.module.module`
2. If new > old, Odoo marks the module as "to upgrade"
3. On upgrade, Odoo runs pending migration scripts
4. Odoo updates the stored version

This flow is **fully compatible** with the proposed scheme.

### 5.4 Odoo App Store Considerations

If the plugin is published on the Odoo App Store:
- The store expects versions to follow `{odoo_series}.{major}.{minor}.{patch}...`
- `19.0.1.247` is valid format
- However, frequent version bumps (every commit) may look unusual in the store listing
- **Recommendation:** For App Store releases, tag specific commits (e.g., `v19.0.1.250`) and use those as stable releases

---

## 6. Recommendations & Trade-offs

### 6.1 Advantages

| Advantage | Detail |
|-----------|--------|
| **Zero manual version bumps** | Commit count is fully automated |
| **Full git traceability** | `git rev-list --count` gives exact build provenance |
| **No new dependencies** | Only uses `git` CLI (already required by `--restore`) |
| **Odoo-native** | Uses only standard Odoo versioning features |
| **Migration-safe** | Migration directories continue to work normally |

### 6.2 Trade-offs

| Trade-off | Mitigation |
|-----------|------------|
| **Version changes on every commit** — even typo fixes bump the version | Acceptable: this is the point of commit-linked versioning. Odoo only runs migrations if scripts exist for the new version. |
| **Rebasing changes commit count** — `git rebase` rewrites history, changing counts | Use `--restore` after build to revert manifest. For release builds, always build from a stable branch (e.g., `main`), not a rebased feature branch. |
| **Cross-branch inconsistency** — `main` and `feature-x` may have different commit counts | Only build releases from `main`. Add a CI check: "is working tree clean + on main branch?" |
| **Commit count grows indefinitely** — could reach 5+ digits over years | Odoo handles this fine. To reset: bump `minor` and the count naturally drops relative to the new scope (use `git rev-list --count HEAD --since="..."` or just accept the large number). |

### 6.3 Future Enhancements

1. **Add short commit hash to ZIP filename** (not manifest):
   - `cloudrefit_invoicing_v19.0.1.247_a3f2c91.zip`
   - Provides exact commit traceability without violating Odoo version constraints

2. **CI/CD integration**:
   - GitHub Actions / GitLab CI can run the build script
   - Auto-attach ZIP to releases
   - Auto-tag releases with the computed version

3. **Version validation in CI**:
   - Check that `minor` matches between manifest and the latest migration directory
   - Block PRs that add migration scripts without bumping `minor`

4. **Pre-release / dev marker**:
   - For dirty trees, append `.99999` to indicate "non-release build"
   - Example: `19.0.1.247.99999` — Odoo treats this as a pre-release of `19.0.1.248`
   - Only used in dev/CI builds, never in production releases

---

## Appendix A: Affected Files Summary

| File | Change | Type |
|------|--------|------|
| [`__manifest__.py`](cloudrefit_invoicing/__manifest__.py:3) | Version placeholder `19.0.1.0`; injected at build time | Modify |
| [`scripts/build/build_plugin.py`](scripts/build/build_plugin.py) | Add git-based version computation; inject into manifest | Major refactor |
| [`static/description/index.html`](cloudrefit_invoicing/static/description/index.html:55) | No change (already receives injected version) | None |
| [`README.md`](cloudrefit_invoicing/README.md:62) | Update version history examples | Minor |
| `migrations/19.0.1.3.0/` → `migrations/19.0.1.0/` | Rename to match new placeholder minor | Rename |

## Appendix B: Proposed Build Script Pseudocode

```python
def get_minor_version():
    """Extract minor segment from manifest placeholder."""
    # manifest has: 'version': '19.0.{minor}.0'
    # Return: minor (e.g., 1)
    ...

def get_commit_count():
    """Count commits touching the plugin directory."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD", "--", "cloudrefit_invoicing/"],
            capture_output=True, text=True, check=True
        )
        count = int(result.stdout.strip())
        return max(count, 1)  # minimum 1
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None  # signal fallback

def is_working_tree_clean():
    """Check for uncommitted changes."""
    ...

def compute_version():
    """Compute full version string."""
    minor = get_minor_version()
    count = get_commit_count()
    if count is None:
        # Fallback: read version from manifest as-is
        return get_manifest_version()
    return f"19.0.{minor}.{count}"

def inject_version():
    """Inject computed version into manifest AND index.html."""
    version = compute_version()
    ...
    # NEW: Write into __manifest__.py
    # EXISTING: Write into index.html
    ...

```

---

*Plan generated 2026-06-05. Updated 2026-06-05: paths updated to reflect plugin move to `invoicing_odoo` repository.*
