<#
.SYNOPSIS
Verify a built release before it is installed or published.

.DESCRIPTION
Checks the environment provenance, the Qt deployment, every packaged PE file,
the frozen smoke report, and the installer's version, architecture gate, and
payload source. Run it after build-installer.ps1.

.EXAMPLE
.\scripts\verify-release.ps1
#>
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. "$PSScriptRoot\build-common.ps1"

function Assert-FenestraSmokeReport {
    param(
        [Parameter(Mandatory)]
        [object] $Report,

        [Parameter(Mandatory)]
        [string] $ReportPath,

        [Parameter(Mandatory)]
        [string] $ExpectedArchitecture,

        [Parameter(Mandatory)]
        [string] $ExpectedVersion,

        [Parameter(Mandatory)]
        [bool] $ExpectedFrozen
    )

    $requiredProperties = @(
        "schemaVersion",
        "application",
        "version",
        "frozen",
        "processArchitecture",
        "pointerBits",
        "checks",
        "passed",
        "failed",
        "exitCode"
    )
    $propertyNames = @($Report.PSObject.Properties.Name)
    foreach ($property in $requiredProperties) {
        if ($propertyNames -notcontains $property) {
            throw "Smoke report '$ReportPath' is missing required property '$property'."
        }
    }

    if ([int] $Report.schemaVersion -ne 1) {
        throw "Smoke report '$ReportPath' has schemaVersion '$($Report.schemaVersion)'; expected 1."
    }
    if ([string] $Report.application -cne "Fenestra") {
        throw "Smoke report '$ReportPath' is for '$($Report.application)'; expected Fenestra."
    }
    if ([string] $Report.version -cne $ExpectedVersion) {
        throw "Smoke report '$ReportPath' has version '$($Report.version)'; expected '$ExpectedVersion'."
    }
    if ($Report.frozen -isnot [bool] -or [bool] $Report.frozen -ne $ExpectedFrozen) {
        throw "Smoke report '$ReportPath' has frozen='$($Report.frozen)'; expected '$ExpectedFrozen'."
    }
    if ([string] $Report.processArchitecture -cne $ExpectedArchitecture) {
        throw "Smoke report '$ReportPath' has processArchitecture '$($Report.processArchitecture)'; expected '$ExpectedArchitecture'."
    }
    if ([int] $Report.pointerBits -ne 64) {
        throw "Smoke report '$ReportPath' has pointerBits '$($Report.pointerBits)'; expected 64."
    }
    if ([int] $Report.exitCode -ne 0 -or [int] $Report.failed -ne 0) {
        throw "Smoke report '$ReportPath' reports exitCode=$($Report.exitCode), failed=$($Report.failed)."
    }

    $checks = @($Report.checks)
    if ($checks.Count -eq 0 -or [int] $Report.passed -ne $checks.Count) {
        throw "Smoke report '$ReportPath' has inconsistent or empty check results."
    }
    foreach ($check in $checks) {
        $checkProperties = @($check.PSObject.Properties.Name)
        if ($checkProperties -notcontains "name" -or $checkProperties -notcontains "passed") {
            throw "Smoke report '$ReportPath' contains a check without name/passed evidence."
        }
    }
    $failedChecks = @(
        $checks | Where-Object { $_.passed -isnot [bool] -or -not [bool] $_.passed }
    )
    if ($failedChecks.Count -gt 0) {
        $failedNames = @($failedChecks | ForEach-Object { $_.name }) -join ", "
        throw "Smoke report '$ReportPath' contains failed checks: $failedNames."
    }

    $webEngineChecks = @(
        $checks | Where-Object { $_.name -ceq "Qt WebEngine minimal page load" }
    )
    if ($webEngineChecks.Count -ne 1) {
        throw "Smoke report '$ReportPath' must contain exactly one Qt WebEngine minimal page load check."
    }
    $webEngine = $webEngineChecks[0]
    $webEngineProperties = @($webEngine.PSObject.Properties.Name)
    if ($webEngineProperties -notcontains "details" -or $null -eq $webEngine.details) {
        throw "Smoke report '$ReportPath' WebEngine check is missing details evidence."
    }
    $detailNames = @($webEngine.details.PSObject.Properties.Name)
    foreach ($property in @("loadFinished", "title", "documentMarker", "documentBytes")) {
        if ($detailNames -notcontains $property) {
            throw "Smoke report '$ReportPath' WebEngine evidence is missing '$property'."
        }
    }
    if ($webEngine.details.loadFinished -isnot [bool] -or
        -not [bool] $webEngine.details.loadFinished -or
        [string] $webEngine.details.title -cne "Fenestra WebEngine Smoke" -or
        [string] $webEngine.details.documentMarker -cne "fenestra-smoke-ready" -or
        [int] $webEngine.details.documentBytes -le 0) {
        throw "Smoke report '$ReportPath' contains invalid Qt WebEngine page-load evidence."
    }
}

