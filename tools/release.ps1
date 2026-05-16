# Release wn-rack to PyPI.
#
# Usage (from any directory):
#     powershell -ExecutionPolicy Bypass -File tools\release.ps1
# Or from inside a PowerShell session:
#     .\tools\release.ps1
#
# Requires:
#   - uv installed and on PATH
#   - .env in repo root containing TWINE_USERNAME=__token__ and TWINE_PASSWORD=pypi-...
#   - version in pyproject.toml bumped past whatever is already on PyPI

$ErrorActionPreference = 'Stop'

# Move to repo root (parent of this script's dir).
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot
Write-Host "Repo root: $repoRoot"

# Load .env into the process environment.
$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path $envFile)) {
    throw ".env not found at $envFile. Copy .env.example to .env and fill in your PyPI token."
}
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq '' -or $line.StartsWith('#')) { return }
    if ($line -match '^\s*([^=]+?)\s*=\s*(.*)$') {
        $name  = $matches[1]
        $value = $matches[2]
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

if (-not $env:TWINE_USERNAME -or -not $env:TWINE_PASSWORD) {
    throw "TWINE_USERNAME / TWINE_PASSWORD not set after loading .env."
}
if ($env:TWINE_USERNAME -ne '__token__') {
    Write-Warning "TWINE_USERNAME is '$($env:TWINE_USERNAME)'. For PyPI API tokens it must be the literal string '__token__'."
}

# Clean previous build artifacts so we don't accidentally re-upload an old version.
foreach ($dir in @('dist', 'build')) {
    if (Test-Path $dir) {
        Write-Host "Removing $dir/"
        Remove-Item -Recurse -Force $dir
    }
}
Get-ChildItem -Path 'src' -Filter '*.egg-info' -Directory -ErrorAction SilentlyContinue |
    ForEach-Object {
        Write-Host "Removing $($_.FullName)"
        Remove-Item -Recurse -Force $_.FullName
    }

# Build sdist + wheel.
Write-Host "`n--- uv build ---"
uv build
if ($LASTEXITCODE -ne 0) { throw "uv build failed." }

# Upload to PyPI.
Write-Host "`n--- twine upload ---"
$artifacts = Get-ChildItem -Path dist\* -Include *.whl, *.tar.gz -File |
    ForEach-Object { $_.FullName }
if (-not $artifacts) { throw "No build artifacts found in dist/." }
uv tool run twine upload @artifacts
if ($LASTEXITCODE -ne 0) { throw "twine upload failed." }

Write-Host "`nRelease complete."
