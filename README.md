# CloudRefit ZATCA e-Invoicing for Odoo

Seamlessly integrate Saudi Arabia's ZATCA e-invoicing compliance into your Odoo ERP.

[![License: LGPL-3.0](https://img.shields.io/badge/License-LGPL--3.0-blue.svg)](LICENSE)
[![Odoo Version](https://img.shields.io/badge/Odoo-17.0-8A2BE2)](https://www.odoo.com)

## Overview

This module connects your Odoo instance to the CloudRefit ZATCA Gateway, enabling full e-invoicing compliance without exposing sensitive operations to your ERP.

**All complex ZATCA logic** — XML canonicalization, ZATCA signing, CSID management, QR-code generation, and API submission — is handled securely by the CloudRefit backend. This module provides only the lightweight integration layer within Odoo.

## Features

- 🔐 Zero secrets in code — all credentials configured via Odoo settings UI
- 📄 Automatic invoice submission on validation
- 🔄 Real-time status tracking (submitted → signed → cleared)
- 🧾 PDF QR-code embedding for printed invoices
- 🧪 Built-in sandbox/test mode for onboarding
- 🌐 Arabic (ar) UI translation included
- 🏗️ Odoo.sh compatible — auto-deploy on push

## Installation

### Odoo.sh
1. Connect this repository in your Odoo.sh dashboard
2. Select the branch matching your Odoo version (e.g., `17.0`)
3. Odoo.sh auto-detects and deploys the module

### Self-Hosted (Docker)

#### Recommended: Direct Git Volume Mount
Clone the repository directly into your host machine and mount it in your `docker-compose.yml`:

```yaml
volumes:
  - /path/to/invoicing_odoo/cloudrefit_invoicing:/mnt/extra-addons/cloudrefit_invoicing
```

To install:
```bash
git clone -b 17.0 https://github.com/cloudrefit/invoicing_odoo /path/to/invoicing_odoo
docker compose restart odoo
```

#### Alternative: Copy to Addons
```bash
git clone -b 17.0 https://github.com/cloudrefit/invoicing_odoo /tmp/invoicing_odoo
sudo cp -r /tmp/invoicing_odoo/cloudrefit_invoicing /data/odoo/addons/
rm -rf /tmp/invoicing_odoo
docker restart <your-odoo-container>
```

Then in Odoo UI:
1. Settings → Activate Developer Mode
2. Apps → Update Apps List
3. Search "CloudRefit" → Install

### Self-Hosted (Bare Metal)
```bash
git clone -b 19.0 https://github.com/cloudrefit/invoicing_odoo /path/to/odoo/addons/invoicing_odoo
# Restart your Odoo server
```

## Configuration

After installation, go to **Settings → CloudRefit ZATCA** and enter:
1. Your CloudRefit API key
2. Your Business ID
3. (Optional) Custom gateway URL

Get your API key at [https://invoicing.cloudrefit.com](https://invoicing.cloudrefit.com).

## Updating

```bash
# Pull latest changes
cd /path/to/invoicing_odoo
git pull origin 19.0

# Option A: Restart Odoo container and upgrade via UI
docker compose restart odoo
# Then in Odoo UI: Apps → Search "CloudRefit" → Upgrade

# Option B: Upgrade directly via Odoo CLI
docker exec <odoo-container> odoo -u cloudrefit_invoicing -d <dbname> --stop-after-init
docker compose restart odoo
```

Odoo.sh updates automatically on push to the branch.

## Supported Odoo Versions

| Branch | Odoo Version |
|--------|-------------|
| `19.0` | Odoo 19 |
| `17.0` | Odoo 17 (planned) |
| `16.0` | Odoo 16 (planned) |

## License

LGPL-3.0 — see [LICENSE](LICENSE) file.

## Support

- Documentation: [https://invoicing.cloudrefit.com/docs](https://invoicing.cloudrefit.com/docs)
- Issues: [GitHub Issues](https://github.com/cloudrefit/invoicing_odoo/issues)
- Commercial Support: [https://cloudrefit.com/contact](https://cloudrefit.com/contact)

