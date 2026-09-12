$guardianPreviewRoot = $PSScriptRoot
$guardianPreviewPython = Join-Path $guardianPreviewRoot '.venv\Scripts\pythonw.exe'
$guardianPreviewScript = Join-Path $guardianPreviewRoot 'tools\preview_startup.py'
Start-Process -FilePath $guardianPreviewPython -ArgumentList ('"' + $guardianPreviewScript + '"') -WorkingDirectory $guardianPreviewRoot -WindowStyle Normal
