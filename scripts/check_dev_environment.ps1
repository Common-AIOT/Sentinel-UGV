$ErrorActionPreference = 'Stop'

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryDirectory = Split-Path -Parent $scriptDirectory
$failureCount = 0

function Test-RequiredCommand {
    param([Parameter(Mandatory = $true)][string]$CommandName)

    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        Write-Output "[ok] $CommandName"
    }
    else {
        Write-Error "[missing] required command: $CommandName" -ErrorAction Continue
        $script:failureCount++
    }
}

function Test-OptionalCommand {
    param([Parameter(Mandatory = $true)][string]$CommandName)

    if (Get-Command $CommandName -ErrorAction SilentlyContinue) {
        Write-Output "[optional:ok] $CommandName"
    }
    else {
        Write-Output "[optional:skip] $CommandName"
    }
}

Write-Output 'Checking required host tools...'
Test-RequiredCommand -CommandName git
Test-RequiredCommand -CommandName docker

if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Output '[ok] Docker Engine'
    }
    else {
        Write-Error '[unavailable] Docker Engine is not running or is not accessible.' -ErrorAction Continue
        $failureCount++
    }

    docker compose version *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Output '[ok] Docker Compose'
    }
    else {
        Write-Error '[missing] Docker Compose plugin' -ErrorAction Continue
        $failureCount++
    }
}

Write-Output 'Checking module-specific tools (informational until each app is scaffolded)...'
@('java', 'node', 'npm', 'python', 'colcon', 'ros2') | ForEach-Object {
    Test-OptionalCommand -CommandName $_
}

if ($failureCount -eq 0) {
    Push-Location $repositoryDirectory
    try {
        docker compose `
            --file backend/compose.local.yaml `
            config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw 'Backend local Docker Compose configuration validation failed.'
        }
        Write-Output '[ok] Backend local Docker Compose configuration'

        docker compose `
            --file backend/compose.prod.yaml `
            config --quiet
        if ($LASTEXITCODE -ne 0) {
            throw 'Backend production Docker Compose configuration validation failed.'
        }
        Write-Output '[ok] Backend production Docker Compose configuration'
    }
    finally {
        Pop-Location
    }

    Write-Output 'Development environment check passed.'
    exit 0
}

Write-Error "Development environment check failed with $failureCount required issue(s)."
exit 1
