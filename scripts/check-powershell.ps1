<#
.SYNOPSIS
Check the build scripts for syntax errors and mismatched named arguments.

.DESCRIPTION
Parses every script in this directory and reports each syntax error. It then
checks every call to a function or sibling script defined here and reports a
named argument that the callee does not declare. The check runs on any
PowerShell 7 host, so it catches these errors without a Windows build.

.EXAMPLE
pwsh -NoProfile -File scripts/check-powershell.ps1
#>
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$commonParameters = @(
    "Confirm", "Debug", "ErrorAction", "ErrorVariable", "InformationAction",
    "InformationVariable", "OutBuffer", "OutVariable", "PipelineVariable",
    "ProgressAction", "Verbose", "WarningAction", "WarningVariable", "WhatIf"
)
$problems = [System.Collections.Generic.List[string]]::new()
$trees = @{}

foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Filter "*.ps1") {
    $tokens = $null
    $errors = $null
    $trees[$file.Name] = [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref] $tokens, [ref] $errors)
    foreach ($syntaxError in $errors) {
        $problems.Add(("{0}:{1}: {2}" -f $file.Name, $syntaxError.Extent.StartLineNumber, $syntaxError.Message))
    }
}

function Get-DeclaredParameterNames {
    param($ParamBlock)

    if ($null -eq $ParamBlock) {
        return @()
    }
    return @($ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
}

$functions = @{}
$scripts = @{}
foreach ($name in $trees.Keys) {
    # A function that returns an empty array returns $null, so keep the array.
    $scripts[$name] = @(Get-DeclaredParameterNames $trees[$name].ParamBlock)
    $definitions = $trees[$name].FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
        }, $true)
    foreach ($definition in $definitions) {
        $functions[$definition.Name] = @(Get-DeclaredParameterNames $definition.Body.ParamBlock)
    }
}

foreach ($name in $trees.Keys) {
    $calls = $trees[$name].FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        }, $true)
    foreach ($call in $calls) {
        $callee = $call.CommandElements[0].Extent.Text.Trim('"', "'")
        $declared = $null
        if ($functions.ContainsKey($callee)) {
            $declared = $functions[$callee]
        }
        elseif ($callee -match '\\([\w-]+\.ps1)$' -and $scripts.ContainsKey($Matches[1])) {
            $callee = $Matches[1]
            $declared = $scripts[$callee]
        }
        if ($null -eq $declared) {
            continue
        }
        foreach ($element in $call.CommandElements) {
            if ($element -isnot [System.Management.Automation.Language.CommandParameterAst]) {
                continue
            }
            $parameter = $element.ParameterName
            $matching = @($declared | Where-Object { $_ -like "$parameter*" })
            if ($matching.Count -eq 0 -and $commonParameters -notcontains $parameter) {
                $problems.Add(("{0}:{1}: {2} does not declare -{3}." -f
                        $name, $element.Extent.StartLineNumber, $callee, $parameter))
            }
        }
    }
}

if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Host $_ }
    throw "The PowerShell check found $($problems.Count) problem(s)."
}
Write-Host "[check-powershell] OK: $($trees.Count) scripts parsed, and every named argument is declared."
