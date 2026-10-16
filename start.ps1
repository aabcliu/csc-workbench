[CmdletBinding()]
param(
    [ValidateSet('start', 'build', 'stop', 'status', 'logs', 'help')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
if ($Action -eq 'help') {
    Write-Output @'
Usage: .\start.ps1 [start|build|stop|status|logs|help]
  start   Load the bundled prebuilt image (or reuse an installed image) and start.
  build   Build the image from source and start. Internet access is required.
  stop    Stop the service, keeping saved workspaces and analysis results.
  status  Show the container status.
  logs    Follow the service logs (Ctrl-C exits log viewing).

Environment: CSC_PORT, CSC_IMAGE, CSC_IMAGE_ARCHIVE, CSC_PLATFORM, CSC_PROJECT_NAME.
'@
    exit 0
}

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed (exit $LASTEXITCODE)."
    }
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install and start Docker Desktop (or Docker Engine with Compose v2), then retry.'
    }
    Invoke-Docker compose version | Out-Null
    $engineInfo = (Invoke-Docker info --format '{{.OSType}} {{.Architecture}}').Trim()
    switch ($engineInfo) {
        'linux aarch64' { $nativePlatform = 'linux/arm64' }
        'linux arm64' { $nativePlatform = 'linux/arm64' }
        'linux x86_64' { $nativePlatform = 'linux/amd64' }
        'linux amd64' { $nativePlatform = 'linux/amd64' }
        default { throw "Unsupported Docker platform: $engineInfo. Use Linux containers on AMD64 or ARM64." }
    }
    if (-not $env:CSC_PORT) { $env:CSC_PORT = '8765' }
    if ($env:CSC_PORT -notmatch '^[1-9][0-9]{0,4}$' -or [int]$env:CSC_PORT -gt 65535) {
        throw 'CSC_PORT must be an integer from 1 to 65535.'
    }
    if (-not $env:CSC_IMAGE) { $env:CSC_IMAGE = 'csc-workbench:0.1.0' }
    if (-not $env:CSC_PLATFORM) { $env:CSC_PLATFORM = $nativePlatform }
    if ($env:CSC_PLATFORM -notin @('linux/amd64', 'linux/arm64')) {
        throw 'CSC_PLATFORM must be linux/amd64 or linux/arm64.'
    }
    $projectName = if ($env:CSC_PROJECT_NAME) { $env:CSC_PROJECT_NAME } else { 'csc-workbench' }
    $composeArgs = @('compose', '--project-name', $projectName, '-f', (Join-Path $PSScriptRoot 'deploy/compose.yaml'))
    switch ($Action) {
        'stop' { Invoke-Docker @composeArgs down }
        'status' { Invoke-Docker @composeArgs ps }
        'logs' { Invoke-Docker @composeArgs logs --follow --tail=100 }
        default {
            if ($Action -eq 'start') {
                $archive = if ($env:CSC_IMAGE_ARCHIVE) { $env:CSC_IMAGE_ARCHIVE } else { Join-Path $PSScriptRoot 'images/csc-workbench.tar' }
                if ($env:CSC_IMAGE_ARCHIVE -or (Test-Path -LiteralPath $archive -PathType Leaf)) {
                    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw "Image archive not found: $archive" }
                    Invoke-Docker load --input $archive
                }
                $imagePlatform = & docker image inspect --format '{{.Os}}/{{.Architecture}}' $env:CSC_IMAGE 2>$null
                if ($LASTEXITCODE -ne 0) {
                    throw "Prebuilt image $env:CSC_IMAGE is not installed. Extract the matching prebuilt package, or run .\start.ps1 build to build from source."
                }
                if ($imagePlatform.Trim() -ne $env:CSC_PLATFORM) {
                    throw "Image targets $imagePlatform; startup targets $env:CSC_PLATFORM. Use the matching package, or set CSC_PLATFORM explicitly for emulation."
                }
                $upArgs = @('up', '--detach', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120')
            } else {
                $env:BUILDX_GIT_INFO = '0'
                $env:BUILDX_GIT_LABELS = '0'
                $env:BUILDX_NO_DEFAULT_ATTESTATIONS = '1'
                $upArgs = @('up', '--detach', '--build', '--pull', 'never', '--wait', '--wait-timeout', '120')
            }
            New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'runtime') | Out-Null
            try { Invoke-Docker @composeArgs @upArgs }
            catch {
                & docker @composeArgs logs --tail=80
                throw
            }
            Write-Output "CSC Workbench is ready: http://127.0.0.1:$env:CSC_PORT"
            Write-Output 'Try Add program > Load example > Use this program > Generate Tests > Run Verification > Locate Faults.'
        }
    }
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
