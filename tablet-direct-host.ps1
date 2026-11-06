﻿#Requires -RunAsAdministrator
<#
  所有 USB 裝置一律只連 Windows 主機，永不連到 VMware VM。

  用法（系統管理員 PowerShell）：
    .\tablet-direct-host.ps1            # 套用
    .\tablet-direct-host.ps1 -Restore   # 還原（VM 恢復可用 USB）
#>
param(
    [string[]]$VmxFiles = @(
        'C:\VMWare\Windows 11 x64 IOT.vmx',
        'C:\VMWare2\Windows 11 x64 IOT2.vmx'
    ),
    [switch]$Restore
)

$svc      = 'VMUSBArbService'          # VMware USB Arbitration Service（攔截 USB 的元兇）
$taskName = 'Block-VMware-USB'
$utf8     = New-Object System.Text.UTF8Encoding($false)

if ($Restore) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Set-Service $svc -StartupType Automatic
    Start-Service $svc
    foreach ($vmx in $VmxFiles) {
        if (Test-Path -LiteralPath "$vmx.bak") { Copy-Item -LiteralPath "$vmx.bak" $vmx -Force; Write-Host "已還原 $vmx" }
    }
    Write-Host '已還原：VM 可再使用 USB。' -ForegroundColor Yellow
    return
}

# 1. 停用 VMware USB 仲裁服務：沒有它，任何 VM 都搶不到 USB
if (Get-Service $svc -ErrorAction SilentlyContinue) {
    Stop-Service $svc -Force -ErrorAction SilentlyContinue
    Set-Service $svc -StartupType Disabled
    Write-Host "[1] 已停止並停用 $svc"
}

# 2. 開機自動再停用一次（防止 VMware 更新/修復把服務改回自動）
$cmd = "Stop-Service $svc -Force -EA 0; Set-Service $svc -StartupType Disabled -EA 0"
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -Command `"$cmd`""
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Write-Host "[2] 已建立開機排程 $taskName"

# 3. VM 設定：移除 USB 控制器（VM 內完全沒有 USB）
#    第一次執行時備份原檔為 .bak；VM 執行中修改可能被覆寫，請在 VM 關機後再跑一次
$keys = 'usb\.present|ehci\.present|usb_xhci\.present|usb\.generic\.autoConnect|pref\.usb\.action'
foreach ($vmx in $VmxFiles) {
    if (-not (Test-Path -LiteralPath $vmx)) { Write-Host "[3] 找不到 $vmx，略過"; continue }
    if (-not (Test-Path -LiteralPath "$vmx.bak")) { Copy-Item -LiteralPath $vmx "$vmx.bak" }
    $lines = @([IO.File]::ReadAllLines($vmx, $utf8) | Where-Object { $_ -notmatch "^\s*($keys)\s*=" })
    $lines += 'usb.present = "FALSE"', 'ehci.present = "FALSE"', 'usb_xhci.present = "FALSE"',
              'usb.generic.autoConnect = "FALSE"'
    [IO.File]::WriteAllLines($vmx, $lines, $utf8)
    Write-Host "[3] 已移除 USB 控制器：$vmx"
}

# 4. 移除 VMware 虛擬 USB 殘留裝置 (VID_0E0F&PID_0001)，重新掃描硬體
Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\VID_0E0F&PID_0001*' } |
    ForEach-Object {
        pnputil /remove-device "$($_.InstanceId)" | Out-Null
        Write-Host "[4] 已移除 $($_.InstanceId)"
    }
pnputil /scan-devices | Out-Null

# 5. 重啟 ADB
Start-Sleep -Seconds 3
if (Get-Command adb -ErrorAction SilentlyContinue) {
    adb kill-server | Out-Null
    adb devices
} else {
    Write-Host '[5] 找不到 adb，請自行執行 adb devices'
}
Write-Host '若仍未出現：拔掉 USB 線再插回。' -ForegroundColor Cyan
