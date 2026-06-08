# CloudRefit ZATCA E-Invoicing Plugin for Odoo 19

Automated ZATCA (Zakat, Tax and Customs Authority) e-invoicing compliance for Odoo 19 Community Edition.

## Features

- **Automated Invoice Signing** — Invoices are automatically signed and submitted to ZATCA upon posting
- **Single-Page Activation** — Paste your CloudRefit dashboard API keys and click "Save & Connect"
- **Connection Health Indicator** — Real-time gateway connectivity status (green/red)
- **Sandbox Testing Module** — Test invoices in ZATCA sandbox without affecting production records
- **Production Guardrails** — Confirmation dialog before production submission, 15-day rule warning
- **B2B & B2C Support** — Standard tax invoices (B2B) and simplified tax invoices (B2C)
- **Async Job Polling** — Handles ZATCA async signing with automatic retry and backoff
- **PDF XML Embedding** — Signed ZATCA XML embedded in printed invoice PDFs
- **Arabic Localization** — Full Arabic translation for all UI elements
- **Retry Pipeline** — Automatic retry for failed submissions with exponential backoff

## Prerequisites

- Odoo 19 Community Edition
- Python 3.x
- CloudRefit account with API keys (register at dashboard.cloudrefit.com)
- Required Odoo modules: `account`, `base`

## Installation

### Odoo.sh

1. Connect this repository in your Odoo.sh dashboard
2. Select the branch matching your Odoo version (e.g., `19.0`)
3. Odoo.sh auto-detects and deploys the module

### Self-Hosted (Docker / Bare Metal)

Clone the repository and copy the module to your Odoo addons:

```bash
# Clone the repo
git clone -b 19.0 https://github.com/cloudrefit/invoicing_odoo.git /tmp/invoicing_odoo

# Copy module to your Odoo addons
sudo cp -r /tmp/invoicing_odoo/cloudrefit_invoicing /path/to/odoo/addons/

# Restart Odoo
sudo systemctl restart odoo     # systemd
# or
docker restart <container-name> # Docker
```

Alternatively, clone directly into your addons path:

```bash
git clone -b 19.0 https://github.com/cloudrefit/invoicing_odoo.git /path/to/odoo/addons/invoicing_odoo
```

Then in Odoo UI:
1. Settings → Activate Developer Mode
2. Apps → Update Apps List
3. Search "CloudRefit" → Install

## Configuration

1. Go to **Settings** → **CloudRefit ZATCA**
2. Copy your **API Key** and **Signing Secret** from the CloudRefit Dashboard (dashboard.cloudrefit.com → API Keys)
3. Enter your **Business ID** and **Technical Unit IDs** (sandbox and production)
4. Set the default **Invoice Type** and **Mode** (start with sandbox for testing)
5. Click **Save & Connect** — the health indicator should turn green

## Usage

### Signing Invoices
- Post an invoice as normal — it will be automatically signed and submitted to ZATCA
- Manual push: Open a posted invoice → **ZATCA** tab → **Push to ZATCA**
- Monitor status in the ZATCA tab: cleared, reported, failed, pending

### Sandbox Testing
- Go to **Settings** → **CloudRefit ZATCA** → **🧪 Sandbox Testing**
- Select a posted invoice and click **Test in Sandbox**
- Results are temporary and do not affect the actual invoice

### Retry Failed Invoices
- Failed invoices are automatically retried every 30 minutes (up to 3 attempts)
- Check the **ZATCA Error** field on the invoice for failure details

## Updating

### Self-Hosted
```bash
cd /path/to/odoo/addons/cloudrefit_invoicing
git pull origin 19.0
# Restart Odoo, then Apps → Upgrade module
```

### Odoo.sh
Updates happen automatically on push to the branch.

## Versioning

This module uses version format `19.0.{minor}.{commit_count}`. Run the build script to inject the git-derived commit count:

```bash
python scripts/build/build_plugin.py
```

## Support
- Documentation: docs.cloudrefit.com
- Dashboard: dashboard.cloudrefit.com
- Email: support@cloudrefit.com

## Version History
- **19.0.1.0** — Git-based deployment; ZIP distribution deprecated
- **19.0.1.2.0** — Major refactoring: security hardening, single-page activation, health indicator, sandbox testing module, 68 unit tests
- **19.0.1.1.41** — Initial release with ZATCA compliance pipeline and setup wizard
