<#
.SYNOPSIS
  SSM port-forward local 8765 -> instance 127.0.0.1:8765
#>
[CmdletBinding()]
param(
  [string]$InstanceId = $env:INSTANCE_ID,
  [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { $env:AWS_DEFAULT_REGION }),
  [string]$Profile = $env:AWS_PROFILE,
  [int]$LocalPort = 8765
)

$ErrorActionPreference = "Stop"
if (-not $InstanceId) { throw "INSTANCE_ID is required (param or env)" }
if (-not $Region) { throw "AWS_REGION (or AWS_DEFAULT_REGION) is required" }

$params = "{`"portNumber`":[`"8765`"],`"localPortNumber`":[`"$LocalPort`"]}"
$aws = @(
  "ssm", "start-session",
  "--target", $InstanceId,
  "--region", $Region,
  "--document-name", "AWS-StartPortForwardingSession",
  "--parameters", $params
)
if ($Profile) { $aws = @("--profile", $Profile) + $aws }
Write-Host "Forwarding localhost:${LocalPort} -> ${InstanceId}:8765 (Ctrl+C to stop)"
& aws @aws
