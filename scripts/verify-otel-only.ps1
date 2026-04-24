<#
.SYNOPSIS
  Verify the OTel-only ingestion path: collector -> file exporter -> CopilotOtelAdapter.
.DESCRIPTION
  Snapshots the current /api/viewer/sessions list, then waits for a new session
  to appear (i.e. caused by Copilot Chat activity producing OTel spans).
  Run this with PIXEL_AGENTS_DEBUG_LOG_DIR pointed at an empty path to ensure
  the only ingestion route is the OTel file exporter.
.PARAMETER Token
  Auth token (default: dev-token-change-me)
.PARAMETER Url
  Base URL (default: http://localhost:8765)
.PARAMETER TimeoutSeconds
  How long to wait for a new session.
#>
param(
    [string]$Token = $env:PIXEL_AGENTS_TOKEN,
    [string]$Url = "http://localhost:8765",
    [int]$TimeoutSeconds = 120
)

if ([string]::IsNullOrWhiteSpace($Token)) { $Token = "dev-token-change-me" }

function Get-Sessions {
    param([string]$BaseUrl, [string]$Tok)
    $r = Invoke-WebRequest -Uri "$BaseUrl/api/viewer/sessions?token=$Tok" -UseBasicParsing
    return ($r.Content | ConvertFrom-Json).sessions
}

Write-Host "[verify-otel] baseline snapshot..."
$baseline = @(Get-Sessions -BaseUrl $Url -Tok $Token | ForEach-Object { "$($_.providerId)/$($_.sessionId)" })
Write-Host "[verify-otel] baseline count: $($baseline.Count)"
Write-Host "[verify-otel] now use Copilot Chat to generate at least one turn."
Write-Host "[verify-otel] watching for new sessions for $TimeoutSeconds seconds..."

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $current = @(Get-Sessions -BaseUrl $Url -Tok $Token | ForEach-Object { "$($_.providerId)/$($_.sessionId)" })
    $new = $current | Where-Object { $baseline -notcontains $_ }
    if ($new) {
        Write-Host "[verify-otel] OK - new sessions detected:" -ForegroundColor Green
        $new | ForEach-Object { Write-Host "  + $_" }
        exit 0
    }
}

Write-Host "[verify-otel] FAIL - no new sessions in $TimeoutSeconds seconds." -ForegroundColor Red
Write-Host "  Check:"
Write-Host "    - docker logs mente-meio-maquina-otel-collector-1 --tail 30"
Write-Host "    - docker exec pixel-agents-py ls -la /otel-data"
Write-Host "    - .vscode/settings.json has github.copilot.chat.otel.enabled=true"
exit 1
