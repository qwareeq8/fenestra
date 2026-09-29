<#
.SYNOPSIS
Install, test, and build the React frontend with a verified x64 Node.js.

.DESCRIPTION
Runs a deterministic npm ci and npm audit, then the format, lint, and test
checks and the Vite build with the application version from
fenestra/app/config.py. The Node.js process and esbuild's native binary must
both be x64.

.PARAMETER NodeExecutable
The x64 Node.js 24 LTS node.exe.

.PARAMETER PythonExecutable
The Python interpreter used to read the application version.

.PARAMETER InstallOnly
Install the locked dependencies without testing or building.

.PARAMETER SkipTests
Build without running the frontend tests.

.EXAMPLE
.\scripts\build-frontend.ps1 -NodeExecutable "C:\Program Files\nodejs\node.exe"
#>
param(
    [string] $NodeExecutable,

    [string] $PythonExecutable,

    [switch] $InstallOnly,

    [switch] $SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. "$PSScriptRoot\build-common.ps1"

$root = Get-FenestraProjectRoot
Push-Location -LiteralPath $root
try {
    $node = Resolve-FenestraNodeExecutable $NodeExecutable
    $nodeDetails = Get-FenestraNodeDetails $node
    # The esbuild package ships a native executable, so Node.js must match the x64 payload.
    $targetArchitecture = "x64"
    if ($nodeDetails.architecture -ne $targetArchitecture) {
        throw "Node.js is $($nodeDetails.architecture), but the frontend build target is $targetArchitecture. Pass a matching -NodeExecutable."
    }

    $python = Resolve-FenestraPythonExecutable $PythonExecutable
    $npm = Resolve-FenestraNpmExecutable $node
    $version = Get-FenestraAppVersion $root
    $originalViteVersion = Get-Item Env:VITE_APP_VERSION -ErrorAction SilentlyContinue
    $env:VITE_APP_VERSION = $version
    $frontend = Join-Path $root "frontend"
    $lockPath = Join-Path $frontend ".fenestra-npm-ci.lock"
    $lockHandle = $null

    $savedEnvironment = Start-FenestraSanitizedEnvironment `
        -PythonExecutable $python `
        -NodeExecutable $node
    try {
        try {
            $lockHandle = [IO.File]::Open(
                $lockPath,
                [IO.FileMode]::OpenOrCreate,
                [IO.FileAccess]::ReadWrite,
                [IO.FileShare]::None
            )
        }
        catch {
            throw "Another frontend dependency installation is using frontend/node_modules. Wait for it to finish before switching Node architectures."
        }

        Push-Location -LiteralPath $frontend
        try {
            $npmVersion = (& $npm --version 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0) {
                throw "npm --version failed."
            }
            $nodeVersion = [version] $nodeDetails.version.TrimStart("v")
            $parsedNpmVersion = [version] $npmVersion
            if ($nodeVersion.Major -ne 24 -or $parsedNpmVersion.Major -ne 11) {
                throw "Release frontend builds require Node.js 24 and npm 11. Found Node $($nodeDetails.version) and npm $npmVersion."
            }
            Write-Host "[build-frontend] Node $($nodeDetails.version) $($nodeDetails.architecture); npm $npmVersion."
            Write-Host "[build-frontend] Target architecture: $targetArchitecture."

            $ignoreScripts = (& $npm config get ignore-scripts 2>&1 | Out-String).Trim().ToLowerInvariant()
            if ($LASTEXITCODE -ne 0) {
                throw "Running npm config get ignore-scripts failed."
            }
            if ($ignoreScripts -eq "true") {
                throw "The npm install scripts are disabled. The esbuild package requires its verified install script, so run this build with npm ignore-scripts=false instead of weakening security globally."
            }

            # A deterministic reinstall prevents x64 and ARM64 native Node tools from
            # being silently reused across release targets.
            Write-Host "[build-frontend] Running deterministic npm ci..."
            & $npm ci
            if ($LASTEXITCODE -ne 0) {
                throw "Running npm ci failed."
            }

            $esbuildPath = (& $node -e "const path=require('path'); const p=require.resolve('@esbuild/win32-'+process.arch+'/package.json'); console.log(path.join(path.dirname(p),'esbuild.exe'));" 2>&1 | Out-String).Trim()
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $esbuildPath -PathType Leaf)) {
                throw "The native esbuild package for Node $($nodeDetails.architecture) was not installed correctly: $esbuildPath."
            }
            $nativeNodeTools = @(
                Get-ChildItem -LiteralPath "node_modules" -Recurse -File |
                    Where-Object { $_.Extension.ToLowerInvariant() -in @(".exe", ".dll", ".node", ".ocx") } |
                    Select-Object -ExpandProperty FullName
            )
            if ($nativeNodeTools.Count -eq 0) {
                throw "No native Node build tools were found after npm ci."
            }
            $frontendPeReport = Join-Path $root "build\$targetArchitecture\frontend-native-pe.json"
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $frontendPeReport) | Out-Null
            & $python (Join-Path $root "scripts\pe_arch.py") `
                --expected $targetArchitecture `
                --json $frontendPeReport `
                @nativeNodeTools
            if ($LASTEXITCODE -ne 0) {
                throw "A native frontend build tool does not match the selected Node architecture."
            }
            & $esbuildPath --version
            if ($LASTEXITCODE -ne 0) {
                throw "The installed esbuild executable could not start."
            }

            Write-Host "[build-frontend] Auditing production and build dependencies..."
            & $npm audit --omit=dev --audit-level=low
            if ($LASTEXITCODE -ne 0) {
                throw "The npm audit found a vulnerability in production dependencies."
            }
            & $npm audit --audit-level=low
            if ($LASTEXITCODE -ne 0) {
                throw "The npm audit found an unresolved frontend build or test dependency vulnerability. Review compatible updates, and do not use npm audit fix --force."
            }

            $marker = [ordered]@{
                architecture      = $targetArchitecture
                nodeExecutable    = $nodeDetails.executable
                nodeVersion       = $nodeDetails.version
                npmVersion        = $npmVersion
                packageLockSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath "package-lock.json").Hash.ToLowerInvariant()
                installedAtUtc    = [DateTime]::UtcNow.ToString("o")
            }
            $marker | ConvertTo-Json | Set-Content `
                -LiteralPath "node_modules\.fenestra-build-environment.json" `
                -Encoding utf8

            if (-not $InstallOnly) {
                Write-Host "[build-frontend] Checking frontend formatting and lint..."
                & $npm run format:check
                if ($LASTEXITCODE -ne 0) {
                    throw "Frontend formatting check failed."
                }
                & $npm run lint
                if ($LASTEXITCODE -ne 0) {
                    throw "Frontend lint failed."
                }

                if (-not $SkipTests) {
                    Write-Host "[build-frontend] Running frontend tests..."
                    & $npm test
                    if ($LASTEXITCODE -ne 0) {
                        throw "Frontend tests failed."
                    }
                }

                Write-Host "[build-frontend] Building frontend..."
                & $npm run build
                if ($LASTEXITCODE -ne 0) {
                    throw "Frontend build failed."
                }
            }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        if ($lockHandle) {
            $lockHandle.Dispose()
            Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
        }
        Restore-FenestraEnvironment $savedEnvironment
        if ($originalViteVersion) {
            $env:VITE_APP_VERSION = $originalViteVersion.Value
        }
        else {
            Remove-Item Env:VITE_APP_VERSION -ErrorAction SilentlyContinue
        }
    }

    if (-not $InstallOnly) {
        $index = Join-Path $frontend "dist\index.html"
        if (-not (Test-Path -LiteralPath $index -PathType Leaf)) {
            throw "Frontend build failed: frontend/dist/index.html is missing."
        }
        Write-Host "[build-frontend] OK: frontend/dist/index.html was built from a verified $targetArchitecture Node toolchain."
    }
    else {
        Write-Host "[build-frontend] OK: frontend dependencies are verified for $targetArchitecture."
    }
}
finally {
    Pop-Location
}
