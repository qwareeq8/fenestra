<#
.SYNOPSIS
Create the x64 release environment and install the locked frontend dependencies.

.DESCRIPTION
Creates .venv-x64 from an official x64 CPython interpreter, or reuses one that
the same interpreter created, installs the project with its dev and build
extras under requirements/build-constraints.txt, verifies the resulting package
closure and native architectures, records an environment provenance marker,
and runs npm ci with a verified x64 Node.js. Rerun it after changing
pyproject.toml or the constraints; it reinstalls into the existing environment
and records the marker again.

.PARAMETER PythonExecutable
The official x64 CPython 3.13 python.exe that creates the environment. When
omitted, the Python Launcher selects CPython 3.13.

.PARAMETER NodeExecutable
The x64 Node.js 24 LTS node.exe for the frontend dependencies.

.PARAMETER PackageIndexUrl
The only package index pip installs from. The bootstrap runs pip in isolated
mode, so pip ignores its configuration files and PIP_* environment variables,
and an extra index cannot supply a pinned package. Pass an HTTPS mirror of PyPI here when
the default is unreachable. The default is https://pypi.org/simple.

.PARAMETER SkipFrontendDependencies
Set up only the Python environment.

.EXAMPLE
.\scripts\bootstrap.ps1 -PythonExecutable C:\Python313\python.exe -NodeExecutable "C:\Program Files\nodejs\node.exe"
#>
param(
    [string] $PythonExecutable,

    [string] $NodeExecutable,

    [ValidatePattern("^https://\S+$")]
    [string] $PackageIndexUrl = "https://pypi.org/simple",

    [switch] $SkipFrontendDependencies
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. "$PSScriptRoot\build-common.ps1"

$context = Get-FenestraBuildContext `
    -PythonExecutable $PythonExecutable `
    -Bootstrap
$originalLocation = Get-Location
try {
    Set-Location $context.Root
    New-Item -ItemType Directory -Force -Path $context.BuildRoot | Out-Null

    $node = $null
    if (-not $SkipFrontendDependencies) {
        $node = Resolve-FenestraNodeExecutable $NodeExecutable
        $nodeDetails = Get-FenestraNodeDetails $node
        if ($nodeDetails.architecture -ne $context.Architecture) {
            throw "Selected Node.js is $($nodeDetails.architecture), but the build target is $($context.Architecture). Pass a matching -NodeExecutable."
        }
    }

    Write-FenestraAdministratorWarning
    Write-Host "[bootstrap] Target architecture: $($context.Architecture)."
    Write-Host "[bootstrap] Base Python: $($context.BasePython)."
    Write-Host "[bootstrap] Environment: $($context.VenvPath)."

    $constraints = Join-Path $context.Root "requirements\build-constraints.txt"
    $pyproject = Join-Path $context.Root "pyproject.toml"
    if (-not (Test-Path -LiteralPath $constraints -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pyproject -PathType Leaf)) {
        throw "The build constraints or pyproject dependency metadata is missing."
    }

    $savedEnvironment = Start-FenestraSanitizedEnvironment `
        -PythonExecutable $context.BasePython `
        -NodeExecutable $node
    try {
        $baseReport = Join-Path $context.BuildRoot "base-python.json"
        & $context.BasePython scripts\verify_python_environment.py `
            --architecture $context.Architecture `
            --mode base `
            --report $baseReport
        if ($LASTEXITCODE -ne 0) {
            throw "The selected base interpreter failed architecture/distribution preflight. Use an official x64 CPython 3.13 interpreter."
        }
        $baseIdentity = Get-FenestraOfficialPythonIdentity $context.BasePython
        if ($baseIdentity.architecture -ne $context.Architecture) {
            throw "The verified official base Python is $($baseIdentity.architecture), not $($context.Architecture)."
        }

        $venvPython = Join-Path $context.VenvPath "Scripts\python.exe"
        if (Test-Path -LiteralPath $context.VenvPath) {
            if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
                throw "The existing $($context.VenvPath) is incomplete. Remove it with 'Remove-Item -Recurse -Force .venv-$($context.Architecture)', then rerun this command."
            }
            # Only the interpreter must match. The package set and the
            # dependency metadata may have changed since the marker was
            # written; the constrained reinstall below brings them up to date.
            Assert-FenestraEnvironmentInterpreter `
                -Architecture $context.Architecture `
                -VenvPython $venvPython `
                -BaseIdentity $baseIdentity
            & $venvPython scripts\verify_python_environment.py `
                --architecture $context.Architecture `
                --mode base `
                --report (Join-Path $context.BuildRoot "existing-environment.json")
            if ($LASTEXITCODE -ne 0) {
                throw "The existing .venv-$($context.Architecture) is incompatible or Conda-based. Remove it with 'Remove-Item -Recurse -Force .venv-$($context.Architecture)', then rerun bootstrap."
            }
            Write-Host "[bootstrap] Reinstalling into the existing .venv-$($context.Architecture)."
        }
        else {
            Write-Host "[bootstrap] Creating .venv-$($context.Architecture)..."
            & $context.BasePython -I -m venv $context.VenvPath
            if ($LASTEXITCODE -ne 0) {
                throw "Creating .venv-$($context.Architecture) failed."
            }
        }

        # Switch the sanitized environment to the architecture-qualified venv.
        Restore-FenestraEnvironment $savedEnvironment
        $savedEnvironment = Start-FenestraSanitizedEnvironment `
            -PythonExecutable $venvPython `
            -NodeExecutable $node

        # A marker describes a fully installed and preflighted environment. Remove
        # it before mutation so an interrupted pip run cannot leave stale evidence.
        Remove-Item `
            -LiteralPath (Join-Path $context.VenvPath ".fenestra-build-environment.json") `
            -Force `
            -ErrorAction SilentlyContinue

        $pipReport = Join-Path $context.BuildRoot "pip-install-report.json"
        # Isolated mode ignores pip configuration files and PIP_* variables,
        # so no extra index or option from the developer's setup applies.
        Write-Host "[bootstrap] Installing constrained binary dependencies from $PackageIndexUrl..."
        & $venvPython -I -m pip install `
            --isolated `
            --disable-pip-version-check `
            --index-url $PackageIndexUrl `
            --upgrade `
            --only-binary=:all: `
            --constraint $constraints `
            --report $pipReport `
            -e ".[dev,build]"
        if ($LASTEXITCODE -ne 0) {
            throw "Installing Fenestra's constrained Python dependencies failed."
        }

        & $venvPython -I -m pip check --isolated --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) {
            throw "The pip check command found an inconsistent Python environment."
        }

        & $venvPython -I "$PSScriptRoot\verify_python_constraints.py" `
            --constraints $constraints `
            --report (Join-Path $context.BuildRoot "python-constraints.json") `
            --allow-unconstrained pip `
            --allow-unconstrained fenestra
        if ($LASTEXITCODE -ne 0) {
            throw "The installed Python package closure is not fully and exactly constrained. If a package was removed from the constraints, delete .venv-$($context.Architecture) and bootstrap again."
        }

        $freeze = & $venvPython -I -m pip freeze --isolated --disable-pip-version-check --all
        if ($LASTEXITCODE -ne 0) {
            throw "Running pip freeze failed."
        }
        $freeze | Set-Content `
            -LiteralPath (Join-Path $context.BuildRoot "pip-freeze.txt") `
            -Encoding utf8

        $installedContext = Get-FenestraBuildContext `
            -PythonExecutable $venvPython `
            -Provisioning
        Invoke-FenestraPreflight -Context $installedContext -Mode full

        [void] (Write-FenestraEnvironmentProvenance `
                -Root $context.Root `
                -Architecture $context.Architecture `
                -BasePython $context.BasePython `
                -VenvPython $venvPython)
        $installedContext = Get-FenestraBuildContext `
            -PythonExecutable $context.BasePython

        if (-not $SkipFrontendDependencies) {
            & "$PSScriptRoot\build-frontend.ps1" `
                -PythonExecutable $context.BasePython `
                -NodeExecutable $node `
                -InstallOnly
            if ($LASTEXITCODE -ne 0) {
                throw "Frontend dependency bootstrap failed."
            }
        }
    }
    finally {
        Restore-FenestraEnvironment $savedEnvironment
    }

    Write-Host "[bootstrap] OK: .venv-$($context.Architecture) is architecture and ABI verified."
}
finally {
    Set-Location -LiteralPath $originalLocation.Path
}
