$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install dependencies.' }
$model = Join-Path $PSScriptRoot 'data\models\w600k_r50.onnx'
if (-not (Test-Path -LiteralPath $model)) {
    Write-Host 'Face recognition uses the ArcFace model (~275 MB, licensed for non-commercial research use only).'
    $answer = Read-Host 'Download it now? [y/N]'
    if ($answer -match '^(y|yes)$') {
        & $python download_arcface.py
        if ($LASTEXITCODE -ne 0) { Write-Warning 'ArcFace download did not finish. Run .\download_arcface.py later to enable face recognition.' }
    } else {
        Write-Host 'Skipping face recognition. Run .\download_arcface.py later to enable it.'
    }
}
& $python app.py @args
