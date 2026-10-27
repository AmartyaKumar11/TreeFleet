# Opens a second window that prints git worktrees and C:\wt once a second.
param(
    [string]$Repo = "D:\pr-sentinel-demo",
    [switch]$Loop
)

if (-not $Loop) {
    Start-Process powershell -ArgumentList @(
        "-NoExit",
        "-File",
        $PSCommandPath,
        "-Repo",
        $Repo,
        "-Loop"
    )
    exit 0
}

$Host.UI.RawUI.WindowTitle = "TreeFleet worktrees"
while ($true) {
    Clear-Host
    Write-Host (Get-Date -Format "HH:mm:ss")
    git -C $Repo worktree list
    Write-Host ""
    if (Test-Path "C:\wt") {
        Get-ChildItem "C:\wt" -Force | Format-Table Mode, LastWriteTime, Name -AutoSize
    } else {
        Write-Host "C:\wt does not exist"
    }
    Start-Sleep -Seconds 1
}
