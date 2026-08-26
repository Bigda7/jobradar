[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArguments
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectDirectory = Split-Path -Parent $PSScriptRoot
$environmentPath = Join-Path $projectDirectory '.env'

function Get-EnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $environmentPath)) {
        return $null
    }

    $prefix = "$Name="
    $line = Get-Content -LiteralPath $environmentPath |
        Where-Object { $_.StartsWith($prefix, [System.StringComparison]::Ordinal) } |
        Select-Object -Last 1

    if ($null -eq $line) {
        return $null
    }

    return $line.Substring($prefix.Length).Trim()
}

if (-not $env:POSTGRES_PASSWORD) {
    $configuredPassword = Get-EnvironmentValue -Name 'POSTGRES_PASSWORD'

    if ($configuredPassword) {
        $env:POSTGRES_PASSWORD = $configuredPassword
    }
    else {
        $databaseUrl = Get-EnvironmentValue -Name 'DATABASE_URL'
        if (-not $databaseUrl) {
            throw 'Set POSTGRES_PASSWORD or DATABASE_URL in .env before running Docker Compose.'
        }

        try {
            $databaseUri = [Uri]$databaseUrl
            $userInfo = $databaseUri.UserInfo.Split(':', 2)
            if ($userInfo.Count -ne 2 -or -not $userInfo[1]) {
                throw 'DATABASE_URL does not contain a password.'
            }
            $env:POSTGRES_PASSWORD = [Uri]::UnescapeDataString($userInfo[1])
        }
        catch {
            throw 'DATABASE_URL is invalid or does not contain a usable password.'
        }
    }
}

Push-Location $projectDirectory
try {
    & docker compose @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
