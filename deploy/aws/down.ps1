<#
.SYNOPSIS
  Stop the TreeFleet EC2 instance (preserves EBS; does not terminate).
#>
[CmdletBinding()]
param(
  [string]$InstanceId = $env:INSTANCE_ID,
  [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { $env:AWS_DEFAULT_REGION }),
  [string]$Profile = $env:AWS_PROFILE
)

$ErrorActionPreference = "Stop"
if (-not $InstanceId) { throw "INSTANCE_ID is required (param or env)" }
if (-not $Region) { throw "AWS_REGION (or AWS_DEFAULT_REGION) is required" }

$aws = @("ec2", "stop-instances", "--instance-ids", $InstanceId, "--region", $Region)
if ($Profile) { $aws = @("--profile", $Profile) + $aws }
& aws @aws | Out-Null

$wait = @("ec2", "wait", "instance-stopped", "--instance-ids", $InstanceId, "--region", $Region)
if ($Profile) { $wait = @("--profile", $Profile) + $wait }
& aws @wait
Write-Host "Instance $InstanceId is stopped."
