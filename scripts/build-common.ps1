<#
.SYNOPSIS
Shared helpers for the Fenestra build scripts.

.DESCRIPTION
Dot-source this file; it defines functions and runs nothing on its own. The
helpers resolve and verify the x64 Python and Node.js toolchains, manage the
.venv-x64 provenance marker, sanitize the build environment, serialize release
builds, and confine deletions to the repository.
#>
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-FenestraProjectRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function ConvertTo-FenestraArchitecture {
    param(
        [Parameter(Mandatory)]
        [string] $Value
    )

    switch -Regex ($Value.Trim().ToLowerInvariant()) {
        "^(amd64|x86_64|x64|win-amd64)$" { return "x64" }
        "^(arm64|aarch64|win-arm64)$" { return "arm64" }
        default { throw "Unsupported 64-bit process architecture '$Value'." }
    }
}

function Resolve-FenestraPythonExecutable {
    param(
        [string] $PythonExecutable
    )

    if ($PythonExecutable) {
        $explicit = Get-Command $PythonExecutable -ErrorAction SilentlyContinue
        if (-not $explicit) {
            throw "The requested Python executable was not found: $PythonExecutable."
        }
        return $explicit.Source
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $launcher) {
        throw "No Python executable was selected. Pass -PythonExecutable with an official CPython 3.13 python.exe path."
    }

    $selected = (& $launcher.Source -3.13 -I -c "import sys; print(sys.executable)" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $selected) {
        throw "The Python Launcher could not select official CPython 3.13. Pass -PythonExecutable explicitly."
    }
    if (-not (Test-Path -LiteralPath $selected -PathType Leaf)) {
        throw "The Python Launcher returned a missing executable: $selected."
    }
    return (Resolve-Path -LiteralPath $selected).Path
}

function Get-FenestraPythonProcessArchitecture {
    param(
        [Parameter(Mandatory)]
        [string] $PythonExecutable
    )

    # Use only Python single-quoted literals here. Windows PowerShell 5.1 can
    # strip embedded double quotes from a native program's multiline -c value.
    $code = "import json,platform,struct,sys,sysconfig; print(json.dumps({'executable':sys.executable,'machine':platform.machine(),'pointer_bits':struct.calcsize('P')*8,'platform':sysconfig.get_platform(),'version':sys.version,'base_prefix':sys.base_prefix}))"
    $raw = (& $PythonExecutable -I -c $code 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect Python '$PythonExecutable': $raw."
    }
    $details = $raw | ConvertFrom-Json
    if ([int] $details.pointer_bits -ne 64) {
        throw "Fenestra release builds require a 64-bit Python process; '$PythonExecutable' reports $($details.pointer_bits)-bit."
    }

    # Windows x64 emulation on ARM64 does not use WOW64. In that environment,
    # platform.machine() may report ARM64 even though Python and its extension
    # ABI are x64, so the executable PE header is the authoritative build input.
    $peRaw = (& $PythonExecutable -I "$PSScriptRoot\pe_arch.py" $PythonExecutable 2>&1 |
            Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect the Python executable PE architecture: $peRaw."
    }
    $peReport = $peRaw | ConvertFrom-Json
    $peFiles = @($peReport.files)
    if ($peFiles.Count -ne 1 -or $peFiles[0].architecture -notin @("x64", "arm64")) {
        throw "The selected Python executable does not have a supported x64 or ARM64 PE architecture."
    }
    $details | Add-Member -NotePropertyName peMachine -NotePropertyValue $peFiles[0].machine_hex
    $details | Add-Member -NotePropertyName architecture -NotePropertyValue $peFiles[0].architecture
    return $details
}

function Resolve-FenestraArchitecture {
    param(
        [string] $PythonExecutable
    )

    # Qt WebEngine is published only for x64 Windows, so every release payload
    # is x64. It also runs on Windows 11 ARM64 through x64 emulation.
    if ($PythonExecutable) {
        $selectedArchitecture = (Get-FenestraPythonProcessArchitecture $PythonExecutable).architecture
        if ($selectedArchitecture -ne "x64") {
            throw "The selected Python process is $selectedArchitecture. Fenestra builds only x64 payloads; select an official x64 CPython interpreter."
        }
    }
    return "x64"
}

