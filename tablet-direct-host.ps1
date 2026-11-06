#Requires -RunAsAdministrator
<#
  讓平板 (Spreadtrum/Unisoc, VID 0x1782) 直接連 Windows 主機 ADB，不經過 VMware。

  用法（系統管理員 PowerShell）：
    .\tablet-direct-host.ps1            # 套用：平板只走主機
    .\tablet-direct-host.ps1 -Restore   # 還原：重新啟用 VMware USB 仲裁服務
#>
param(
    [string[]]$VmxFiles = @(
        'C:\VMWare\Windows 11 x64 IOT.vmx',
        'C:\VMWare2\Windows 11 x64 IOT2.vmx'
    ),
    [switch]$Restore
)

$svc = 'VMUSBArbService'   # VMware USB Arbitration Service（攔截 USB 的元兇）

if ($Restore) {
    Set-Service $svc -StartupType Automatic
    Start-Service $svc
    Write-Host "已還原 $svc。" -ForegroundColor Yellow
    return
}

# 1. 停用 VMware USB 仲裁服務：沒有它，VM 就無法搶走任何 USB 裝置
if (Get-Service $svc -ErrorAction SilentlyContinue) {
    Stop-Service $svc -Force -ErrorAction SilentlyContinue
    Set-Service $svc -StartupType Disabled
    Write-Host "[1] 已停止並停用 $svc"
}

# 2. VM 設定：關閉「自動連接新 USB 裝置」，並移除無效的 pref.usb.action
#    （VM 執行中修改可能在關機時被覆寫，建議 VM 關機後再跑一次）
$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($vmx in $VmxFiles) {
    if (-not (Test-Path -LiteralPath $vmx)) { Write-Host "[2] 找不到 $vmx，略過"; continue }
    Copy-Item -LiteralPath $vmx "$vmx.bak" -Force
    $lines = [IO.File]::ReadAllLines($vmx, $utf8) |
        Where-Object { $_ -notmatch '^\s*(usb\.generic\.autoConnect|pref\.usb\.action)\s*=' }
    $lines += 'usb.generic.autoConnect = "FALSE"'
    [IO.File]::WriteAllLines($vmx, $lines, $utf8)
    Write-Host "[2] 已更新 $vmx（備份：$vmx.bak）"
}

# 3. 移除卡在 Error 28 的 VMware 虛擬 USB 裝置 (VID_0E0F&PID_0001)，重新掃描硬體
Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\VID_0E0F&PID_0001*' } |
    ForEach-Object {
        pnputil /remove-device "$($_.InstanceId)" | Out-Null
        Write-Host "[3] 已移除 $($_.InstanceId)"
    }
pnputil /scan-devices | Out-Null

# 4. 重啟 ADB
Start-Sleep -Seconds 3
if (Get-Command adb -ErrorAction SilentlyContinue) {
    adb kill-server | Out-Null
    adb devices
} else {
    Write-Host '[4] 找不到 adb，請自行執行 adb devices'
}
Write-Host '若仍未出現：拔掉平板 USB 線再插回。' -ForegroundColor Cyan
