param(
    [Parameter(Mandatory = $true)][string]$PublicOrigin,
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$securePassword = Read-Host 'Choose your Frame password (at least 16 characters)' -AsSecureString
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
$previousPassword = $env:FRAME_PASSWORD
try {
    $env:FRAME_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
    if ($env:FRAME_PASSWORD.Length -lt 16) { throw 'Use at least 16 characters.' }
    $pythonCommand = if (Test-Path '.venv/Scripts/python.exe') { '.\.venv\Scripts\python.exe' } else { 'python' }
    Write-Host 'Phone login username: frame'
    Write-Host 'Point your Cloudflare Tunnel service at http://127.0.0.1:'$Port
    & $pythonCommand app.py --port $Port --public-origin $PublicOrigin
    if ($LASTEXITCODE -ne 0) { throw 'Frame could not start. Check the message above.' }
} finally {
    $env:FRAME_PASSWORD = $previousPassword
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}
