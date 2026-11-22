<#
.SYNOPSIS
  Prompt for TreeFleet secrets and write SecureString parameters under a prefix.
  Blank input skips that key. Values are never echoed.
#>
[CmdletBinding()]
param(
  [string]$Prefix = $(if ($env:PARAMETER_PREFIX) { $env:PARAMETER_PREFIX } else { "/treefleet" }),
  [string]$Region = $(if ($env:AWS_REGION) { $env:AWS_REGION } else { $env:AWS_DEFAULT_REGION }),
  [string]$Profile = $env:AWS_PROFILE
)

$ErrorActionPreference = "Stop"
if (-not $Region) { throw "AWS_REGION (or AWS_DEFAULT_REGION) is required" }
$Prefix = $Prefix.TrimEnd("/")

# Derived from treefleet.toml / AGENTS.md / docs/aws-deploy-audit.md (env-only secrets).
$keys = @(
  "DEEPSEEK_API_KEY",
  "TYPESAFE_API_KEY",
  "CURSOR_API_KEY",
  "LANGFUSE_PUBLIC_KEY",
  "LANGFUSE_SECRET_KEY",
  "LANGFUSE_BASE_URL",
  "GH_TOKEN",
  "TREEFLEET_CURSOR_SANDBOX"
)

Write-Host "Writing SecureString parameters under $Prefix (blank = skip)"
foreach ($key in $keys) {
  $secure = Read-Host -AsSecureString -Prompt $key
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
  if ([string]::IsNullOrWhiteSpace($plain)) {
    Write-Host "skip $key"
    continue
  }
  $name = "$Prefix/$key"
  $aws = @(
    "ssm", "put-parameter",
    "--name", $name,
    "--type", "SecureString",
    "--value", $plain,
    "--overwrite",
    "--region", $Region
  )
  if ($Profile) { $aws = @("--profile", $Profile) + $aws }
  & aws @aws | Out-Null
  Write-Host "wrote $name"
  $plain = $null
}
Write-Host "done"
