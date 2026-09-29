<#
.SYNOPSIS
Build the frontend and the PyInstaller application bundle.

.DESCRIPTION
Builds frontend/dist, runs the source smoke test, runs PyInstaller from
.venv-x64 into dist/x64/Fenestra, audits the Qt deployment and every packaged
PE file, and runs the frozen smoke test. Evidence is written under build/x64.

.PARAMETER PythonExecutable
The official x64 CPython interpreter that created .venv-x64.

.PARAMETER NodeExecutable
The x64 Node.js 24 LTS node.exe for the frontend build.

.PARAMETER SkipFrontendTests
Build the frontend without running its tests.

.EXAMPLE
.\scripts\build-app.ps1 -PythonExecutable C:\Python313\python.exe -NodeExecutable "C:\Program Files\nodejs\node.exe"
#>
param(
    [string] $PythonExecutable,

    [string] $NodeExecutable,

    [switch] $SkipFrontendTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. "$PSScriptRoot\build-common.ps1"

$root = Get-FenestraProjectRoot
$buildLock = Enter-FenestraReleaseBuildLock -Root $root
try {
$targetArchitecture = Resolve-FenestraArchitecture -PythonExecutable $PythonExecutable
$version = Get-FenestraAppVersion $root
$staleDirectories = @(
    (Join-Path $root "build\$targetArchitecture\Fenestra"),
    (Join-Path $root "dist\$targetArchitecture\Fenestra")
)
$staleFiles = @(
    (Join-Path $root "installer\dist\FenestraSetup-$version-$targetArchitecture.exe"),
    (Join-Path $root "installer\dist\FenestraSetup-$version-$targetArchitecture-manifest.txt"),
    (Join-Path $root "build\$targetArchitecture\installer.json"),
    (Join-Path $root "build\$targetArchitecture\installer-bootstrap-pe.json"),
    (Join-Path $root "build\$targetArchitecture\inno-setup.log"),
    (Join-Path $root "build\$targetArchitecture\pe-report.json"),
    (Join-Path $root "build\$targetArchitecture\pyinstaller-audit.json"),
    (Join-Path $root "build\$targetArchitecture\pyinstaller.log"),
    (Join-Path $root "build\$targetArchitecture\qt-deployment.json"),
    (Join-Path $root "build\$targetArchitecture\smoke-frozen.json"),
    (Join-Path $root "build\$targetArchitecture\smoke-source.json")
)
Write-Host "[build-app] Invalidating stale $targetArchitecture release artifacts..."
foreach ($staleDirectory in $staleDirectories) {
    Remove-FenestraWorkspacePath -Root $root -Path $staleDirectory -Recurse
}
foreach ($staleFile in $staleFiles) {
    Remove-FenestraWorkspacePath -Root $root -Path $staleFile
}

$context = Get-FenestraBuildContext `
    -PythonExecutable $PythonExecutable
Push-Location -LiteralPath $context.Root
try {
    if (-not (Test-Path -LiteralPath "Fenestra.spec" -PathType Leaf)) {
        throw "Fenestra.spec was not found in the project root."
    }

    $node = Resolve-FenestraNodeExecutable $NodeExecutable
    $nodeDetails = Get-FenestraNodeDetails $node
    if ($nodeDetails.architecture -ne $context.Architecture) {
        throw "Node.js is $($nodeDetails.architecture), but the application target is $($context.Architecture). Pass a matching -NodeExecutable."
    }

    Write-FenestraAdministratorWarning
    Write-Host "[build-app] Target architecture: $($context.Architecture)."
    Write-Host "[build-app] Python: $($context.Python)."
    Write-Host "[build-app] Work path: $($context.WorkPath)."
    Write-Host "[build-app] Bundle path: $($context.BundlePath)."

    New-Item -ItemType Directory -Force -Path $context.BuildRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $context.DistRoot | Out-Null

    $savedEnvironment = Start-FenestraSanitizedEnvironment `
        -PythonExecutable $context.Python `
        -NodeExecutable $node
    $buildSucceeded = $false
    try {
        & "$PSScriptRoot\build-frontend.ps1" `
            -PythonExecutable $context.Python `
            -NodeExecutable $node `
            -SkipTests:$SkipFrontendTests
        if ($LASTEXITCODE -ne 0) {
            throw "The architecture-safe frontend build failed."
        }

        Invoke-FenestraPreflight -Context $context -Mode full

        Remove-Item -LiteralPath $context.SourceSmokeReport -Force -ErrorAction SilentlyContinue
        Write-Host "[build-app] Running the source smoke test..."
        $sourceSmokeExitCode = Invoke-FenestraBoundedProcess `
            -FilePath $context.Python `
            -ArgumentList @(
            "-I", "-m", "fenestra", "--smoke-test", "--smoke-report",
            "`"$($context.SourceSmokeReport)`""
        ) `
            -TimeoutSeconds 120 `
            -Hidden
        if ($sourceSmokeExitCode -ne 0 -or
            -not (Test-Path -LiteralPath $context.SourceSmokeReport -PathType Leaf)) {
            throw "The source smoke test failed or did not write its JSON report."
        }
        $sourceSmoke = Get-Content -LiteralPath $context.SourceSmokeReport -Raw | ConvertFrom-Json
        if ([int] $sourceSmoke.exitCode -ne 0 -or [bool] $sourceSmoke.frozen) {
            throw "The source smoke report is invalid or reports a failure."
        }

        Remove-Item -LiteralPath $context.PyInstallerLog -Force -ErrorAction SilentlyContinue
        Write-Host "[build-app] Running PyInstaller from the verified $($context.Architecture) interpreter..."
        $pyInstallerArguments = @(
            "-I", "-m", "PyInstaller",
            "--clean",
            "--noconfirm",
            "--log-level", "INFO",
            "--workpath", $context.BuildRoot,
            "--distpath", $context.DistRoot,
            "Fenestra.spec"
        )
        $pyInstallerExitCode = Invoke-FenestraNativeCommand `
            -FilePath $context.Python `
            -ArgumentList $pyInstallerArguments `
            -TranscriptPath $context.PyInstallerLog
        if ($pyInstallerExitCode -ne 0) {
            throw "PyInstaller failed with exit code $pyInstallerExitCode."
        }

        $transcript = Get-Content -LiteralPath $context.PyInstallerLog -Raw
        $fatalPatterns = @(
            "QtLibraryInfo\(PySide6\): failed to obtain Qt library info",
            "failed to obtain Qt library info",
            "DLL load failed while importing QtCore"
        )
        foreach ($pattern in $fatalPatterns) {
            if ($transcript -match $pattern) {
                throw "PyInstaller reported a fatal Qt hook/import failure even though it returned success: $pattern."
            }
        }

        $frozenExe = Join-Path $context.BundlePath "Fenestra.exe"
        if (-not (Test-Path -LiteralPath $frozenExe -PathType Leaf)) {
            throw "PyInstaller returned success, but $frozenExe is missing."
        }

        $pythonPrefix = (& $context.Python -I -c "import sys; print(sys.prefix)" | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Python failed while resolving sys.prefix for PyInstaller provenance verification."
        }
        $pythonBasePrefix = (& $context.Python -I -c "import sys; print(sys.base_prefix)" | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Python failed while resolving sys.base_prefix for PyInstaller provenance verification."
        }
        $auditReport = Join-Path $context.BuildRoot "pyinstaller-audit.json"
        & $context.Python scripts\audit_pyinstaller.py `
            --architecture $context.Architecture `
            --build-dir $context.WorkPath `
            --bundle $context.BundlePath `
            --python-prefix $pythonPrefix `
            --python-base-prefix $pythonBasePrefix `
            --transcript $context.PyInstallerLog `
            --report $auditReport
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller provenance, warning, or analysis-table verification failed."
        }

        & $context.Python scripts\verify_qt_deployment.py `
            --architecture $context.Architecture `
            --bundle $context.BundlePath `
            --report $context.QtReport
        if ($LASTEXITCODE -ne 0) {
            throw "The frozen Qt platform/WebEngine deployment is incomplete or has the wrong architecture."
        }

        & $context.Python scripts\pe_arch.py `
            --expected $context.Architecture `
            --recursive `
            --json $context.PeReport `
            $context.BundlePath
        if ($LASTEXITCODE -ne 0) {
            throw "The frozen payload contains a missing, unknown, or cross-architecture PE binary."
        }

        Remove-Item -LiteralPath $context.FrozenSmokeReport -Force -ErrorAction SilentlyContinue
        Write-Host "[build-app] Running the frozen smoke test..."
        $frozenSmokeExitCode = Invoke-FenestraBoundedProcess `
            -FilePath $frozenExe `
            -ArgumentList @("--smoke-test", "--smoke-report", "`"$($context.FrozenSmokeReport)`"") `
            -TimeoutSeconds 120 `
            -Hidden
        if ($frozenSmokeExitCode -ne 0) {
            throw "The frozen smoke test failed with exit code $frozenSmokeExitCode."
        }
        if (-not (Test-Path -LiteralPath $context.FrozenSmokeReport -PathType Leaf)) {
            throw "The windowed frozen executable did not write its smoke report."
        }
        $frozenSmoke = Get-Content -LiteralPath $context.FrozenSmokeReport -Raw | ConvertFrom-Json
        if ([int] $frozenSmoke.exitCode -ne 0 -or -not [bool] $frozenSmoke.frozen) {
            throw "The frozen smoke report is invalid or reports a failure."
        }

        $head = (& git -C $context.Root rev-parse HEAD | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Running git rev-parse HEAD failed while creating release evidence."
        }
        $dirty = [bool] ((& git -C $context.Root status --porcelain=v1 --untracked-files=all | Out-String).Trim())
        if ($LASTEXITCODE -ne 0) {
            throw "Running git status failed while creating release evidence."
        }
        $payloadFingerprint = Get-FenestraDirectoryFingerprint `
            -Directory $context.BundlePath `
            -ExcludeNames @(".release.json")
        $manifest = [ordered]@{
            schemaVersion             = 2
            application               = "Fenestra"
            version                   = $version
            architecture              = $context.Architecture
            sourceCommit              = $head
            sourceDirty               = $dirty
            sourceFingerprint         = Get-FenestraSourceFingerprint $context.Root
            payloadFingerprint        = $payloadFingerprint
            pythonExecutable          = $context.Python
            pythonProcessArchitecture = $context.PythonDetails.architecture
            pythonVersion             = $context.PythonDetails.version
            nodeExecutable            = $nodeDetails.executable
            nodeProcessArchitecture   = $nodeDetails.architecture
            nodeVersion               = $nodeDetails.version
            packageLockSha256         = (Get-FileHash -Algorithm SHA256 -LiteralPath "frontend\package-lock.json").Hash.ToLowerInvariant()
            specSha256                = (Get-FileHash -Algorithm SHA256 -LiteralPath "Fenestra.spec").Hash.ToLowerInvariant()
            environmentReportSha256   = (Get-FileHash -Algorithm SHA256 -LiteralPath $context.EnvironmentReport).Hash.ToLowerInvariant()
            peReport                  = $context.PeReport
            qtReport                  = $context.QtReport
            sourceSmokeReport         = $context.SourceSmokeReport
            frozenSmokeReport         = $context.FrozenSmokeReport
            builtAtUtc                = [DateTime]::UtcNow.ToString("o")
        }
        $manifest | ConvertTo-Json -Depth 5 | Set-Content `
            -LiteralPath (Join-Path $context.BundlePath ".release.json") `
            -Encoding utf8
        $buildSucceeded = $true
    }
    finally {
        if (-not $buildSucceeded) {
            try {
                Remove-FenestraWorkspacePath `
                    -Root $context.Root `
                    -Path $context.BundlePath `
                    -Recurse
            }
            catch {
                Write-Warning "Failed to remove an incomplete frozen bundle: $($_.Exception.Message)."
            }
        }
        Restore-FenestraEnvironment $savedEnvironment
    }
}
finally {
    Pop-Location
}

Write-Host "[build-app] OK: $($context.BundlePath) is verified as $($context.Architecture)."
}
finally {
    Exit-FenestraReleaseBuildLock -Token $buildLock
}
