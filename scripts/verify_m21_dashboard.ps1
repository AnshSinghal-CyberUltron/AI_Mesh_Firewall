# M2.1 SOC Command Center — API + M1 alignment + optional SQL sanity (Steps 1–3).
#
# Usage (local docker):
#   .\scripts\verify_m21_dashboard.ps1 `
#     -Email "sandbox@zeroshield.io" `
#     -Password "YOUR_PASSWORD"
#
# Usage (staging/prod):
#   .\scripts\verify_m21_dashboard.ps1 `
#     -BaseUrl "https://api.example.com" `
#     -GatewayUrl "https://gateway.example.com" `
#     -Email "sandbox@zeroshield.io" `
#     -Password "YOUR_PASSWORD" `
#     -OrgSlug "sandbox"
#
# Optional: skip gateway inject or Postgres cross-check
#   -SkipInject -SkipSql
#
# Password can also be supplied via env: $env:M21_PASSWORD

param(
  [string]$BaseUrl = "http://127.0.0.1:8100",
  [string]$GatewayUrl = "http://127.0.0.1:8300",
  [string]$Email = "sandbox@zeroshield.io",
  [string]$Password = $env:M21_PASSWORD,
  [string]$Period = "24h",
  [string]$OrgSlug = "sandbox",
  [int]$DrainSeconds = 5,
  [switch]$SkipInject,
  [switch]$SkipSql,
  [switch]$UseDockerPostgres,
  [string]$ComposeFile = "",
  [string]$PostgresUser = "ai_mesh_firewall",
  [string]$PostgresDb = "ai_mesh_firewall"
)

$ErrorActionPreference = "Stop"

$script:pass = 0
$script:fail = 0
$LANES = @("chat", "rag", "vector", "mcp", "threat_intel")

function Write-Check {
  param([string]$Name, [bool]$Ok, [string]$Detail = "")
  if ($Ok) {
    Write-Host "PASS $Name" -ForegroundColor Green
    $script:pass++
  } else {
    $suffix = if ($Detail) { " - $Detail" } else { "" }
    Write-Host "FAIL $Name$suffix" -ForegroundColor Red
    $script:fail++
  }
}

