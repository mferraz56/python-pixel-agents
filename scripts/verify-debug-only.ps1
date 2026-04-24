<#
.SYNOPSIS
  Verify the debug-log ingestion path: CopilotDebugAdapter tailing JSONL files.
.DESCRIPTION
  Same approach as verify-otel-only.ps1 but expects activity from the local
  Copilot Chat debug-logs folder. To isolate, you can run with PIXEL_AGENTS_OTEL_TRACES_FILE
  pointing at /dev/null in the container env so OTel cannot contribute.
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

Write-Host "[verify-debug] baseline snapshot..."
$baseline = @(Get-Sessions -BaseUrl $Url -Tok $Token | ForEach-Object { "$($_.providerId)/$($_.sessionId)" })
Write-Host "[verify-debug] baseline count: $($baseline.Count)"
Write-Host "[verify-debug] now use Copilot Chat to generate a turn (writes to debug-logs)."
Write-Host "[verify-debug] watching for $TimeoutSeconds seconds..."

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $current = @(Get-Sessions -BaseUrl $Url -Tok $Token | ForEach-Object { "$($_.providerId)/$($_.sessionId)" })
    $new = $current | Where-Object { $baseline -notcontains $_ }
    if ($new) {
        Write-Host "[verify-debug] OK - new sessions detected:" -ForegroundColor Green
        $new | ForEach-Object { Write-Host "  + $_" }
        exit 0
    }
}

Write-Host "[verify-debug] FAIL - no new sessions in $TimeoutSeconds seconds." -ForegroundColor Red
Write-Host "  Check:"
Write-Host "    - docker exec pixel-agents-py ls -la /copilot-debug-logs | Select-Object -First 5"
Write-Host "    - PIXEL_AGENTS_DEBUG_LOG_DIR mount in docker-compose.yml is correct"
exit 1
