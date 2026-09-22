<#
.SYNOPSIS
    Open a separate window for AlphaPai credential entry.

.DESCRIPTION
    Launches Save-AlphaPaiCredential.ps1 in its own interactive PowerShell
    window so the user types the account and password there. The calling
    process - and any agent driving it - only learns whether the prompt
    completed, never what was entered.

    Add -Probe to verify the stored credential with one headed login attempt
    right after saving it.
#>
[CmdletBinding()]
param(
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
$saveScript = Join-Path $PSScriptRoot 'Save-AlphaPaiCredential.ps1'
if (-not (Test-Path -LiteralPath $saveScript -PathType Leaf)) {
    throw "Credential setup script is missing: $saveScript"
}

$quoted = '"' + $saveScript.Replace('"', '\"') + '"'
$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $quoted)
if ($Probe) { $arguments += '-Probe' }

$process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
    -WindowStyle Normal -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Credential setup did not complete successfully (exit code $($process.ExitCode))."
}
Write-Output (ConvertTo-Json -Compress -InputObject @{ prompt_completed = $true; target = 'AlphaPai:Login' })
