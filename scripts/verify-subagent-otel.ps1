<#
.SYNOPSIS
  Scan replay store for sessions that surfaced subagent messages.
.DESCRIPTION
  Hits /api/viewer/sessions, then for each session GETs the replay and
  counts ``subagentToolStart`` and ``subagentToolDone`` messages. Exits 0
  when at least one session has a non-zero subagent count, 1 otherwise.
  Use after Copilot Chat fires a runSubagent invocation to confirm the
  end-to-end OTel + debug-log subagent pipeline projects correctly.
.PARAMETER Token
  Auth token (default: dev-token-change-me)
.PARAMETER Url
  Base URL (default: http://localhost:8765)
#>
param(
    [string]$Token = $env:PIXEL_AGENTS_TOKEN,
    [string]$Url = "http://localhost:8765"
)

if ([string]::IsNullOrWhiteSpace($Token)) { $Token = "dev-token-change-me" }

$r = Invoke-WebRequest -Uri "$Url/api/viewer/sessions?token=$Token" -UseBasicParsing
$sessions = ($r.Content | ConvertFrom-Json).sessions
Write-Host "[verify-subagent] scanning $($sessions.Count) session(s)..."

$totalStart = 0; $totalDone = 0; $hits = @()
foreach ($s in $sessions) {
    $rr = Invoke-WebRequest -Uri "$Url/api/viewer/replay/$($s.providerId)/$($s.sessionId)?token=$Token" -UseBasicParsing -ErrorAction SilentlyContinue
    if ($null -eq $rr -or $rr.StatusCode -ne 200) { continue }
    $body = $rr.Content | ConvertFrom-Json
    $starts = @($body.messages | Where-Object { $_.type -eq 'subagentToolStart' }).Count
    $dones  = @($body.messages | Where-Object { $_.type -eq 'subagentToolDone'  }).Count
    if ($starts -gt 0 -or $dones -gt 0) {
        $hits += [pscustomobject]@{ session = "$($s.providerId)/$($s.sessionId)"; starts = $starts; dones = $dones }
        $totalStart += $starts; $totalDone += $dones
    }
}

if ($hits.Count -eq 0) {
    Write-Host "[verify-subagent] FAIL - no session has subagent messages." -ForegroundColor Red
    Write-Host "  - confirm Copilot Chat fired a runSubagent invocation"
    Write-Host "  - tail otel: docker exec pixel-agents-py grep -c 'execute_tool runSubagent' /otel-data/copilot-traces.jsonl"
    Write-Host "  - tail debug: docker exec pixel-agents-py ls /copilot-debug-logs/<sid>/runSubagent-*.jsonl"
    exit 1
}

Write-Host "[verify-subagent] OK" -ForegroundColor Green
$hits | Format-Table -AutoSize
Write-Host ("totals: start={0} done={1}" -f $totalStart, $totalDone)
exit 0