function Assert-FenestraFileVersion {
    param(
        [Parameter(Mandatory)]
        [string] $Path,

        [Parameter(Mandatory)]
        [string] $ExpectedVersion
    )

    $versionInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
    $fileVersion = ([string] $versionInfo.FileVersion).Trim()
    $productVersion = ([string] $versionInfo.ProductVersion).Trim()
    if ($fileVersion -cne $ExpectedVersion -or $productVersion -cne $ExpectedVersion) {
        throw "'$Path' embeds file/product version '$fileVersion'/'$productVersion'; expected '$ExpectedVersion'."
    }
    if (([string] $versionInfo.ProductName).Trim() -cne "Fenestra") {
        throw "'$Path' does not embed the expected Fenestra product identity."
    }
}

$context = Get-FenestraBuildContext
Push-Location -LiteralPath $context.Root
try {
    $version = Get-FenestraAppVersion $context.Root
    $manifestPath = Join-Path $context.BundlePath ".release.json"
    $installerPath = Join-Path $context.Root "installer\dist\FenestraSetup-$version-$($context.Architecture).exe"
    $installerOutputManifestPath = Join-Path $context.Root "installer\dist\FenestraSetup-$version-$($context.Architecture)-manifest.txt"
    $installerEvidencePath = Join-Path $context.BuildRoot "installer.json"
    $innoLogPath = Join-Path $context.BuildRoot "inno-setup.log"
    $innoVersionProbeLogPath = Join-Path $context.BuildRoot "inno-setup-version-probe.log"

    Write-Host "[verify-release] Verifying $($context.Architecture) release evidence..."
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Missing verified payload manifest: $manifestPath."
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.architecture -ne $context.Architecture) {
        throw "Manifest architecture '$($manifest.architecture)' does not match requested '$($context.Architecture)'."
    }
    if ($manifest.version -ne $version) {
        throw "Manifest version '$($manifest.version)' does not match APP_VERSION '$version'."
    }

    $package = Get-Content -LiteralPath "frontend\package.json" -Raw | ConvertFrom-Json
    $lockVersions = @(
        & $context.Python -I -c "import json; data=json.load(open('frontend/package-lock.json', encoding='utf-8')); print(data['version']); print(data['packages']['']['version'])"
    )
    if ($LASTEXITCODE -ne 0 -or $lockVersions.Count -ne 2) {
        throw "Could not read root versions from frontend/package-lock.json."
    }
    if ($package.version -ne $version -or
        $lockVersions[0] -ne $version -or
        $lockVersions[1] -ne $version) {
        throw "Version mismatch among APP_VERSION, package.json, and package-lock.json."
    }

    $head = (& git -C $context.Root rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $manifest.sourceCommit -ne $head) {
        throw "The payload was not built from the current HEAD. Manifest: $($manifest.sourceCommit); current: $head."
    }
    $sourceFingerprint = Get-FenestraSourceFingerprint $context.Root
    if ($manifest.sourceFingerprint -ne $sourceFingerprint) {
        throw "Tracked or untracked release inputs changed after the payload was built. Rebuild $($context.Architecture)."
    }
    $payloadFingerprint = Get-FenestraDirectoryFingerprint `
        -Directory $context.BundlePath `
        -ExcludeNames @(".release.json")
    if ($manifest.payloadFingerprint -ne $payloadFingerprint) {
        throw "The frozen payload changed after PE/Qt verification. Rebuild $($context.Architecture)."
    }

    $requiredFiles = @(
        (Join-Path $context.BundlePath "Fenestra.exe"),
        (Join-Path $context.BundlePath "_internal\icon.ico"),
        (Join-Path $context.BundlePath "_internal\LICENSE"),
        (Join-Path $context.BundlePath "_internal\frontend\dist\index.html"),
        $context.EnvironmentReport,
        $context.PeReport,
        $context.QtReport,
        $context.SourceSmokeReport,
        $context.FrozenSmokeReport,
        (Join-Path $context.WorkPath "warn-Fenestra.txt"),
        (Join-Path $context.WorkPath "xref-Fenestra.html"),
        (Join-Path $context.WorkPath "Analysis-00.toc"),
        (Join-Path $context.WorkPath "COLLECT-00.toc"),
        $context.PyInstallerLog,
        $installerPath,
        $installerOutputManifestPath,
        $installerEvidencePath,
        $innoLogPath,
        $innoVersionProbeLogPath,
        (Join-Path $context.BuildRoot "installer-bootstrap-pe.json")
    )
    foreach ($required in $requiredFiles) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Required release evidence is missing: $required."
        }
    }
    Assert-FenestraFileVersion `
        -Path (Join-Path $context.BundlePath "Fenestra.exe") `
        -ExpectedVersion $version
    Assert-FenestraFileVersion -Path $installerPath -ExpectedVersion $version

    $installerEvidence = Get-Content -LiteralPath $installerEvidencePath -Raw | ConvertFrom-Json
    $expectedAllowed = "x64compatible"
    $expectedMinVersion = if ($context.Architecture -eq "x64") { "10.0.17763" } else { "10.0.22000" }
    $expectedCompilerIdentity = "Inno Setup 6 Command-Line Compiler"
    $expectedCompilerVersion = "6.7.3"
    if ([int] $installerEvidence.schemaVersion -lt 4 -or
        $installerEvidence.architecture -ne $context.Architecture -or
        $installerEvidence.architecturesAllowed -ne $expectedAllowed -or
        $installerEvidence.architecturesInstallIn64BitMode -ne $expectedAllowed -or
        $installerEvidence.compilerIdentity -ne $expectedCompilerIdentity -or
        $installerEvidence.compilerVersion -ne $expectedCompilerVersion -or
        [string]::IsNullOrWhiteSpace([string] $installerEvidence.compilerPath) -or
        $installerEvidence.compilerSha256 -notmatch '^[0-9a-f]{64}$' -or
        $installerEvidence.minVersion -ne $expectedMinVersion -or
        -not (Test-FenestraPathEquals $installerEvidence.payloadPath $context.BundlePath) -or
        $installerEvidence.installerPath -ne $installerPath -or
        $installerEvidence.outputManifestPath -ne $installerOutputManifestPath -or
        $installerEvidence.payloadFingerprint -ne $manifest.payloadFingerprint) {
        throw "Installer evidence does not match the architecture-qualified payload."
    }
    $innoVersionProbeTranscript = Get-Content -LiteralPath $innoVersionProbeLogPath -Raw
    if ($innoVersionProbeTranscript -notmatch
        '(?m)^Inno Setup 6 Command-Line Compiler\r?$' -or
        $innoVersionProbeTranscript -notmatch
        '(?m)^Compiler engine version:\s+Inno Setup 6\.7\.3\r?$') {
        throw "The recorded Inno Setup compiler probe does not identify the required 6.7.3 compiler."
    }
    $payloadManifestHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $manifestPath).Hash.ToLowerInvariant()
    if ($installerEvidence.payloadManifestSha256 -ne $payloadManifestHash) {
        throw "The payload manifest changed after the installer was compiled."
    }
    $installerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $installerPath).Hash.ToLowerInvariant()
    if ($installerEvidence.installerSha256 -ne $installerHash) {
        throw "The installer changed after it was associated with the verified payload."
    }
    $installerOutputManifestHash = (
        Get-FileHash -Algorithm SHA256 -LiteralPath $installerOutputManifestPath
    ).Hash.ToLowerInvariant()
    if ($installerEvidence.outputManifestSha256 -ne $installerOutputManifestHash) {
        throw "The compiler-generated installer payload manifest changed after compilation."
    }

    # Inno Setup's OutputManifestFile is compiler-generated evidence of every file
    # embedded in Setup. Require exact path, size, and hash agreement with the
    # architecture-qualified bundle so a mislabeled or stale payload cannot pass.
    $outputManifestRows = @(
        Get-Content -LiteralPath $installerOutputManifestPath |
            ConvertFrom-Csv -Delimiter "`t"
    )
    $payloadFiles = @(Get-ChildItem -LiteralPath $context.BundlePath -Recurse -File)
    if ($outputManifestRows.Count -ne $payloadFiles.Count -or $outputManifestRows.Count -eq 0) {
        throw "The installer output manifest file count does not match the verified payload."
    }
    $bundleRoot = [IO.Path]::GetFullPath($context.BundlePath).TrimEnd([char[]] @('\', '/'))
    $bundlePrefix = $bundleRoot + [IO.Path]::DirectorySeparatorChar
    $manifestFiles = @{}
    foreach ($row in $outputManifestRows) {
        $sourcePath = [IO.Path]::GetFullPath([string] $row.SourceFilename)
        if (-not $sourcePath.StartsWith($bundlePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "The installer embeds a file outside dist/$($context.Architecture)/Fenestra: $sourcePath."
        }
        $relative = $sourcePath.Substring($bundlePrefix.Length).Replace('\', '/')
        if ($manifestFiles.ContainsKey($relative)) {
            throw "The installer output manifest contains a duplicate payload file: $relative."
        }
        $manifestFiles[$relative] = $row
    }
    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($bundlePrefix.Length).Replace('\', '/')
        if (-not $manifestFiles.ContainsKey($relative)) {
            throw "The installer output manifest omits payload file: $relative."
        }
        $row = $manifestFiles[$relative]
        if ([long] $row.OriginalSize -ne $file.Length) {
            throw "The installer output manifest size differs for payload file: $relative."
        }
        $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        if ([string] $row.SHA256Sum -ne $actualHash) {
            throw "The installer output manifest hash differs for payload file: $relative."
        }
    }

    $innoLines = @(Get-Content -LiteralPath $innoLogPath)
    $innoTranscript = $innoLines -join [Environment]::NewLine
    $innoWarnings = @(
        $innoLines | Where-Object {
            $_ -match '(?i)\bwarning(?:s|\(s\))?\b' -and
            $_ -notmatch '(?i)\b0\s+warning(?:s|\(s\))?\b'
        }
    )
    if ($innoWarnings.Count -gt 0) {
        throw "Inno Setup emitted unclassified warning text: $($innoWarnings -join ' | ')."
    }
    $effectiveMarkers = @(
        "FENESTRA_EFFECTIVE_ARCHITECTURE=$($context.Architecture)",
        "FENESTRA_EFFECTIVE_ALLOWED=$expectedAllowed",
        "FENESTRA_EFFECTIVE_64BIT_MODE=$expectedAllowed",
        "FENESTRA_EFFECTIVE_MIN_VERSION=$expectedMinVersion"
    )
    foreach ($marker in $effectiveMarkers) {
        if ($innoTranscript -notmatch [regex]::Escape($marker)) {
            throw "The Inno transcript lacks the expected effective configuration marker: $marker."
        }
    }
    $expectedPayloadMarker = 'FENESTRA_EFFECTIVE_PAYLOAD=.*[\\/]dist[\\/]' +
    [regex]::Escape($context.Architecture) + '[\\/]Fenestra'
    if ($innoTranscript -notmatch $expectedPayloadMarker) {
        throw "The Inno transcript does not prove the architecture-qualified payload path."
    }
    $environmentHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $context.EnvironmentReport).Hash.ToLowerInvariant()
    if ($manifest.environmentReportSha256 -ne $environmentHash) {
        throw "The recorded Python dependency/ABI preflight evidence changed after packaging."
    }

    $sourceSmoke = Get-Content -LiteralPath $context.SourceSmokeReport -Raw | ConvertFrom-Json
    $frozenSmoke = Get-Content -LiteralPath $context.FrozenSmokeReport -Raw | ConvertFrom-Json
    Assert-FenestraSmokeReport `
        -Report $sourceSmoke `
        -ReportPath $context.SourceSmokeReport `
        -ExpectedArchitecture $context.Architecture `
        -ExpectedVersion $version `
        -ExpectedFrozen $false
    Assert-FenestraSmokeReport `
        -Report $frozenSmoke `
        -ReportPath $context.FrozenSmokeReport `
        -ExpectedArchitecture $context.Architecture `
        -ExpectedVersion $version `
        -ExpectedFrozen $true

    $iss = Get-Content -LiteralPath "installer\fenestra.iss" -Raw
    if ($iss -notmatch 'PayloadArchitecturesAllowed\s+"x64compatible"' -or
        $iss -notmatch 'PayloadArchitecturesInstallIn64BitMode\s+"x64compatible"') {
        throw "The x64 installer is not configured for Windows 11 ARM64 x64 emulation."
    }
    if ($iss -notmatch '\.\.\\dist\\" \+ PayloadArchitecture \+ "\\Fenestra') {
        throw "The installer does not derive its source from the architecture-qualified payload directory."
    }

    $savedEnvironment = Start-FenestraSanitizedEnvironment -PythonExecutable $context.Python
    try {
        Invoke-FenestraPreflight `
            -Context $context `
            -Mode full `
            -ReportPath (Join-Path $context.BuildRoot "preflight-verify.json")

        $pythonPrefix = (& $context.Python -I -c "import sys; print(sys.prefix)" | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Python failed while resolving sys.prefix for PyInstaller provenance verification."
        }
        $pythonBasePrefix = (& $context.Python -I -c "import sys; print(sys.base_prefix)" | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Python failed while resolving sys.base_prefix for PyInstaller provenance verification."
        }
        & $context.Python scripts\audit_pyinstaller.py `
            --architecture $context.Architecture `
            --build-dir $context.WorkPath `
            --bundle $context.BundlePath `
            --python-prefix $pythonPrefix `
            --python-base-prefix $pythonBasePrefix `
            --transcript $context.PyInstallerLog `
            --report (Join-Path $context.BuildRoot "pyinstaller-audit.json")
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller warning, provenance, or table-of-contents verification failed."
        }

        & $context.Python scripts\verify_qt_deployment.py `
            --architecture $context.Architecture `
            --bundle $context.BundlePath `
            --report $context.QtReport
        if ($LASTEXITCODE -ne 0) {
            throw "Qt deployment verification failed."
        }

        & $context.Python scripts\pe_arch.py `
            --expected $context.Architecture `
            --recursive `
            --json $context.PeReport `
            $context.BundlePath
        if ($LASTEXITCODE -ne 0) {
            throw "Payload PE verification failed."
        }

        # Rerun the windowed frozen executable. The JSON report makes failures
        # visible even though the PyInstaller executable has no console.
        Remove-Item -LiteralPath $context.FrozenSmokeReport -Force
        $frozenExe = Join-Path $context.BundlePath "Fenestra.exe"
        $frozenSmokeExitCode = Invoke-FenestraBoundedProcess `
            -FilePath $frozenExe `
            -ArgumentList @("--smoke-test", "--smoke-report", "`"$($context.FrozenSmokeReport)`"") `
            -TimeoutSeconds 120 `
            -Hidden
        if ($frozenSmokeExitCode -ne 0 -or
            -not (Test-Path -LiteralPath $context.FrozenSmokeReport -PathType Leaf)) {
            throw "The frozen executable failed its release-verification smoke test."
        }
        $rerunSmoke = Get-Content -LiteralPath $context.FrozenSmokeReport -Raw | ConvertFrom-Json
        Assert-FenestraSmokeReport `
            -Report $rerunSmoke `
            -ReportPath $context.FrozenSmokeReport `
            -ExpectedArchitecture $context.Architecture `
            -ExpectedVersion $version `
            -ExpectedFrozen $true
    }
    finally {
        Restore-FenestraEnvironment $savedEnvironment
    }
}
finally {
    Pop-Location
}

Write-Host "[verify-release] PASSED: $installerPath is tied to a verified $($context.Architecture) payload." -ForegroundColor Green
