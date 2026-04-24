# Send a few synthetic events to a running pixel-agents-python instance.
# Usage:
#   .\scripts\send-sample-event.ps1
#   .\scripts\send-sample-event.ps1 -BaseUrl http://localhost:8765 -Token dev-token-change-me

param(
  [string]$BaseUrl = "http://localhost:8765",
  [string]$Token = $env:PIXEL_AGENTS_TOKEN,
  [string]$ProviderId = "copilot",
  [string]$SessionId = ("demo-" + [Guid]::NewGuid().ToString().Substring(0,8))
)

if (-not $Token) { $Token = "dev-token-change-me" }

$headers = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json" }
$base = "$BaseUrl/api/hooks/$ProviderId"

function Send-Event($payload) {
  $body = $payload | ConvertTo-Json -Depth 6
  Invoke-RestMethod -Method Post -Uri $base -Headers $headers -Body $body | Out-Null
  Write-Host "sent: $($payload.event.kind)"
}

Send-Event @{ session_id = $SessionId; event = @{ kind = "sessionStart"; source = "copilot" } }
Start-Sleep -Milliseconds 250
Send-Event @{ session_id = $SessionId; event = @{ kind = "toolStart"; tool_id = "t1"; tool_name = "Read" } }
Start-Sleep -Milliseconds 500
Send-Event @{ session_id = $SessionId; event = @{ kind = "toolEnd"; tool_id = "t1" } }
Start-Sleep -Milliseconds 250
Send-Event @{ session_id = $SessionId; event = @{ kind = "tokenUsage"; input_tokens = 1200; output_tokens = 340 } }
Send-Event @{ session_id = $SessionId; event = @{ kind = "turnEnd" } }

Write-Host ""
Write-Host "Open: $BaseUrl/viewer/?token=$Token"
