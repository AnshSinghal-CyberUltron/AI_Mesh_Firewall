# Incident Queue & Forensics — API + Playwright UI sync (Steps 1–4).
#
# Usage (local docker):
#   .\scripts\verify_m26_incidents.ps1
#
# Optional:
#   .\scripts\verify_m26_incidents.ps1 -SkipPlaywright
#   .\scripts\verify_m26_incidents.ps1 -Email "admin@zeroshield.io" -Password "YOUR_PASSWORD"

param(
  [string]$BaseUrl = "http://127.0.0.1:8100",
  [string]$FrontendUrl = "http://127.0.0.1:8180",
  [string]$Email = "admin@zeroshield.io",
  [string]$Password = $env:TEST_PASSWORD,
  [string]$OrgSlug = "zeroshield",
  [string]$ControlContainer = "ai_mesh_firewall-control-1",
  [string]$DockerNetwork = "ai_mesh_firewall_default",
  [string]$PlaywrightImage = "mcr.microsoft.com/playwright:v1.60.0-jammy",
  [switch]$SkipPlaywright
)

$ErrorActionPreference = "Stop"

if (-not $Password) {
  $Password = "Adm1n!Pass#2024"
}

$script:pass = 0
$script:fail = 0
$stamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$probeTitle = "Incidents E2E probe $stamp"
$alertRuleName = "Incidents alert E2E $stamp"

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

function Seed-OpenIncident {
  param([string]$Title)
  $py = @"
from auth.models import Organization
from policy.models import EnforcementEvent, SecurityIncident
from policy.constants import ACTION_BLOCK
title='$Title'
org=Organization.objects.filter(slug='$OrgSlug').first()
assert org
ev=EnforcementEvent.objects.create(organization=org, action=ACTION_BLOCK, metadata={'source':'threat_intel','threat_type':'prompt_injection','detail':title,'model':'gpt-4o','key_prefix':'zs_incidents'})
inc=SecurityIncident.objects.create(organization=org, enforcement_event=ev, title=title, severity='high', status='open')
print(inc.id)
"@
  $out = docker exec -w /app/control $ControlContainer python manage.py shell -c $py
  $id = ($out -split "`n" | Where-Object { $_ -match '^\d+$' } | Select-Object -Last 1).Trim()
  if (-not $id) { throw "Failed to seed incident for Playwright: $out" }
  return [int]$id
}

Write-Host "=== Incident Queue E2E (stamp=$stamp) ===" -ForegroundColor Cyan

$env:BASE_URL = $BaseUrl
$env:TEST_EMAIL = $Email
$env:TEST_PASSWORD = $Password
$env:ORG_SLUG = $OrgSlug
$env:CONTROL_CONTAINER = $ControlContainer
$env:M26_PROBE_STAMP = "$stamp"
$env:M26_PROBE_TITLE = $probeTitle
$env:M26_ALERT_RULE_NAME = $alertRuleName

python scripts/verify_m26_incidents.py
$apiExit = $LASTEXITCODE
Write-Check "API verify_m26_incidents.py" ($apiExit -eq 0) "exit=$apiExit"
if ($apiExit -ne 0) {
  Write-Host "`n=== Incidents Result: $script:pass PASS / $script:fail FAIL (API failed; skipping Playwright) ===" -ForegroundColor Yellow
  exit 1
}

if ($SkipPlaywright) {
  Write-Host "`n=== Incidents Result: $script:pass PASS / $script:fail FAIL (Playwright skipped) ===" -ForegroundColor Cyan
  exit 0
}

Write-Host "`n=== Playwright UI sync gate ===" -ForegroundColor Cyan
$uiStamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$uiTitle = "Incidents E2E probe $uiStamp"
$incidentId = Seed-OpenIncident -Title $uiTitle
Write-Host "Seeded UI probe incident id=$incidentId title=$uiTitle"

$repoRoot = (Get-Location).Path
docker run --rm --network $DockerNetwork -v "${repoRoot}:/work" -w /work `
  -e NODE_PATH=/work/tests/e2e/node_modules `
  -e BASE_URL=http://frontend:5173 `
  -e TEST_EMAIL=$Email `
  -e TEST_PASSWORD=$Password `
  -e M26_INCIDENT_ID=$incidentId `
  -e M26_PROBE_TITLE=$uiTitle `
  -e M26_PROBE_STAMP=$uiStamp `
  $PlaywrightImage `
  bash -lc "cd tests/e2e && npm install --omit=dev 2>/dev/null && node /work/scripts/playwright_m26_incidents_sync.mjs"

$pwExit = $LASTEXITCODE
Write-Check "Playwright playwright_m26_incidents_sync.mjs" ($pwExit -eq 0) "exit=$pwExit"

Write-Host "`n=== Incidents Result: $script:pass PASS / $script:fail FAIL ===" -ForegroundColor Cyan
if ($script:fail -gt 0) { exit 1 }
exit 0
