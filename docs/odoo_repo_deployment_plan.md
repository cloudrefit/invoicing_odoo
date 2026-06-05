# CloudRefit Odoo Plugin — Deployment Plan

> **Status: Completed ✅** — The Odoo plugin has been moved to its own repository at [`cloudrefit/invoicing_odoo`](https://github.com/cloudrefit/invoicing_odoo). The old `apps/plugins/odoo/` directory in the `invoicing_platform` monorepo has been removed. This plan is retained for historical reference.

## Repo Naming

> **⚠️ Security & IP Notice**
> `cloudrefit/invoicing_odoo` is a **public repository**. Never commit the following to this repo:
> - API keys, secrets, tokens, or credentials of any kind
> - Proprietary business logic, pricing algorithms, or trade secrets
> - Internal API endpoints, internal service URLs, or infrastructure details
> - Any innovation or intellectual property that gives CloudRefit competitive advantage
>
> The public repo contains **only** the Odoo module integration layer — UI views, standard Odoo models, and calls to CloudRefit's **public API endpoints only**. All sensitive logic lives in the private `invoicing_platform` backend, never in this repo.


| Repo | Visibility | Purpose |
|---|---|---|
| `cloudrefit/invoicing_platform` | Private | Monorepo — all microservices, workers, frontends, plugin sources |
| `cloudrefit/invoicing_odoo` | Public | Odoo modules — Odoo.sh compatible |

> Future pattern: `cloudrefit/invoicing_n8n`, `cloudrefit/invoicing_woocommerce`, etc.

---

## Public Repo Branch Strategy

| Branch | Odoo version | Odoo.sh |
|---|---|---|
| `19.0` | v19 (current) | ✅ auto-detected |
| `17.0` | v17 (future) | ✅ auto-detected |
| `16.0` | v16 (future) | ✅ auto-detected |

Same module code on every branch — only Odoo-version-specific adjustments differ.

---

## Public Repo Folder Structure

```
cloudrefit/invoicing_odoo  (branch: 19.0)
├── cloudrefit_invoicing/
│   ├── __manifest__.py
│   ├── __init__.py
│   ├── models/
│   ├── views/
│   ├── data/
│   └── security/
└── README.md
```

Each root folder = one Odoo module. Add more modules as root folders when needed.

---

## Private → Public Sync (GitHub Actions)

File: `.github/workflows/sync-odoo.yml` in `cloudrefit/invoicing_platform`

```yaml
name: Sync Odoo plugins to public repo

on:
  push:
    branches:
      - main
    paths:
      - 'cloudrefit_invoicing/**'

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout private repo
        uses: actions/checkout@v4

      - name: Push to public repo
        uses: cpina/github-action-push-to-another-repository@main
        env:
          API_TOKEN_GITHUB: ${{ secrets.PUBLIC_REPO_TOKEN }}
        with:
          source-directory: cloudrefit_invoicing
          destination-github-username: cloudrefit
          destination-repository-name: invoicing_odoo
          target-branch: 19.0
          commit-message: "sync: ${{ github.event.head_commit.message }}"
```

**Setup required:**
1. Create a GitHub personal access token (PAT) with `repo` scope
2. Add it as secret `PUBLIC_REPO_TOKEN` in `cloudrefit/invoicing_platform`

---

## Platform Comparison

| Platform | Install method | Python modules | Auto-deploy |
|---|---|---|---|
| Self-hosted Docker | CLI (git clone + restart) | ✅ yes | ❌ manual |
| Self-hosted bare metal | CLI (git clone + restart) | ✅ yes | ❌ manual |
| Odoo.sh | Git push | ✅ yes | ✅ automatic |

> UI upload (`Apps → Import Module`) does **not** work for Python modules on any platform.
> It only supports pure XML/data modules with no custom Python models.

---

## Self-Hosted Docker — One-Time Server Setup

Run once on your Ubuntu server:

```bash
# Ensure addons directory is valid (Odoo requires at least one module)
sudo mkdir -p /data/odoo-19/addons/custom_base
sudo tee /data/odoo-19/addons/custom_base/__manifest__.py << 'MANIFEST'
{
    'name': 'Custom Base',
    'version': '1.0.0',
    'category': 'Hidden',
    'depends': ['base'],
    'installable': True,
}
MANIFEST
sudo touch /data/odoo-19/addons/custom_base/__init__.py
sudo touch /data/odoo-19/addons/__init__.py

# Restart to confirm addons path is accepted
docker restart <your-odoo-container>
docker logs <your-odoo-container> 2>&1 | grep "addons paths:"
# Should show /mnt/extra-addons in the list
```

---

## Self-Hosted Docker — Install a Plugin

```bash
# Clone public repo and copy the module
cd /tmp
git clone -b 19.0 https://github.com/cloudrefit/invoicing_odoo
sudo cp -r /tmp/invoicing_odoo/cloudrefit_invoicing /data/odoo-19/addons/
rm -rf /tmp/invoicing_odoo

# Restart Odoo
docker restart <your-odoo-container>
```

Then in Odoo UI:
1. Settings → Activate developer mode
2. Apps → Update Apps List
3. Search `cloudrefit_invoicing` → Install

---

## Self-Hosted Docker — Update a Plugin

```bash
cd /data/odoo-19/addons/cloudrefit_invoicing
sudo git pull origin 19.0
docker restart <your-odoo-container>
```

Then in Odoo UI: Apps → find the module → Upgrade

---

## Odoo.sh — One-Time Setup

1. Go to Odoo.sh dashboard → Settings → Repositories
2. Connect `cloudrefit/invoicing_odoo`
3. Select branch `19.0`
4. Odoo.sh detects all module folders automatically

---

## Odoo.sh — Install a Plugin

Odoo.sh picks up all modules in the repo automatically on connect.

1. Apps → Update Apps List
2. Search `cloudrefit_invoicing` → Install

---

## Odoo.sh — Update a Plugin

Push changes to branch `19.0` in `cloudrefit/invoicing_odoo` (either directly or via the GitHub Actions sync from the private repo). Odoo.sh redeploys automatically.

Then in Odoo UI: Apps → find the module → Upgrade

---

## Codebase & Security Review Findings

Prior to committing to the public repository strategy, a review of the `cloudrefit_invoicing/` codebase (then at `invoicing_platform/apps/plugins/odoo/community/v19.0`) was conducted. The findings confirm that the plugin is 100% safe to publish publicly:

1. **No Proprietary IP or ZATCA Logic:** The plugin contains only standard Odoo boilerplate views, basic model inheritance, and a payload builder (`_build_zatca_payload`). It contains absolutely **no ZATCA signing algorithms**, XML manipulation, or cryptographic key handling. All complex, proprietary logic (ZATCA enrollment, CSID generation, UBL XML creation, canonicalization, cryptographic signing, QR-code generation, and reporting/clearing submissions) is handled entirely by the CloudRefit backend API. A competitor cannot use this module to bypass the service since they would still need the backend gateway to perform the actual ZATCA operations.
2. **No Hardcoded Secrets:** There are no hardcoded API keys, passwords, private keys, or credentials in the codebase. All authentication credentials (`api_key`, `business_id`, `signing_secret`) are retrieved dynamically from the user's database settings.
3. **API Endpoints:** The plugin interacts only with necessary public endpoints exposed by the CloudRefit ZATCA gateway (`/api/plugin/ping`, `/api/v1/invoices/{business_id}/sign`, and `/api/v1/jobs/{job_uuid}`).

---

## Open Source Strategy

The `cloudrefit/invoicing_odoo` repository will be released as an open-source project (LGPL-3 license). Because all proprietary ZATCA compliance logic is securely hosted on the CloudRefit backend API, open-sourcing the integration plugin carries zero IP risk and provides significant business value:

1. **Trust and Transparency:** Enterprise customers and agencies can audit the code to ensure data security before installing it on their ERP systems.
2. **Community Contributions:** The Odoo community can submit Pull Requests to add features (e.g., POS integration), fix edge-case bugs, and translate the UI, reducing internal maintenance overhead.
3. **Frictionless Distribution:** A public repository allows seamless installation via Odoo.sh and standard CLI without requiring complex access tokens or SSH keys.
4. **Marketing & SEO:** A public GitHub repository acts as a lead generator for the CloudRefit SaaS platform when developers search for Odoo ZATCA solutions.

---

## Next Steps

1. **Create public repo** — `cloudrefit/invoicing_odoo`, create branch `19.0`, push module folder
2. **Set up GitHub Actions** — add sync workflow to `cloudrefit/invoicing_platform`
3. **Fix self-hosted server** — one-time setup above, then clone module
4. **Test end-to-end** — verify install on self-hosted, connect repo on Odoo.sh
5. **Write README** — user-facing install instructions in `cloudrefit/invoicing_odoo/README.md`
