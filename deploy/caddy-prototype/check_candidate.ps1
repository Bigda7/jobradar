param([switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
$taskBackend = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$taskContext = Join-Path $taskBackend '.deploy/caddy-rebuild-20261013'
$taskOutput = Join-Path $taskContext 'output'
$taskBuilder = 'jobradar-release-check'
$taskGoImage = 'golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c'

function Invoke-CheckedNative {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
}

Push-Location $taskBackend
try {
    if (-not $VerifyOnly) {
        New-Item -ItemType Directory -Path $taskContext -Force | Out-Null
        $taskArchive = Join-Path $taskContext 'source.tar.gz'
        if (-not (Test-Path -LiteralPath $taskArchive)) {
            Invoke-WebRequest 'https://codeload.github.com/caddyserver/caddy/tar.gz/72dd0fb067f6d7826c7f79907670ba4a713bfe37' -OutFile $taskArchive
        }
        if ((Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'e86c49311c0c9f7e9b3b80527f0139e5739dfe28effeaa41c444dd743f0bbd7e') {
            throw 'Pinned source archive checksum mismatch'
        }
        foreach ($taskName in @('Dockerfile', '.dockerignore', 'build_candidate.sh', 'verify_graph.go', 'integration_probe.go')) {
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot $taskName) -Destination $taskContext
        }
        Invoke-CheckedNative docker @('buildx', 'build', '--builder', $taskBuilder, '--target', 'artifacts', '--output', "type=local,dest=$taskOutput", $taskContext)
    }
    Invoke-CheckedNative uv @('run', 'python', 'deploy/caddy-prototype/verify_artifacts.py', $taskOutput)
    Invoke-CheckedNative docker @('run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,exec,size=256m', '--env', 'GOCACHE=/tmp/go-cache', '--mount', "type=bind,source=$PSScriptRoot,target=/tools,readonly", '--workdir', '/tools', $taskGoImage, 'go', 'test', 'verify_graph.go', 'verify_graph_test.go')
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Dockerfile.probe') -Destination $taskOutput
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.dockerignore.probe') -Destination (Join-Path $taskOutput '.dockerignore')
    foreach ($taskArchitecture in @('amd64', 'arm64')) {
        $taskBinary = Join-Path $taskOutput "caddy.$taskArchitecture"
        $taskSarif = Join-Path $taskContext "rebuilt-$taskArchitecture.sarif"
        $taskSbom = Join-Path $taskContext "rebuilt-$taskArchitecture.sbom.json"
        Invoke-CheckedNative docker @('scout', 'cves', "fs://$taskBinary", '--format', 'sarif', '--output', $taskSarif)
        Invoke-CheckedNative docker @('scout', 'cves', "fs://$taskBinary", '--format', 'sbom', '--output', $taskSbom)
        Invoke-CheckedNative docker @('scout', 'cves', "fs://$taskBinary", '--only-severity', 'critical,high', '--exit-code')
        $taskTag = "jobradar-caddy-probe:$taskArchitecture-20261013"
        Invoke-CheckedNative docker @('buildx', 'build', '--builder', $taskBuilder, '--platform', "linux/$taskArchitecture", '--build-arg', "ARCH=$taskArchitecture", '--file', (Join-Path $taskOutput 'Dockerfile.probe'), '--tag', $taskTag, '--load', $taskOutput)
        Invoke-CheckedNative docker @('run', '--rm', '--name', "jobradar-caddy-probe-$taskArchitecture-20261013", '--platform', "linux/$taskArchitecture", '--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m', '--user', '65532:65532', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--memory', '256m', '--cpus', '2', '--pids-limit', '64', $taskTag)
    }
} finally {
    Pop-Location
}
