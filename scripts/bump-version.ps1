<#
.SYNOPSIS
  Bumps the 4th version segment and syncs to __manifest__.py.
  Reads VERSION, increments last segment, writes back, then updates manifest.
#>
$ErrorActionPreference = "Stop"
$root = git rev-parse --show-toplevel
$versionFile = Join-Path $root "VERSION"

# 1. Read current version
$current = (Get-Content $versionFile -Raw).Trim()
$parts = $current -split '\.'
$parts[3] = [string]([int]$parts[3] + 1)
$new = $parts -join '.'

# 2. Update VERSION file
[System.IO.File]::WriteAllText($versionFile, $new, [System.Text.UTF8Encoding]::new($false))

# 3. Update __manifest__.py
$manifest = Join-Path $root "cloudrefit_invoicing\__manifest__.py"
$content = Get-Content $manifest -Raw -Encoding UTF8
$escapedOld = [regex]::Escape($current)
$content = $content -replace $escapedOld, $new
[System.IO.File]::WriteAllText($manifest, $content, [System.Text.UTF8Encoding]::new($false))

# 4. Stage files
git add VERSION cloudrefit_invoicing/__manifest__.py

Write-Host "Version bumped: $current -> $new (files staged)"
