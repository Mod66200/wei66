# Seeker × HAPPY GO Playio 每日任務（ADB）

在 Windows 電腦上用 ADB 控制 Seeker 自動跑 Playio 任務。執行時不呼叫 AI，**不耗 token**。

## 需要
- Python 3.8+、[Android platform-tools](https://developer.android.com/tools/releases/platform-tools)（`adb` 要在 PATH，或用 `--adb` 指定路徑）
- Seeker 開啟 USB 偵錯並允許這台電腦；建議關閉螢幕鎖，或設定充電時螢幕不關：`adb shell svc power stayon usb`

## 使用步驟
1. **偵察**：手機手動開到 Playio 任務頁，雙擊 `probe.bat`。
   會列出套件名稱、畫面上所有文字（★ 表示可點）與座標，並存截圖到 `probe/`。
2. **改設定**：依偵察結果修改 `tasks.json`
   - `package` 填 HAPPY GO 套件名稱（留空則自動找）
   - 每個任務的 `text` 改成畫面上的實際按鈕文字
   - 遊戲畫面沒有文字時，用截圖量座標，改寫成 `tap_xy`
   - 確認無誤的任務把 `enabled` 改成 `true`
3. **執行**：雙擊 `run.bat`；只跑單一任務：`run.bat --task 簽到`
4. **每天自動跑**（可選）：
   ```bat
   schtasks /create /tn "PlayioDaily" /tr "\"%CD%\run.bat\"" /sc daily /st 09:10
   ```

## 步驟類型
| do | 參數 | 說明 |
|---|---|---|
| `launch` | `package`（可省略） | 開啟 App |
| `wait` | `sec` | 等待秒數 |
| `tap_text` | `text` / `regex` / `id`、`timeout`、`optional` | 找到文字就點 |
| `wait_text` | 同上 | 只等待文字出現 |
| `tap_xy` | `x`、`y` | 點座標 |
| `swipe` | `x1 y1 x2 y2 ms` | 滑動 |
| `back` | `times` | 返回鍵 |
| `home` | — | 回桌面 |
| `screenshot` | — | 截圖到 `logs/` |

任務可設 `repeat`（重複次數）、`enabled`、`on_fail_back`（失敗時按幾次返回）。

## 安全機制
- 要點的按鈕文字含 `block_words`（兌換、付款、扣點、綁卡、簽署、授權…）時會**立即停止全部任務**，交給你手動處理。
- 失敗會截圖存到 `logs/fail_*.png`，每天的紀錄在 `logs/YYYYMMDD.log`。

> 注意：HAPPY GO 條款可能禁止用自動化工具取得點數，使用風險（停權、點數取消）請自行評估。
