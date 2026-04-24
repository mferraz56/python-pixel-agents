<#
.SYNOPSIS
  Verify replay survives container restart.
.DESCRIPTION
  1. Lists current sessions.
  2. Picks the largest one (by replay size) and snapshots its message-type histogram.
  3. Restarts the pixel-agents-py container.
  4. Re-fetches the same replay and asserts the histogram is identical (>= same counts).
#>
param(
    [string]$Token = $env:PIXEL_AGENTS_TOKEN,
    [string]$Url = "http://localhost:8765"
)

if ([string]::IsNullOrWhiteSpace($Token)) { $Token = "dev-token-change-me" }

function Get-Replay {
    param([string]$Provider, [string]$Sid)
    $r = Invoke-WebRequest -Uri "$Url/api/viewer/replay/$Provider/$Sid`?token=$Token" -UseBasicParsing
    return $r.Content | ConvertFrom-Json
}

Write-Host "[verify-replay] listing sessions..."
$sessions = (Invoke-WebRequest -Uri "$Url/api/viewer/sessions?token=$Token" -UseBasicParsing).Content | ConvertFrom-Json
$sessions = $sessions.sessions
if (-not $sessions -or $sessions.Count -eq 0) {
    Write-Host "[verify-replay] FAIL - no sessions in replay store." -ForegroundColor Red
    exit 1
}

# Pick the session with the most envelopes from a small sample (cap at 5 probes).
$best = $null; $bestCount = -1
foreach ($s in ($sessions | Select-Object -First 5)) {
    try {
        $body = Get-Replay -Provider $s.providerId -Sid $s.sessionId
        if ($body.envelopeCount -gt $bestCount) {
            $best = $s; $bestCount = $body.envelopeCount; $beforeBody = $body
        }
    } catch { continue }
}

if (-not $best) {
    Write-Host "[verify-replay] FAIL - could not load any replay." -ForegroundColor Red
    exit 1
}

$beforeHisto = $beforeBody.messages | Group-Object type | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Count)" }
Write-Host "[verify-replay] target: $($best.providerId)/$($best.sessionId)  envelopes=$bestCount"
Write-Host "[verify-replay] before: $($beforeHisto -join ', ')"

Write-Host "[verify-replay] restarting pixel-agents-py..."
docker restart pixel-agents-py | Out-Null
Start-Sleep -Seconds 4

# Wait for /api/health
for ($i = 0; $i -lt 20; $i++) {
    try {
        $h = (Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2).Content
        if ($h -match '"status":"ok"') { break }
    } catch { Start-Sleep -Seconds 1 }
}

$afterBody = Get-Replay -Provider $best.providerId -Sid $best.sessionId
$afterHisto = $afterBody.messages | Group-Object type | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Count)" }
Write-Host "[verify-replay] after:  $($afterHisto -join ', ')"

if (($beforeHisto -join ',') -eq ($afterHisto -join ',') -and $afterBody.envelopeCount -ge $bestCount) {
    Write-Host "[verify-replay] OK - replay stable across restart." -ForegroundColor Green
    exit 0
}

Write-Host "[verify-replay] FAIL - replay differs across restart." -ForegroundColor Red
exit 1
