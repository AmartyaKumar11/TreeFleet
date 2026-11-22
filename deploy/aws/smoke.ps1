<#
.SYNOPSIS
  Run on-box smoke.sh via SSM send-command and print the result (no secrets).
#>
[CmdletBinding()]
param(
  [string]$InstanceId = $env:INSTANCE_ID,
  [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { $env:AWS_DEFAULT_REGION }),
  [string]$Profile = $env:AWS_PROFILE,
  [switch]$Tests
)

$ErrorActionPreference = "Stop"
if (-not $InstanceId) { throw "INSTANCE_ID is required (param or env)" }
if (-not $Region) { throw "AWS_REGION (or AWS_DEFAULT_REGION) is required" }

$cmd = "export PATH=/home/treefleet/.local/bin:/usr/local/bin:`$PATH; /usr/local/bin/treefleet-smoke"
if ($Tests) {
  $cmd = "export PATH=/home/treefleet/.local/bin:/usr/local/bin:`$PATH; /usr/local/bin/treefleet-smoke --tests"
}

$send = @(
  "ssm", "send-command",
  "--instance-ids", $InstanceId,
  "--document-name", "AWS-RunShellScript",
  "--parameters", "commands=[`"$cmd`"]",
  "--query", "Command.CommandId",
  "--output", "text",
  "--region", $Region
)
if ($Profile) { $send = @("--profile", $Profile) + $send }
$commandId = (& aws @send).Trim()
Write-Host "CommandId=$commandId"

$deadline = (Get-Date).AddMinutes(30)
do {
  Start-Sleep -Seconds 5
  $inv = @(
    "ssm", "get-command-invocation",
    "--command-id", $commandId,
    "--instance-id", $InstanceId,
    "--query", "[Status,StandardOutputContent,StandardErrorContent]",
    "--output", "json",
    "--region", $Region
  )
  if ($Profile) { $inv = @("--profile", $Profile) + $inv }
  $raw = & aws @inv
  $obj = $raw | ConvertFrom-Json
  $status = $obj[0]
  if ($status -in @("Success", "Cancelled", "TimedOut", "Failed")) { break }
} while ((Get-Date) -lt $deadline)

Write-Host "Status=$status"
if ($obj[1]) { Write-Host $obj[1] }
if ($obj[2]) { Write-Host $obj[2] }
if ($status -ne "Success") { exit 1 }