function Get-AuthHeaders {
  param([string]$Base, [string]$UserEmail, [string]$UserPassword)
  if (-not $UserPassword) {
    throw "Password required: pass -Password or set env M21_PASSWORD"
  }
  $login = @{ email = $UserEmail; password = $UserPassword } | ConvertTo-Json
  $tok = Invoke-RestMethod -Method Post -Uri "$Base/api/auth/token/" `
    -ContentType "application/json" -Body $login -TimeoutSec 45
  if (-not $tok.access) { throw "Auth response missing access token" }
  return @{ Authorization = "Bearer $($tok.access)" }
}

function Get-DashboardBundle {
  param([string]$Base, [hashtable]$Headers, [string]$Window)
  $dash = Invoke-RestMethod -Uri "$Base/api/module2/dashboard/?period=$Window" -Headers $Headers -TimeoutSec 45
  $soc = Invoke-RestMethod -Uri "$Base/api/security/soc-kpis/?period=$Window" -Headers $Headers -TimeoutSec 45
  return @{ Dashboard = $dash; Soc = $soc }
}

function Measure-Dashboard {
  param($Dash, $Soc)
  if ($null -ne $Dash.kpis.total_events) { $gw = [int]$Dash.kpis.total_events }
  elseif ($null -ne $Dash.kpis.requests_inspected) { $gw = [int]$Dash.kpis.requests_inspected }
  else { $gw = 0 }
  if ($null -ne $Dash.kpis.blocked) { $blocked = [int]$Dash.kpis.blocked } else { $blocked = 0 }
  if ($null -ne $Dash.kpis.monitored) { $monitored = [int]$Dash.kpis.monitored } else { $monitored = 0 }
  if ($null -ne $Dash.kpis.rerouted) { $rerouted = [int]$Dash.kpis.rerouted } else { $rerouted = 0 }
  if ($null -ne $Soc.requests_inspected) { $m1 = [int]$Soc.requests_inspected } else { $m1 = 0 }

  $laneTotals = @{}
  $laneSum = 0
  foreach ($lane in $LANES) {
    if ($null -ne $Dash.lane_summary.$lane.total) { $n = [int]$Dash.lane_summary.$lane.total } else { $n = 0 }
    $laneTotals[$lane] = $n
    $laneSum += $n
  }

  $extraLaneKeys = @()
  if ($Dash.lane_summary) {
    foreach ($k in $Dash.lane_summary.PSObject.Properties.Name) {
      if ($k -notin $LANES) { $extraLaneKeys += $k }
    }
  }

  if ($gw -gt 0) { $expectedBlockRate = [math]::Round(($blocked / $gw) * 100, 1) } else { $expectedBlockRate = 0.0 }
  if ($null -ne $Dash.kpis.block_rate) { $actualBlockRate = [double]$Dash.kpis.block_rate } else { $actualBlockRate = 0.0 }

  return [pscustomobject]@{
    GatewayRequests   = $gw
    M1Requests        = $m1
    LaneSum           = $laneSum
    LaneTotals        = $laneTotals
    Blocked           = $blocked
    Monitored         = $monitored
    Rerouted          = $rerouted
    BlockRate         = $actualBlockRate
    ExpectedBlockRate = $expectedBlockRate
    ExtraLaneKeys     = $extraLaneKeys
  }
}

function Test-DashboardInvariants {
  param($Metrics, [string]$Label = "snapshot")
  Write-Host ""
  Write-Host "=== Step 1: Dashboard invariants ($Label) ===" -ForegroundColor Cyan
  Write-Host ("Gateway Requests: {0}" -f $Metrics.GatewayRequests)
  Write-Host ("Lane sum:         {0} (chat={1} rag={2} vector={3} mcp={4} threat_intel={5})" -f `
    $Metrics.LaneSum, `
    $Metrics.LaneTotals.chat, $Metrics.LaneTotals.rag, $Metrics.LaneTotals.vector, `
    $Metrics.LaneTotals.mcp, $Metrics.LaneTotals.threat_intel)
  Write-Host ("M1 requests_inspected: {0}" -f $Metrics.M1Requests)
  Write-Host ("Monitored: {0}  Rerouted: {1}  Blocked: {2}  Block rate: {3}%" -f `
    $Metrics.Monitored, $Metrics.Rerouted, $Metrics.Blocked, $Metrics.BlockRate)

  Write-Check "lane_sum_equals_gateway_requests" ($Metrics.LaneSum -eq $Metrics.GatewayRequests) `
    "lane_sum=$($Metrics.LaneSum) gateway=$($Metrics.GatewayRequests)"
  Write-Check "m1_equals_m2_gateway_requests" ($Metrics.M1Requests -eq $Metrics.GatewayRequests) `
    "m1=$($Metrics.M1Requests) m2=$($Metrics.GatewayRequests)"
  Write-Check "no_extra_lane_keys" ($Metrics.ExtraLaneKeys.Count -eq 0) `
    ("extra: " + ($Metrics.ExtraLaneKeys -join ", "))
  Write-Check "monitored_is_non_negative" ($Metrics.Monitored -ge 0)
  Write-Check "rerouted_is_non_negative" ($Metrics.Rerouted -ge 0)
  Write-Check "monitored_not_in_lane_sum" ($Metrics.Monitored -le $Metrics.GatewayRequests)
  Write-Check "rerouted_not_in_lane_sum" ($Metrics.Rerouted -le $Metrics.GatewayRequests)
  if ($Metrics.GatewayRequests -gt 0) {
    Write-Check "block_rate_math" ([math]::Abs($Metrics.BlockRate - $Metrics.ExpectedBlockRate) -lt 0.05) `
      "api=$($Metrics.BlockRate) expected=$($Metrics.ExpectedBlockRate)"
  } else {
    Write-Check "block_rate_zero_when_no_traffic" ($Metrics.BlockRate -eq 0)
  }
}

function Invoke-SqlCollapsedCounts {
  param([int]$OrgId)
  @"
SELECT
  COUNT(*) FILTER (WHERE final_action = 'block') AS blocked_requests,
  COUNT(*) AS total_requests
FROM (
  SELECT
    COALESCE(NULLIF(TRIM(metadata->>'request_id'), ''), 'row:' || id::text) AS req_key,
    CASE
      WHEN bool_or(action = 'block') THEN 'block'
      WHEN bool_or(action = 'redact') THEN 'redact'
      ELSE 'allow'
    END AS final_action
  FROM policy_enforcementevent
  WHERE organization_id = $OrgId
    AND created_at >= NOW() - INTERVAL '24 hours'
  GROUP BY 1
) collapsed;
"@
}

function Run-PostgresSanity {
  param([string]$Slug, [object]$ApiMetrics)
  Write-Host ""
  Write-Host "=== Step 2: Postgres collapsed counts (24h) ===" -ForegroundColor Cyan

  $repoRoot = Split-Path $PSScriptRoot -Parent
  if (-not $ComposeFile) {
    $ComposeFile = Join-Path $repoRoot "docker-compose.yml"
  }
  if (-not (Test-Path $ComposeFile)) {
    Write-Check "compose_file_found" $false $ComposeFile
    return
  }

  $orgQuery = "SELECT id, slug FROM auth_api_organization WHERE slug = '$Slug' LIMIT 1;"
  try {
    $orgRaw = docker compose -f $ComposeFile exec -T postgres `
      psql -U $PostgresUser -d $PostgresDb -t -A -c $orgQuery 2>&1
    if ($LASTEXITCODE -ne 0) { throw $orgRaw }
    $orgLine = ($orgRaw | Where-Object { $_ -match '\S' } | Select-Object -First 1)
    if (-not $orgLine) {
      Write-Check "org_slug_resolved" $false "slug=$Slug not found"
      return
    }
    $orgId = [int]($orgLine -split '\|')[0]
    Write-Host "Org id for slug '$Slug': $orgId"
    Write-Check "org_slug_resolved" $true

    $sql = Invoke-SqlCollapsedCounts -OrgId $orgId
    $sqlRaw = docker compose -f $ComposeFile exec -T postgres `
      psql -U $PostgresUser -d $PostgresDb -t -A -c $sql 2>&1
    if ($LASTEXITCODE -ne 0) { throw $sqlRaw }
    $line = ($sqlRaw | Where-Object { $_ -match '\S' } | Select-Object -First 1)
    $parts = $line -split '\|'
    $dbBlocked = [int]$parts[0]
    $dbTotal = [int]$parts[1]
    Write-Host "SQL collapsed total: $dbTotal  blocked: $dbBlocked"
    Write-Host "API gateway requests: $($ApiMetrics.GatewayRequests)  blocked: $($ApiMetrics.Blocked)"

    Write-Check "sql_total_matches_api" ($dbTotal -eq $ApiMetrics.GatewayRequests) `
      "sql=$dbTotal api=$($ApiMetrics.GatewayRequests)"
    Write-Check "sql_blocked_matches_api" ($dbBlocked -eq $ApiMetrics.Blocked) `
      "sql=$dbBlocked api=$($ApiMetrics.Blocked)"
  } catch {
    Write-Check "postgres_sanity" $false $_.Exception.Message
  }
}

function Invoke-ChatProbe {
  param([string]$Base, [string]$Gw, [hashtable]$Headers)
  $sim = Invoke-RestMethod -Method Post -Uri "$Base/api/gateways/simulator-default/" -Headers $Headers -TimeoutSec 45
  if (-not $sim.key) { throw "simulator-default did not return key" }
  $body = @{
    model      = "auto"
    messages   = @(@{ role = "user"; content = "Hello - benign M2.1 KPI probe." })
    max_tokens = 16
  } | ConvertTo-Json -Depth 5
  $gwHeaders = @{
    "Content-Type"  = "application/json"
    Authorization   = "Bearer $($sim.key)"
  }
  try {
    $resp = Invoke-WebRequest -Method Post -Uri "$Gw/v1/chat/completions" `
      -Headers $gwHeaders -Body $body -UseBasicParsing -TimeoutSec 90
    return $resp.StatusCode
  } catch {
    if ($_.Exception.Response) {
      return [int]$_.Exception.Response.StatusCode
    }
    throw
  }
}

