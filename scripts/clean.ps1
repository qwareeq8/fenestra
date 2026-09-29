<#
.SYNOPSIS
Remove build outputs and optional development environments.

.DESCRIPTION
Deletes build/, dist/, installer/dist/, frontend/dist/, test and lint caches,
PyInstaller spec backups, and source __pycache__ directories. Every deletion is
confined to the repository by Remove-FenestraWorkspacePath.

.PARAMETER RemoveEnvironments
Also delete the .venv-x64 virtual environment.

.PARAMETER RemoveFrontendDependencies
Also delete frontend/node_modules.

.EXAMPLE
.\scripts\clean.ps1 -RemoveEnvironments
#>
param(
    [switch] $RemoveEnvironments,

    [switch] $RemoveFrontendDependencies
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. "$PSScriptRoot\build-common.ps1"

$root = Get-FenestraProjectRoot
Push-Location -LiteralPath $root
try {

    function Remove-WorkspacePath {
        param(
            [Parameter(Mandatory)]
            [string] $Path
        )

        Remove-FenestraWorkspacePath -Root $root -Path $Path -Recurse
    }

    foreach ($outputPath in @("build", "dist", "installer\dist", "frontend\dist")) {
        Remove-WorkspacePath $outputPath
    }
    foreach ($cachePath in @(".pytest_cache", ".ruff_cache", ".mypy_cache")) {
        try {
            Remove-WorkspacePath $cachePath
        }
        catch {
            # Test and lint caches are never release inputs. A cache owned by a
            # different Windows token must not block removal of release outputs.
            Write-Warning "Could not remove optional cache '$cachePath': $($_.Exception.Message)."
        }
    }
    if ($RemoveEnvironments) {
        Remove-WorkspacePath ".venv-x64"
    }
    if ($RemoveFrontendDependencies) {
        Remove-WorkspacePath "frontend\node_modules"
    }

    Get-ChildItem -LiteralPath $root -Filter "*.spec.bak" -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            Remove-WorkspacePath $_.FullName.Substring($root.Length).TrimStart([char[]] @('\', '/'))
        }

    foreach ($sourceDirectory in @("fenestra", "tests", "scripts")) {
        Get-ChildItem -LiteralPath (Join-Path $root $sourceDirectory) `
            -Recurse `
            -Filter "__pycache__" `
            -Directory `
            -ErrorAction SilentlyContinue | ForEach-Object {
            Remove-WorkspacePath $_.FullName.Substring($root.Length).TrimStart([char[]] @('\', '/'))
        }
    }

    $environmentStatus = if ($RemoveEnvironments) { "was" } else { "was not" }
    Write-Host "[clean] OK: Removed build outputs. The environment $environmentStatus removed."
}
finally {
    Pop-Location
}
