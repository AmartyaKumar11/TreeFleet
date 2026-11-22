<#
.SYNOPSIS
  Start the TreeFleet EC2 instance and print the SSM tunnel command.
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

$aws = @("ec2", "start-instances", "--instance-ids", $InstanceId, "--region", $Region)
if ($Profile) { $aws = @("--profile", $Profile) + $aws }
& aws @aws | Out-Null

$wait = @("ec2", "wait", "instance-running", "--instance-ids", $InstanceId, "--region", $Region)
if ($Profile) { $wait = @("--profile", $Profile) + $wait }
& aws @wait

Write-Host "Waiting for SSM Online..."
$deadline = (Get-Date).AddMinutes(10)
do {
  $q = @("ssm", "describe-instance-information",
    "--filters", "Key=InstanceIds,Values=$InstanceId",
    "--query", "InstanceInformationList[0].PingStatus",
    "--output", "text", "--region", $Region)
  if ($Profile) { $q = @("--profile", $Profile) + $q }
  $status = (& aws @q).Trim()
  if ($status -eq "Online") { break }
  Start-Sleep -Seconds 5
} while ((Get-Date) -lt $deadline)

if ($status -ne "Online") { throw "SSM PingStatus is '$status' (wanted Online)" }

Write-Host "Instance $InstanceId is running and SSM Online."
Write-Host "Open the UI tunnel:"
$tunnel = "aws ssm start-session --target $InstanceId --region $Region --document-name AWS-StartPortForwardingSession --parameters `"{\`"portNumber\`":[\`"8765\`"],\`"localPortNumber\`":[\`"8765\`"]}`""
if ($Profile) { $tunnel = "aws --profile $Profile " + ($tunnel.Substring(4)) }
Write-Host $tunnel
Write-Host "Then browse: http://127.0.0.1:8765"
Write-Host "(Or run .\tunnel.ps1)"