# ── Main ──────────────────────────────────────────────────────────────────────

Write-Host "M2.1 dashboard verification" -ForegroundColor Cyan
Write-Host "Base: $BaseUrl  Gateway: $GatewayUrl  Period: $Period  Org: $OrgSlug"

try {
  $headers = Get-AuthHeaders -Base $BaseUrl -UserEmail $Email -UserPassword $Password
  Write-Check "auth_token" $true
} catch {
  Write-Check "auth_token" $false $_.Exception.Message
  Write-Host "--- passed=$pass failed=$fail"
  exit 1
}

$before = Get-DashboardBundle -Base $BaseUrl -Headers $headers -Window $Period
$beforeMetrics = Measure-Dashboard -Dash $before.Dashboard -Soc $before.Soc
Test-DashboardInvariants -Metrics $beforeMetrics -Label "baseline"

if ($UseDockerPostgres -and -not $SkipSql) {
  Run-PostgresSanity -Slug $OrgSlug -ApiMetrics $beforeMetrics
} elseif (-not $SkipSql) {
  Write-Host ""
  Write-Host "Skip SQL: pass -UseDockerPostgres to compare policy_enforcementevent vs API (local docker)." -ForegroundColor Yellow
}

if (-not $SkipInject) {
  Write-Host ""
  Write-Host "=== Step 3: Controlled +1 chat probe ===" -ForegroundColor Cyan
  try {
    $httpCode = Invoke-ChatProbe -Base $BaseUrl -Gw $GatewayUrl -Headers $headers
    Write-Host "Gateway chat HTTP: $httpCode"
    Write-Check "gateway_chat_reachable" ($httpCode -in 200, 400, 401, 403, 422)
    Write-Host "Waiting ${DrainSeconds}s for telemetry drain..."
    Start-Sleep -Seconds $DrainSeconds

    $after = Get-DashboardBundle -Base $BaseUrl -Headers $headers -Window $Period
    $afterMetrics = Measure-Dashboard -Dash $after.Dashboard -Soc $after.Soc

    $gwDelta = $afterMetrics.GatewayRequests - $beforeMetrics.GatewayRequests
    $m1Delta = $afterMetrics.M1Requests - $beforeMetrics.M1Requests
    $chatDelta = $afterMetrics.LaneTotals.chat - $beforeMetrics.LaneTotals.chat

    Write-Host "Delta gateway: +$gwDelta  M1: +$m1Delta  chat lane: +$chatDelta"
    Write-Check "gateway_requests_plus_one" ($gwDelta -eq 1) "delta=$gwDelta"
    Write-Check "m1_requests_plus_one" ($m1Delta -eq 1) "delta=$m1Delta"
    Write-Check "chat_lane_plus_one" ($chatDelta -eq 1) "delta=$chatDelta"
    Write-Check "other_lanes_unchanged" (
      ($afterMetrics.LaneTotals.rag -eq $beforeMetrics.LaneTotals.rag) -and
      ($afterMetrics.LaneTotals.vector -eq $beforeMetrics.LaneTotals.vector) -and
      ($afterMetrics.LaneTotals.mcp -eq $beforeMetrics.LaneTotals.mcp) -and
      ($afterMetrics.LaneTotals.threat_intel -eq $beforeMetrics.LaneTotals.threat_intel)
    )

    Test-DashboardInvariants -Metrics $afterMetrics -Label "after inject"
  } catch {
    Write-Check "controlled_plus_one" $false $_.Exception.Message
  }
} else {
  Write-Host ""
  Write-Host "Step 3 skipped (-SkipInject)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Summary ===" -ForegroundColor Cyan
Write-Host "passed=$pass failed=$fail"
Write-Host ""
Write-Host "Manual follow-ups (not automated here):"
Write-Host "  Step 4 - MCP / RAG / threat-intel lane probes via respective simulators"
Write-Host "  Step 5 - Browser WS ticker (Live badge + onEnforcementEvent)"

if ($fail -gt 0) { exit 1 }
exit 0
