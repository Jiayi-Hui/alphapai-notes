<#
.SYNOPSIS
    Store the AlphaPai login in Windows Credential Manager.

.DESCRIPTION
    You type the account and password into THIS window. The value goes straight
    into Credential Manager under the target 'AlphaPai:Login'. It is never
    written to the repository, never passed as a command-line argument (which
    would be visible in the process list), and never echoed back to any agent
    driving this skill.

    Run it directly, or via Open-AlphaPaiCredentialPrompt.ps1 which opens a
    separate window for it.
#>
[CmdletBinding()]
param(
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
$target = 'AlphaPai:Login'
$site   = 'https://alphapai-web.rabyte.cn'

if (-not ('AlphaPaiCredential.NativeMethods' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;

namespace AlphaPaiCredential
{
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct CREDENTIAL
    {
        public UInt32 Flags;
        public UInt32 Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public UInt32 CredentialBlobSize;
        public IntPtr CredentialBlob;
        public UInt32 Persist;
        public UInt32 AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    public static class NativeMethods
    {
        [DllImport("Advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CredWrite(ref CREDENTIAL credential, UInt32 flags);

        public static void Write(string target, string username, IntPtr password, int passwordChars, string comment)
        {
            var credential = new CREDENTIAL
            {
                Type = 1,
                TargetName = target,
                UserName = username,
                CredentialBlob = password,
                CredentialBlobSize = checked((UInt32)(passwordChars * 2)),
                Persist = 2,
                Comment = comment
            };
            if (!CredWrite(ref credential, 0))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        }
    }
}
'@
}

Write-Host 'AlphaPai credential setup' -ForegroundColor Cyan
Write-Host "Site this credential is used for : $site"
Write-Host "Credential Manager target        : $target"
Write-Host 'Your input stays in this window. Nothing is written to the repo,'
Write-Host 'and nothing is shown to the agent.'
Write-Host ''

$account = (Read-Host -Prompt 'AlphaPai account (phone or email)').Trim()
if ([string]::IsNullOrWhiteSpace($account)) {
    throw 'An account name is required.'
}
$password = Read-Host -AsSecureString -Prompt 'AlphaPai password (input hidden)'
if ($password.Length -eq 0) {
    throw 'Password cannot be empty.'
}

$passwordPtr = [IntPtr]::Zero
try {
    $passwordPtr = [Runtime.InteropServices.Marshal]::SecureStringToCoTaskMemUnicode($password)
    [AlphaPaiCredential.NativeMethods]::Write(
        $target,
        $account,
        $passwordPtr,
        $password.Length,
        'AlphaPai login used by the alphapai-notes skill'
    )
    Write-Host 'Credential saved to Windows Credential Manager.' -ForegroundColor Green
}
finally {
    if ($passwordPtr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeCoTaskMemUnicode($passwordPtr)
    }
    $password.Dispose()
    $account = $null
}

if ($Probe) {
    Write-Host ''
    Write-Host 'Verifying with a headed login...' -ForegroundColor Cyan
    $auth = Join-Path $PSScriptRoot 'ap_auth.py'
    & python -B $auth login --headed --timeout-seconds 60
    $code = $LASTEXITCODE
    Write-Host "Verification finished with exit code $code."
}

Read-Host -Prompt 'Press Enter to close this window' | Out-Null
