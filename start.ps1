$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = '.\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & (Join-Path $PSScriptRoot 'setup-runtime.ps1') -Runtime cpu
}
& $python -c "import fastembed, onnxruntime, qdrant_client, cv2, PIL"
if ($LASTEXITCODE -ne 0) { throw 'Dependencies are missing. Run setup-runtime.ps1 -Runtime cpu (or gpu), then start again.' }
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
# FastEmbed's tokenizer JSON loader uses the process default encoding.
# SigLIP 2 includes Unicode tokens, so Windows must run Python in UTF-8 mode.
& $python -X utf8 app.py @args