function Get-FenestraBuildContext {
    param(
        [string] $PythonExecutable,

        [switch] $Bootstrap,

        [switch] $Provisioning
    )

    $root = Get-FenestraProjectRoot
    $basePython = $null
    if ($Bootstrap) {
        $basePython = Resolve-FenestraPythonExecutable $PythonExecutable
        $resolvedArchitecture = Resolve-FenestraArchitecture $basePython
        $activePython = $basePython
    }
    else {
        if ($PythonExecutable) {
            $basePython = Resolve-FenestraPythonExecutable $PythonExecutable
            $resolvedArchitecture = Resolve-FenestraArchitecture $basePython
        }
        else {
            $resolvedArchitecture = Resolve-FenestraArchitecture
        }
        $venvCandidate = Join-Path $root ".venv-$resolvedArchitecture\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $venvCandidate -PathType Leaf)) {
            $bootstrapHint = ".\scripts\bootstrap.ps1"
            if ($basePython) {
                $bootstrapHint += " -PythonExecutable `"$basePython`""
            }
            else {
                $bootstrapHint += " -PythonExecutable C:\Path\To\OfficialPython\python.exe"
            }
            throw "Missing architecture-qualified environment '.venv-$resolvedArchitecture'. Run: $bootstrapHint."
        }
        $activePython = (Resolve-Path -LiteralPath $venvCandidate).Path
    }

    $pythonDetails = Get-FenestraPythonProcessArchitecture $activePython
    if ($pythonDetails.architecture -ne $resolvedArchitecture) {
        $venvName = ".venv-$resolvedArchitecture"
        throw "$venvName contains $($pythonDetails.architecture) Python, not $resolvedArchitecture. Remove it with 'Remove-Item -Recurse -Force $venvName', then rerun bootstrap with the correct official CPython interpreter."
    }

    $environmentProvenance = $null
    if (-not $Bootstrap -and -not $Provisioning) {
        $environmentProvenance = Assert-FenestraEnvironmentProvenance `
            -Root $root `
            -Architecture $resolvedArchitecture `
            -VenvPath (Join-Path $root ".venv-$resolvedArchitecture") `
            -VenvPython $activePython `
            -SelectedPython $basePython
        $basePython = $environmentProvenance.basePythonExecutable
    }

    return [pscustomobject]@{
        Root                  = $root
        Architecture          = $resolvedArchitecture
        VenvPath              = Join-Path $root ".venv-$resolvedArchitecture"
        Python                = $activePython
        BasePython            = $basePython
        PythonDetails         = $pythonDetails
        EnvironmentProvenance = $environmentProvenance
        EnvironmentMarker     = Join-Path $root ".venv-$resolvedArchitecture\.fenestra-build-environment.json"
        BuildRoot             = Join-Path $root "build\$resolvedArchitecture"
        WorkPath              = Join-Path $root "build\$resolvedArchitecture\Fenestra"
        DistRoot              = Join-Path $root "dist\$resolvedArchitecture"
        BundlePath            = Join-Path $root "dist\$resolvedArchitecture\Fenestra"
        EnvironmentReport     = Join-Path $root "build\$resolvedArchitecture\preflight.json"
        PeReport              = Join-Path $root "build\$resolvedArchitecture\pe-report.json"
        QtReport              = Join-Path $root "build\$resolvedArchitecture\qt-deployment.json"
        SourceSmokeReport     = Join-Path $root "build\$resolvedArchitecture\smoke-source.json"
        FrozenSmokeReport     = Join-Path $root "build\$resolvedArchitecture\smoke-frozen.json"
        PyInstallerLog        = Join-Path $root "build\$resolvedArchitecture\pyinstaller.log"
    }
}

function Resolve-FenestraNodeExecutable {
    param(
        [string] $NodeExecutable
    )

    $node = if ($NodeExecutable) {
        Get-Command $NodeExecutable -ErrorAction SilentlyContinue
    }
    else {
        Get-Command node.exe -ErrorAction SilentlyContinue
    }
    if (-not $node) {
        throw "Node.js was not found. Install Node.js 24 LTS or pass -NodeExecutable."
    }
    return $node.Source
}

function Get-FenestraNodeDetails {
    param(
        [Parameter(Mandatory)]
        [string] $NodeExecutable
    )

    $raw = (& $NodeExecutable -p "JSON.stringify({executable:process.execPath,version:process.version,architecture:process.arch,platform:process.platform})" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to inspect Node.js '$NodeExecutable': $raw."
    }
    $details = $raw | ConvertFrom-Json
    $major = [int] ($details.version -replace '^v(\d+).*$', '$1')
    if ($major -ne 24) {
        throw "Node.js 24 LTS is required for release builds; found $($details.version) at $NodeExecutable."
    }
    $details.architecture = ConvertTo-FenestraArchitecture $details.architecture
    return $details
}

function Resolve-FenestraNpmExecutable {
    param(
        [Parameter(Mandatory)]
        [string] $NodeExecutable
    )

    $adjacent = Join-Path (Split-Path -Parent $NodeExecutable) "npm.cmd"
    if (Test-Path -LiteralPath $adjacent -PathType Leaf) {
        return $adjacent
    }
    throw "The npm.cmd launcher was not found next to the selected Node executable: $NodeExecutable."
}

function Start-FenestraSanitizedEnvironment {
    param(
        [Parameter(Mandatory)]
        [string] $PythonExecutable,

        [string] $NodeExecutable
    )

    $names = @(
        "PATH", "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV",
        "CONDA_PREFIX", "CONDA_DEFAULT_ENV", "CONDA_PROMPT_MODIFIER",
        "CONDA_SHLVL", "CONDA_EXE", "_CONDA_EXE", "CONDA_PYTHON_EXE",
        "_CE_CONDA", "_CE_M",
        "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QML_IMPORT_PATH", "QML2_IMPORT_PATH",
        "QTWEBENGINEPROCESS_PATH", "QTWEBENGINE_RESOURCES_PATH",
        "QTWEBENGINE_LOCALES_PATH", "QTWEBENGINE_DICTIONARIES_PATH",
        "QTWEBENGINE_CHROMIUM_FLAGS", "QTWEBENGINE_DISABLE_SANDBOX",
        "QT_QPA_PLATFORM", "QT_QPA_PLATFORMTHEME"
    )
    $saved = @{}
    foreach ($name in $names) {
        $item = Get-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        $saved[$name] = if ($item) { $item.Value } else { $null }
    }

    $redirectVariables = $names | Where-Object {
        $_ -like "QT*" -or $_ -like "QML*"
    }
    $clearedRedirectVariables = @(
        $redirectVariables | Where-Object { $null -ne $saved[$_] }
    )
    if ($clearedRedirectVariables.Count -gt 0) {
        Write-Host (
            "[environment] Clearing Qt/WebEngine redirect variables for build children: " +
            ($clearedRedirectVariables -join ", ")
        )
    }
    else {
        Write-Host "[environment] No Qt/WebEngine redirect variables were set."
    }

    $pythonDetails = Get-FenestraPythonProcessArchitecture $PythonExecutable
    $pathEntries = [System.Collections.Generic.List[string]]::new()
    $pathEntries.Add((Split-Path -Parent $PythonExecutable))
    $pathEntries.Add($pythonDetails.base_prefix)
    if ($NodeExecutable) {
        $pathEntries.Add((Split-Path -Parent $NodeExecutable))
    }
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $pathEntries.Add((Split-Path -Parent $git.Source))
    }
    $pathEntries.Add((Join-Path $env:SystemRoot "System32"))
    $pathEntries.Add($env:SystemRoot)
    $pathEntries.Add((Join-Path $env:SystemRoot "System32\Wbem"))
    $env:PATH = (($pathEntries | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique) -join ";")

    foreach ($name in $names | Where-Object { $_ -notin @("PATH", "VIRTUAL_ENV") }) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
    $venvRoot = Split-Path -Parent (Split-Path -Parent $PythonExecutable)
    if ((Split-Path -Leaf $venvRoot) -like ".venv-*") {
        $env:VIRTUAL_ENV = $venvRoot
    }
    else {
        Remove-Item Env:VIRTUAL_ENV -ErrorAction SilentlyContinue
    }

    return $saved
}

