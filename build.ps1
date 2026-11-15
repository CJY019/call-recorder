[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '0.1.0',
    [switch]$SmokeTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess) {
    throw 'Build with 64-bit PowerShell on Windows.'
}

$repo = $PSScriptRoot
$build = Join-Path $repo 'packaging\_build'
$downloads = Join-Path $build 'downloads'
$logs = Join-Path $build 'logs'
$dist = Join-Path $repo 'dist'
New-Item -ItemType Directory -Force -Path $build, $downloads, $logs, $dist | Out-Null
$toolchain = Get-Content -LiteralPath (Join-Path $repo 'packaging\toolchain.json') -Raw | ConvertFrom-Json
$utf8 = New-Object System.Text.UTF8Encoding($false)
$pythonHome = Join-Path $build 'python'
$python = Join-Path $pythonHome 'python.exe'
$pythonRegistration = 'HKCU:\Software\Python\PythonCore\3.13\InstallPath'
$machinePythonRegistration = 'HKLM:\Software\Python\PythonCore\3.13\InstallPath'
$pythonCleanup = $false
$cleanupFailed = $false
$pythonInstaller = $null
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Get-RegistryValue([string]$Path, [string]$Name = '') {
    if (Test-Path -LiteralPath $Path) {
        return (Get-Item -LiteralPath $Path).GetValue($Name)
    }
    return $null
}

function Test-SamePath([string]$First, [string]$Second) {
    if (-not $First -or -not $Second) { return $false }
    return [IO.Path]::GetFullPath($First).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Second).TrimEnd('\')
}

$machinePythonBefore = Get-RegistryValue $machinePythonRegistration

function Assert-TrustedSignature([string]$Path, [string]$Publisher) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
        -not $signature.SignerCertificate.Subject.Contains($Publisher)) {
        throw "Publisher signature verification failed: $Path ($($signature.Status)). Expected $Publisher."
    }
}

function Get-PinnedDownload($Tool) {
    $target = Join-Path $downloads $Tool.filename
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
        Write-Host "Downloading $($Tool.filename) from the pinned official release..."
        $temporary = $target + '.partial'
        Invoke-WebRequest -UseBasicParsing -Uri $Tool.url -OutFile $temporary
        Move-Item -LiteralPath $temporary -Destination $target -Force
    }
    $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Tool.sha256) {
        throw "SHA256 verification failed: $target. Remove this cached file and retry."
    }
    Assert-TrustedSignature $target $Tool.signer
    return $target
}

