param([ValidateSet('cpu', 'gpu')][string]$Runtime = 'cpu')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$runtimePython = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
# The CPU and GPU distributions share Python import names; do not install both.
& $runtimePython -m pip uninstall -y fastembed fastembed-gpu onnxruntime onnxruntime-gpu
if ($LASTEXITCODE -ne 0) { throw 'Could not remove the previous runtime packages.' }
$runtimeRequirements = if ($Runtime -eq 'gpu') { 'requirements-gpu.txt' } else { 'requirements.txt' }
& $runtimePython -m pip install -r $runtimeRequirements
if ($LASTEXITCODE -ne 0) { throw 'Could not install the selected runtime.' }
& $runtimePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
Write-Host "Installed $Runtime runtime. start.ps1 preserves this choice."
