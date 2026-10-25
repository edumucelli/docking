$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$shared = "\\host.lan\Data"
$lab = Join-Path $shared "lab"
New-Item -ItemType Directory -Force -Path $lab | Out-Null
Remove-Item (Join-Path $lab "bootstrap-error.txt") -ErrorAction SilentlyContinue
Start-Transcript -Path (Join-Path $lab "bootstrap.log") -Append
try {
    # This lab has a sparse disk on the Linux host; avoid a hibernation image.
    & powercfg.exe /hibernate off
    if (-not (Test-Path "C:\msys64\usr\bin\bash.exe")) {
        # Pin the stable installer; GitHub's generic latest endpoint is a nightly.
        $installer = Join-Path $env:TEMP "msys2-x86_64-20260927.exe"
        $url = "https://github.com/msys2/msys2-installer/releases/download/2026-09-27/msys2-x86_64-20260927.exe"
        $expected = "416d506c839a2da4bc184eeec8cbaaf1055ee698618228c62dfc4232a6970f0e"
        Invoke-WebRequest $url -OutFile $installer -UseBasicParsing
        if ((Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
            throw "MSYS2 installer checksum mismatch"
        }
        $process = Start-Process $installer -ArgumentList @("in", "--confirm-command", "--accept-messages", "--root", "C:/msys64") -PassThru -Wait
        if ($process.ExitCode -ne 0) { throw "MSYS2 installation failed: $($process.ExitCode)" }
        Remove-Item $installer
    }
    $env:MSYSTEM = "UCRT64"
    $env:CHERE_INVOKING = "1"
    $env:PATH = "C:\msys64\ucrt64\bin;C:\msys64\usr\bin;" + $env:PATH
    $env:PYGI_DLL_PATH = "C:\msys64\ucrt64\bin"
    & C:\msys64\usr\bin\bash.exe --login -c "pacman --noconfirm -Syu"
    # A core-runtime update closes MSYS2 processes. Give it time to finish and
    # use a fresh shell for the package transaction.
    Start-Sleep -Seconds 3
    for ($attempt = 0; $attempt -lt 2; $attempt++) {
        & C:\msys64\usr\bin\bash.exe --login -c "pacman --noconfirm -Syu --needed mingw-w64-ucrt-x86_64-gtk3 mingw-w64-ucrt-x86_64-python mingw-w64-ucrt-x86_64-python-gobject mingw-w64-ucrt-x86_64-python-cairo mingw-w64-ucrt-x86_64-python-pip mingw-w64-ucrt-x86_64-python-pillow mingw-w64-ucrt-x86_64-python-psutil mingw-w64-ucrt-x86_64-python-pytest mingw-w64-ucrt-x86_64-python-numpy"
        if ($LASTEXITCODE -eq 0) { break }
        if ($attempt -eq 1) { throw "GTK/Python dependency installation failed" }
        Start-Sleep -Seconds 5
    }
    & C:\msys64\ucrt64\bin\python.exe -m pip install --break-system-packages "openmeteo-requests>=1.2,<1.5" requests-cache retry-requests
    if ($LASTEXITCODE -ne 0) { throw "Docking Python dependency installation failed" }
    # The runner must live in the interactive user's session, rather than a service.
    $agentFolder = Join-Path $env:LOCALAPPDATA "DockingLab"
    New-Item -ItemType Directory -Force -Path $agentFolder | Out-Null
    $agentPath = Join-Path $agentFolder "agent.py"
    Copy-Item (Join-Path $shared "repo\packaging\windows\lab\agent.py") $agentPath -Force
    $runner = '"C:\msys64\ucrt64\bin\pythonw.exe" "' + $agentPath + '"'
    New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "DockingLabAgent" -Value $runner -PropertyType String -Force | Out-Null
    Start-Process C:\msys64\ucrt64\bin\pythonw.exe -ArgumentList ('"' + $agentPath + '"')
} catch {
    $_ | Out-String | Set-Content (Join-Path $lab "bootstrap-error.txt") -Encoding UTF8
    throw
} finally {
    Stop-Transcript
}