function ConvertTo-NativeArgument([string]$Argument) {
    # CreateProcess quoting; no command shell is involved.
    $escaped = [regex]::Replace($Argument, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-BuildTool([string]$Executable, [string[]]$Arguments, [string]$LogName, [int[]]$Accepted = @(0)) {
    $stdout = Join-Path $logs ($LogName + '.stdout.log')
    $stderr = Join-Path $logs ($LogName + '.stderr.log')
    $commandLine = (@($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    $process = Start-Process -FilePath $Executable -ArgumentList $commandLine -WorkingDirectory $repo `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -Wait -PassThru
    if ($Accepted -notcontains $process.ExitCode) {
        Get-Content -LiteralPath $stdout -Tail 30 | Write-Host
        Get-Content -LiteralPath $stderr -Tail 50 | Write-Host
        throw "$LogName failed with exit code $($process.ExitCode). Logs: $logs"
    }
}

Push-Location -LiteralPath $repo
try {
    $pythonInstaller = Get-PinnedDownload $toolchain.python
    $innoInstaller = Get-PinnedDownload $toolchain.inno
    # The standard CPython installer registers this version even with a custom
    # TargetDir. Refuse to replace another per-user installation, and uninstall
    # our own temporary runtime in finally to restore launcher precedence.
    $registeredPython = Get-RegistryValue $pythonRegistration
    if (Test-Path -LiteralPath (Split-Path -Parent $pythonRegistration)) {
        if (-not (Test-SamePath $registeredPython $pythonHome)) {
            throw 'An existing per-user Python 3.13 installation must be preserved. Build on a clean Windows runner instead.'
        }
        $pythonCleanup = $true
    }
    $pythonStamp = Join-Path $pythonHome '.toolchain.sha256'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $pythonStamp) -or
        (Get-Content -LiteralPath $pythonStamp -Raw).Trim() -ne $toolchain.python.sha256) {
        Write-Host "Preparing private CPython $($toolchain.python.version) x64..."
        Invoke-BuildTool $pythonInstaller @(
            '/quiet', '/norestart', 'InstallAllUsers=0', "TargetDir=$pythonHome", 'Include_pip=1', 'Include_tcltk=1',
            'Include_test=0', 'Include_doc=0', 'Include_dev=0', 'Include_launcher=0', 'InstallLauncherAllUsers=0', 'PrependPath=0',
            'AppendPath=0', 'AssociateFiles=0', 'Shortcuts=0', 'CompileAll=0', '/log',
            (Join-Path $logs 'python-bootstrap.log')
        ) 'bootstrap-python' @(0, 3010)
        $pythonCleanup = $true
        [IO.File]::WriteAllText($pythonStamp, $toolchain.python.sha256, $utf8)
    }
    Assert-TrustedSignature $python $toolchain.python.signer

    $innoHome = Join-Path $build 'inno'
    $compiler = Join-Path $innoHome 'ISCC.exe'
    # Retire a registered private bootstrap from an older build-script revision.
    # The official portable mode creates no uninstall entry or file associations.
    $innoRegistration = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1'
    if (Test-SamePath (Get-RegistryValue $innoRegistration 'InstallLocation') $innoHome) {
        $privateUninstaller = Join-Path $innoHome 'unins000.exe'
        if (-not (Test-Path -LiteralPath $privateUninstaller -PathType Leaf)) {
            throw 'The registered private Inno bootstrap cannot be safely cleaned up: its uninstaller is missing.'
        }
        Invoke-BuildTool $privateUninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
            ('/LOG=' + (Join-Path $logs 'inno-bootstrap-uninstall.log'))) 'uninstall-old-private-inno'
    }
    $innoStamp = Join-Path $innoHome '.portable-toolchain.sha256'
    if (-not (Test-Path -LiteralPath $compiler -PathType Leaf) -or -not (Test-Path -LiteralPath $innoStamp) -or
        (Get-Content -LiteralPath $innoStamp -Raw).Trim() -ne $toolchain.inno.sha256) {
        Write-Host "Preparing private Inno Setup $($toolchain.inno.version)..."
        Invoke-BuildTool $innoInstaller @(
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/NOICONS', '/PORTABLE=1', '/TASKS=',
            "/DIR=$innoHome", ('/LOG=' + (Join-Path $logs 'inno-bootstrap.log'))
        ) 'bootstrap-inno'
        [IO.File]::WriteAllText($innoStamp, $toolchain.inno.sha256, $utf8)
    }
    Assert-TrustedSignature $compiler $toolchain.inno.signer

    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONHASHSEED = '0'
    $env:PYTHONOPTIMIZE = $null
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    if (-not $env:SOURCE_DATE_EPOCH) { $env:SOURCE_DATE_EPOCH = '1794787200' }
    $venv = Join-Path $build 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Invoke-BuildTool $python @('-m', 'venv', $venv) 'create-venv'
    }
    Write-Host 'Installing exact, hash-checked wheels from PyPI...'
    Invoke-BuildTool $venvPython @(
        '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
        '--index-url', 'https://pypi.org/simple', '--require-hashes', '--only-binary=:all:',
        '--cache-dir', (Join-Path $build 'pip-cache'), '-r', (Join-Path $repo 'requirements-build.txt')
    ) 'install-dependencies'
    Invoke-BuildTool $venvPython @('-X', 'utf8', (Join-Path $repo 'packaging\prepare_build.py'), $Version) 'prepare-build'
    Write-Host 'Running the synthetic unit tests (no real audio capture)...'
    Invoke-BuildTool $venvPython @('-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v') 'unit-tests'
    Write-Host 'Freezing the application and its Python/Tk/NumPy/audio runtime...'
    Invoke-BuildTool $venvPython @(
        '-X', 'utf8', '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--distpath', $dist, '--workpath', (Join-Path $build 'pyinstaller'),
        (Join-Path $repo 'packaging\CallRecorder.spec')
    ) 'pyinstaller'

    $application = Join-Path $dist 'CallRecorder'
    foreach ($document in @('LICENSE', 'THIRD_PARTY_NOTICES.md')) {
        $source = Join-Path $repo $document
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $application $document) -Force
        }
    }
    Copy-Item -LiteralPath (Join-Path $repo 'packaging\installed-help.txt') `
        -Destination (Join-Path $application ([char]0x4F7F + [string][char]0x7528 + [char]0x8BF4 + [char]0x660E + '.txt')) -Force
    Invoke-BuildTool $venvPython @('-X', 'utf8', (Join-Path $repo 'packaging\verify_bundle.py'), $application) 'verify-bundle'
    Write-Host 'Compiling the per-user Windows installer...'
    Invoke-BuildTool $compiler @("/DAppVersion=$Version", "/DRepoRoot=$repo", (Join-Path $repo 'packaging\installer.iss')) 'inno-compile'
    $setup = Join-Path $dist "CallRecorder-Setup-$Version-x64.exe"
    $hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $dist 'SHA256SUMS.txt'), "$hash  $([IO.Path]::GetFileName($setup))`n", $utf8)
    if ($SmokeTest) {
        Write-Host 'Verifying installation, capture-disabled GUI launch, uninstallation and retained user data...'
        Invoke-BuildTool $venvPython @('-X', 'utf8', (Join-Path $repo 'packaging\smoke_install.py'), $setup) 'smoke-install'
    }
    Write-Host "Built: $setup"
    Write-Host "SHA256: $hash"
    Write-Host "Build logs: $logs"
}
catch {
    Write-Host "Build failed. Diagnostic logs: $logs"
    Write-Error -ErrorAction Continue $_
    exit 1
}
finally {
    if ($pythonCleanup -and $pythonInstaller) {
        try {
            if (-not (Test-SamePath (Get-RegistryValue $pythonRegistration) $pythonHome)) {
                throw 'Private Python ownership could not be confirmed. Its registration was left untouched.'
            }
            Write-Host 'Removing temporary Python installation and its own per-user registration...'
            Invoke-BuildTool $pythonInstaller @('/uninstall', '/quiet', '/norestart', '/log',
                (Join-Path $logs 'python-bootstrap-uninstall.log')) 'cleanup-python' @(0, 3010)
            if (Test-SamePath (Get-RegistryValue $pythonRegistration) $pythonHome) {
                throw 'The official Python uninstaller retained its private registration; inspect cleanup logs.'
            }
            if ((Get-RegistryValue $machinePythonRegistration) -ne $machinePythonBefore) {
                throw 'The pre-existing machine Python registration changed; inspect cleanup logs.'
            }
        }
        catch {
            $cleanupFailed = $true
            Write-Error -ErrorAction Continue $_
        }
    }
    Pop-Location
    if ($cleanupFailed) { exit 1 }
}