function Restore-FenestraEnvironment {
    param(
        [Parameter(Mandatory)]
        [hashtable] $SavedEnvironment
    )

    foreach ($entry in $SavedEnvironment.GetEnumerator()) {
        if ($null -eq $entry.Value) {
            Remove-Item -LiteralPath "Env:$($entry.Key)" -ErrorAction SilentlyContinue
        }
        else {
            Set-Item -LiteralPath "Env:$($entry.Key)" -Value $entry.Value
        }
    }
}

function Get-FenestraStringSha256 {
    param(
        [Parameter(Mandatory)]
        [AllowEmptyString()]
        [string] $Value
    )

    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
        $digest = $sha256.ComputeHash($bytes)
    }
    finally {
        $sha256.Dispose()
    }
    return ([BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
}

function Test-FenestraPathEquals {
    param(
        [Parameter(Mandatory)]
        [string] $Left,

        [Parameter(Mandatory)]
        [string] $Right
    )

    $leftPath = [IO.Path]::GetFullPath($Left).TrimEnd([char[]] @('\', '/'))
    $rightPath = [IO.Path]::GetFullPath($Right).TrimEnd([char[]] @('\', '/'))
    return $leftPath.Equals($rightPath, [StringComparison]::OrdinalIgnoreCase)
}

function Get-FenestraOfficialPythonIdentity {
    param(
        [Parameter(Mandatory)]
        [string] $PythonExecutable
    )

    $resolved = Resolve-FenestraPythonExecutable $PythonExecutable
    $signature = Get-AuthenticodeSignature -LiteralPath $resolved
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        throw "Release environments require an Authenticode-valid Python Software Foundation interpreter. '$resolved' is not a verified official CPython executable."
    }

    $details = Get-FenestraPythonProcessArchitecture $resolved
    return [pscustomobject]@{
        executable       = $resolved
        sha256           = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolved).Hash.ToLowerInvariant()
        version          = $details.version
        architecture     = $details.architecture
        basePrefix       = [IO.Path]::GetFullPath([string] $details.base_prefix)
        signerSubject    = $signature.SignerCertificate.Subject
        signerThumbprint = $signature.SignerCertificate.Thumbprint
    }
}

function Get-FenestraPipFreezeSnapshot {
    param(
        [Parameter(Mandatory)]
        [string] $PythonExecutable
    )

    $packages = @(& $PythonExecutable -I -m pip freeze --isolated --disable-pip-version-check --all)
    if ($LASTEXITCODE -ne 0) {
        throw "Running pip freeze failed while validating the architecture-qualified environment."
    }
    $normalized = @(
        $packages |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ } |
            ForEach-Object {
                # Fenestra itself is installed editable. pip freeze renders that
                # entry with the current commit hash, or a bare path when git
                # is unavailable, so the raw line would invalidate the marker
                # on every commit. The provenance check protects the
                # third-party closure, so normalize the project's own entry.
                if ($_ -match '^-e .*fenestra') { "-e fenestra" } else { $_ }
            } |
            Sort-Object
    )
    $text = if ($normalized.Count -gt 0) {
        ($normalized -join "`n") + "`n"
    }
    else {
        ""
    }
    return [pscustomobject]@{
        packages = $normalized
        sha256   = Get-FenestraStringSha256 $text
    }
}

function Get-FenestraEnvironmentRemediationCommand {
    param(
        [Parameter(Mandatory)]
        [string] $Architecture,

        [string] $BasePython
    )

    $selectedBase = if ($BasePython) {
        " -PythonExecutable `"$BasePython`""
    }
    else {
        " -PythonExecutable C:\Path\To\OfficialPython\python.exe"
    }
    return "Remove-Item -LiteralPath `".venv-$Architecture`" -Recurse -Force; .\scripts\bootstrap.ps1$selectedBase"
}

function Assert-FenestraEnvironmentInterpreter {
    # The bootstrap reinstalls packages into an existing environment, so it
    # checks only that the environment runs the selected official interpreter.
    param(
        [Parameter(Mandatory)]
        [ValidateSet("x64")]
        [string] $Architecture,

        [Parameter(Mandatory)]
        [string] $VenvPython,

        [Parameter(Mandatory)]
        [pscustomobject] $BaseIdentity
    )

    $venvDetails = Get-FenestraPythonProcessArchitecture $VenvPython
    $mismatches = [System.Collections.Generic.List[string]]::new()
    if ($venvDetails.architecture -ne $Architecture) {
        $mismatches.Add("environment Python architecture $($venvDetails.architecture)")
    }
    if ([string] $venvDetails.version -ne $BaseIdentity.version) {
        $mismatches.Add("Python version")
    }
    if (-not (Test-FenestraPathEquals ([string] $venvDetails.base_prefix) $BaseIdentity.basePrefix)) {
        $mismatches.Add("base interpreter $($venvDetails.base_prefix)")
    }
    if ($mismatches.Count -gt 0) {
        $remediation = Get-FenestraEnvironmentRemediationCommand `
            -Architecture $Architecture `
            -BasePython $BaseIdentity.executable
        throw ".venv-$Architecture was not created by the selected interpreter ($($mismatches -join ', ')). Remediation: $remediation."
    }
}

function Assert-FenestraEnvironmentProvenance {
    param(
        [Parameter(Mandatory)]
        [string] $Root,

        [Parameter(Mandatory)]
        [ValidateSet("x64")]
        [string] $Architecture,

        [Parameter(Mandatory)]
        [string] $VenvPath,

        [Parameter(Mandatory)]
        [string] $VenvPython,

        [string] $SelectedPython
    )

    $markerPath = Join-Path $VenvPath ".fenestra-build-environment.json"
    $fallbackBase = $null
    if ($SelectedPython -and -not (Test-FenestraPathEquals $SelectedPython $VenvPython)) {
        $fallbackBase = (Resolve-FenestraPythonExecutable $SelectedPython)
    }
    $remediation = Get-FenestraEnvironmentRemediationCommand `
        -Architecture $Architecture `
        -BasePython $fallbackBase

    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
        throw ".venv-$Architecture has no verified Fenestra provenance marker. It may predate architecture-safe builds or contain stale packages. Remediation: $remediation."
    }

    try {
        $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    }
    catch {
        throw ".venv-$Architecture has an unreadable provenance marker. Remediation: $remediation."
    }

    $requiredProperties = @(
        "schemaVersion", "architecture", "basePythonExecutable",
        "basePythonSha256", "basePythonVersion", "basePythonPrefix",
        "basePythonSignerSubject", "basePythonSignerThumbprint",
        "constraintsSha256", "pyprojectSha256", "environmentPythonExecutable",
        "environmentPythonVersion", "environmentBasePrefix", "installedPackagesSha256"
    )
    $missingProperties = @(
        $requiredProperties | Where-Object {
            $marker.PSObject.Properties.Name -notcontains $_
        }
    )
    if ($missingProperties.Count -gt 0) {
        throw ".venv-$Architecture has an incomplete provenance marker (missing $($missingProperties -join ', ')). Remediation: $remediation."
    }

    $baseSelection = if ($SelectedPython -and
        -not (Test-FenestraPathEquals $SelectedPython $VenvPython)) {
        $SelectedPython
    }
    else {
        [string] $marker.basePythonExecutable
    }

    try {
        $baseIdentity = Get-FenestraOfficialPythonIdentity $baseSelection
    }
    catch {
        throw "$($_.Exception.Message) Remediation: $remediation."
    }
    if (-not $fallbackBase) {
        $remediation = Get-FenestraEnvironmentRemediationCommand `
            -Architecture $Architecture `
            -BasePython $baseIdentity.executable
    }

    $venvDetails = Get-FenestraPythonProcessArchitecture $VenvPython
    $constraintsPath = Join-Path $Root "requirements\build-constraints.txt"
    $pyprojectPath = Join-Path $Root "pyproject.toml"
    $mismatches = [System.Collections.Generic.List[string]]::new()

    if ([int] $marker.schemaVersion -ne 1) {
        $mismatches.Add("unsupported marker schema")
    }
    if ([string] $marker.architecture -ne $Architecture) {
        $mismatches.Add("marker architecture")
    }
    if ($baseIdentity.architecture -ne $Architecture) {
        $mismatches.Add("base Python architecture")
    }
    if (-not (Test-FenestraPathEquals ([string] $marker.basePythonExecutable) $baseIdentity.executable)) {
        $mismatches.Add("base Python path")
    }
    if ([string] $marker.basePythonSha256 -ne $baseIdentity.sha256) {
        $mismatches.Add("base Python hash")
    }
    if ([string] $marker.basePythonVersion -ne $baseIdentity.version) {
        $mismatches.Add("base Python version")
    }
    if (-not (Test-FenestraPathEquals ([string] $marker.basePythonPrefix) $baseIdentity.basePrefix)) {
        $mismatches.Add("base Python prefix")
    }
    if ([string] $marker.basePythonSignerSubject -ne $baseIdentity.signerSubject -or
        [string] $marker.basePythonSignerThumbprint -ne $baseIdentity.signerThumbprint) {
        $mismatches.Add("base Python Authenticode identity")
    }
    if (-not (Test-FenestraPathEquals ([string] $marker.environmentPythonExecutable) $VenvPython)) {
        $mismatches.Add("environment Python path")
    }
    if ([string] $marker.environmentPythonVersion -ne $venvDetails.version) {
        $mismatches.Add("environment Python version")
    }
    if (-not (Test-FenestraPathEquals ([string] $marker.environmentBasePrefix) ([string] $venvDetails.base_prefix))) {
        $mismatches.Add("environment base prefix")
    }
    if (-not (Test-Path -LiteralPath $constraintsPath -PathType Leaf) -or
        [string] $marker.constraintsSha256 -ne
        (Get-FileHash -Algorithm SHA256 -LiteralPath $constraintsPath).Hash.ToLowerInvariant()) {
        $mismatches.Add("build constraints")
    }
    if (-not (Test-Path -LiteralPath $pyprojectPath -PathType Leaf) -or
        [string] $marker.pyprojectSha256 -ne
        (Get-FileHash -Algorithm SHA256 -LiteralPath $pyprojectPath).Hash.ToLowerInvariant()) {
        $mismatches.Add("pyproject dependency metadata")
    }

    try {
        $freeze = Get-FenestraPipFreezeSnapshot $VenvPython
        if ([string] $marker.installedPackagesSha256 -ne $freeze.sha256) {
            $mismatches.Add("installed package set")
        }
    }
    catch {
        $mismatches.Add("installed package inventory")
    }

    if ($mismatches.Count -gt 0) {
        throw ".venv-$Architecture provenance mismatch: $($mismatches -join ', '). Rerun .\scripts\bootstrap.ps1 to reinstall into the environment and record it again. If the interpreter changed, run: $remediation."
    }
    return $marker
}

function Write-FenestraEnvironmentProvenance {
    param(
        [Parameter(Mandatory)]
        [string] $Root,

        [Parameter(Mandatory)]
        [ValidateSet("x64")]
        [string] $Architecture,

        [Parameter(Mandatory)]
        [string] $BasePython,

        [Parameter(Mandatory)]
        [string] $VenvPython
    )

    $identity = Get-FenestraOfficialPythonIdentity $BasePython
    if ($identity.architecture -ne $Architecture) {
        throw "The official base Python is $($identity.architecture), not $Architecture."
    }
    $venvDetails = Get-FenestraPythonProcessArchitecture $VenvPython
    $freeze = Get-FenestraPipFreezeSnapshot $VenvPython
    $constraintsPath = Join-Path $Root "requirements\build-constraints.txt"
    $pyprojectPath = Join-Path $Root "pyproject.toml"
    $venvPath = Split-Path -Parent (Split-Path -Parent $VenvPython)
    $markerPath = Join-Path $venvPath ".fenestra-build-environment.json"
    $marker = [ordered]@{
        schemaVersion               = 1
        architecture                = $Architecture
        basePythonExecutable        = $identity.executable
        basePythonSha256            = $identity.sha256
        basePythonVersion           = $identity.version
        basePythonPrefix            = $identity.basePrefix
        basePythonSignerSubject     = $identity.signerSubject
        basePythonSignerThumbprint  = $identity.signerThumbprint
        constraintsSha256           = (Get-FileHash -Algorithm SHA256 -LiteralPath $constraintsPath).Hash.ToLowerInvariant()
        pyprojectSha256             = (Get-FileHash -Algorithm SHA256 -LiteralPath $pyprojectPath).Hash.ToLowerInvariant()
        environmentPythonExecutable = (Resolve-Path -LiteralPath $VenvPython).Path
        environmentPythonVersion    = $venvDetails.version
        environmentBasePrefix       = [IO.Path]::GetFullPath([string] $venvDetails.base_prefix)
        installedPackagesSha256     = $freeze.sha256
        installedPackages           = $freeze.packages
        verifiedAtUtc               = [DateTime]::UtcNow.ToString("o")
    }
    $json = $marker | ConvertTo-Json -Depth 4
    $utf8WithoutBom = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($markerPath, $json + "`n", $utf8WithoutBom)
    Write-Host "[environment] Wrote verified provenance marker: $markerPath."
    return $markerPath
}

function Remove-FenestraWorkspacePath {
    param(
        [Parameter(Mandatory)]
        [string] $Root,

        [Parameter(Mandatory)]
        [string] $Path,

        [switch] $Recurse
    )

    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd([char[]] @('\', '/'))
    $targetPath = if ([IO.Path]::IsPathRooted($Path)) {
        [IO.Path]::GetFullPath($Path)
    }
    else {
        [IO.Path]::GetFullPath((Join-Path $rootPath $Path))
    }
    $rootPrefix = $rootPath + [IO.Path]::DirectorySeparatorChar
    if ($targetPath.Equals($rootPath, [StringComparison]::OrdinalIgnoreCase) -or
        -not $targetPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the Fenestra workspace: $targetPath."
    }

    $rootItem = Get-Item -LiteralPath $rootPath -Force
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing recursive deletion because the workspace root is a reparse point: $rootPath."
    }
    $relative = $targetPath.Substring($rootPrefix.Length)
    $current = $rootPath
    foreach ($component in $relative.Split(
            [char[]] @('\', '/'),
            [StringSplitOptions]::RemoveEmptyEntries
        )) {
        $current = Join-Path $current $component
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing recursive deletion through a reparse point: $current."
            }
        }
    }

    if (-not (Test-Path -LiteralPath $targetPath)) {
        return
    }
    Write-Host "[clean] Removing $targetPath."
    if ($Recurse) {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    }
    else {
        Remove-Item -LiteralPath $targetPath -Force
    }
}

function Enter-FenestraReleaseBuildLock {
    param(
        [Parameter(Mandatory)]
        [string] $Root
    )

    $rootPath = [IO.Path]::GetFullPath($Root).TrimEnd([char[]] @('\', '/'))
    $ownerToken = "$PID|$rootPath"
    $ownerVariable = "FENESTRA_RELEASE_BUILD_LOCK_OWNER"
    $currentOwner = [Environment]::GetEnvironmentVariable(
        $ownerVariable,
        [EnvironmentVariableTarget]::Process
    )
    if ($currentOwner -eq $ownerToken) {
        return [pscustomobject]@{
            OwnsLock      = $false
            LockPath      = Join-Path $rootPath "build\.release-build.lock"
            OwnerToken    = $ownerToken
            PreviousOwner = $currentOwner
            Stream        = $null
        }
    }

    $buildRoot = Join-Path $rootPath "build"
    New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null
    $lockPath = Join-Path $buildRoot ".release-build.lock"
    try {
        $stream = [IO.File]::Open(
            $lockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
    }
    catch [IO.IOException] {
        $message = "Another Fenestra release build already owns $lockPath. " +
        "Wait for that build to finish before starting another build or installer command."
        throw $message
    }

    try {
        $payload = @(
            "PID=$PID"
            "Root=$rootPath"
            "StartedAtUtc=$([DateTime]::UtcNow.ToString('o'))"
        ) -join [Environment]::NewLine
        $bytes = [Text.Encoding]::UTF8.GetBytes($payload + [Environment]::NewLine)
        $stream.SetLength(0)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        [Environment]::SetEnvironmentVariable(
            $ownerVariable,
            $ownerToken,
            [EnvironmentVariableTarget]::Process
        )
    }
    catch {
        $stream.Dispose()
        throw
    }

    Write-Host "[build-lock] Acquired exclusive release-build ownership: $lockPath."
    return [pscustomobject]@{
        OwnsLock      = $true
        LockPath      = $lockPath
        OwnerToken    = $ownerToken
        PreviousOwner = $currentOwner
        Stream         = $stream
    }
}

function Exit-FenestraReleaseBuildLock {
    param(
        [Parameter(Mandatory)]
        [psobject] $Token
    )

    if (-not [bool] $Token.OwnsLock) {
        return
    }

    try {
        $Token.Stream.Dispose()
    }
    finally {
        [Environment]::SetEnvironmentVariable(
            "FENESTRA_RELEASE_BUILD_LOCK_OWNER",
            $Token.PreviousOwner,
            [EnvironmentVariableTarget]::Process
        )
    }
    Write-Host "[build-lock] Released exclusive release-build ownership: $($Token.LockPath)."
}

function Invoke-FenestraNativeCommand {
    param(
        [Parameter(Mandatory)]
        [string] $FilePath,

        [Parameter(Mandatory)]
        [string[]] $ArgumentList,

        [Parameter(Mandatory)]
        [string] $TranscriptPath
    )

    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw "The native executable does not exist: $FilePath."
    }
    $resolvedFilePath = (Resolve-Path -LiteralPath $FilePath).Path
    $lines = [System.Collections.Generic.List[string]]::new()
    $previousPreference = $ErrorActionPreference
    $exitCode = $null
    try {
        # In Windows PowerShell 5.1, redirected native stderr is represented as
        # ErrorRecord objects. PyInstaller logs normally to stderr, so collect
        # it under Continue and preserve the real native exit code explicitly.
        $ErrorActionPreference = "Continue"
        # LASTEXITCODE is an automatic global. Clear the global value so a
        # launch failure cannot reuse a successful code from an earlier tool.
        $global:LASTEXITCODE = $null
        & $resolvedFilePath @ArgumentList 2>&1 | ForEach-Object {
            $line = $_.ToString()
            Write-Host $line
            $lines.Add($line)
        }
        $exitCode = $global:LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }

    $parent = Split-Path -Parent $TranscriptPath
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $utf8WithoutBom = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllLines($TranscriptPath, $lines, $utf8WithoutBom)
    if ($null -eq $exitCode) {
        throw "The native command could not be launched reliably: $resolvedFilePath."
    }
    return [int] $exitCode
}

function Invoke-FenestraBoundedProcess {
    param(
        [Parameter(Mandatory)]
        [string] $FilePath,

        [string[]] $ArgumentList = @(),

        [ValidateRange(1, 2147483)]
        [int] $TimeoutSeconds = 120,

        [switch] $Hidden
    )

    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw "The bounded process executable does not exist: $FilePath."
    }

    $startParameters = @{
        FilePath     = (Resolve-Path -LiteralPath $FilePath).Path
        ArgumentList = $ArgumentList
        PassThru     = $true
    }
    if ($Hidden) {
        $startParameters.WindowStyle = "Hidden"
    }

    $process = Start-Process @startParameters
    try {
        $timeoutMilliseconds = [int] ([long] $TimeoutSeconds * 1000)
        if (-not $process.WaitForExit($timeoutMilliseconds)) {
            $taskkillPath = Join-Path $env:SystemRoot "System32\taskkill.exe"
            $taskkillResult = "taskkill.exe is unavailable"
            if (Test-Path -LiteralPath $taskkillPath -PathType Leaf) {
                $taskkillProcess = $null
                try {
                    $taskkillProcess = Start-Process `
                        -FilePath $taskkillPath `
                        -ArgumentList @("/PID", $process.Id, "/T", "/F") `
                        -WindowStyle Hidden `
                        -PassThru
                    if ($taskkillProcess.WaitForExit(10000)) {
                        $taskkillProcess.WaitForExit()
                        $taskkillResult = "taskkill exit code $($taskkillProcess.ExitCode)"
                    }
                    else {
                        $taskkillProcess.Kill()
                        [void] $taskkillProcess.WaitForExit(5000)
                        $taskkillResult = "taskkill itself exceeded its 10-second timeout"
                    }
                }
                catch {
                    $taskkillResult = "taskkill failed: $($_.Exception.Message)"
                }
                finally {
                    if ($taskkillProcess) {
                        $taskkillProcess.Dispose()
                    }
                }
            }

            $process.Refresh()
            if (-not $process.HasExited -and -not $process.WaitForExit(10000)) {
                try {
                    $process.Kill()
                }
                catch {
                    throw "Process $($process.Id) exceeded the $TimeoutSeconds-second timeout and could not be terminated. $taskkillResult. Parent-process termination also failed: $($_.Exception.Message)."
                }
                if (-not $process.WaitForExit(10000)) {
                    throw "Process $($process.Id) exceeded the $TimeoutSeconds-second timeout and did not exit after termination. $taskkillResult."
                }
            }
            throw "Process $($process.Id) exceeded the $TimeoutSeconds-second timeout and was terminated. $taskkillResult."
        }

        # The parameterless wait flushes process state before ExitCode is read.
        $process.WaitForExit()
        return [int] $process.ExitCode
    }
    finally {
        $process.Dispose()
    }
}

function Write-FenestraAdministratorWarning {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Write-Warning "This terminal is elevated. PyInstaller release builds should run from a non-administrator terminal."
    }
}

function Get-FenestraAppVersion {
    param(
        [Parameter(Mandatory)]
        [string] $Root
    )

    $match = Select-String -LiteralPath (Join-Path $Root "fenestra\app\config.py") -Pattern 'APP_VERSION\s*=\s*"([^"]+)"'
    if (-not $match) {
        throw "APP_VERSION was not found in fenestra/app/config.py."
    }
    return $match.Matches.Groups[1].Value
}

function Get-FenestraSourceFingerprint {
    param(
        [Parameter(Mandatory)]
        [string] $Root
    )

    $paths = @(& git -C $Root ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0) {
        throw "Running git ls-files failed while computing the release input fingerprint."
    }
    $builder = [Text.StringBuilder]::new()
    foreach ($relative in ($paths | Sort-Object -Unique)) {
        $absolute = Join-Path $Root $relative
        if (Test-Path -LiteralPath $absolute -PathType Leaf) {
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $absolute).Hash.ToLowerInvariant()
            [void] $builder.Append($relative.Replace('\', '/')).Append("`0").Append($hash).Append("`n")
        }
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($builder.ToString())
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash($bytes)
    }
    finally {
        $sha256.Dispose()
    }
    return ([BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
}

function Get-FenestraDirectoryFingerprint {
    param(
        [Parameter(Mandatory)]
        [string] $Directory,

        [string[]] $ExcludeNames = @()
    )

    $resolved = (Resolve-Path -LiteralPath $Directory).Path
    $builder = [Text.StringBuilder]::new()
    $files = Get-ChildItem -LiteralPath $resolved -Recurse -File | Sort-Object FullName
    foreach ($file in $files) {
        if ($file.Name -in $ExcludeNames) {
            continue
        }
        $relative = $file.FullName.Substring($resolved.Length).TrimStart([char[]] @('\', '/'))
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
        [void] $builder.Append($relative.Replace('\', '/')).Append("`0").Append($hash).Append("`n")
    }
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($builder.ToString()))
    }
    finally {
        $sha256.Dispose()
    }
    return ([BitConverter]::ToString($digest)).Replace("-", "").ToLowerInvariant()
}

function Invoke-FenestraPreflight {
    param(
        [Parameter(Mandatory)]
        [pscustomobject] $Context,

        [ValidateSet("base", "full")]
        [string] $Mode = "full",

        [string] $PythonExecutable,

        [string] $ReportPath
    )

    if (-not $PythonExecutable) {
        $PythonExecutable = $Context.Python
    }
    if (-not $ReportPath) {
        $ReportPath = $Context.EnvironmentReport
    }
    New-Item -ItemType Directory -Force -Path $Context.BuildRoot | Out-Null
    $arguments = @(
        "scripts\verify_python_environment.py",
        "--architecture", $Context.Architecture,
        "--mode", $Mode,
        "--report", $ReportPath
    )
    & $PythonExecutable @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python $Mode architecture/ABI preflight failed for $($Context.Architecture)."
    }
}
