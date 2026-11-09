#!/usr/bin/env python3
"""HAPPY GO Playio 每日任務：用 ADB 在 Seeker 上自動點擊。

只用 Python 標準函式庫，執行時不呼叫任何 AI，不耗 token。

指令：
  python playio_tasks.py probe            # 偵察：列出套件、目前畫面可點文字、存截圖
  python playio_tasks.py run              # 依 tasks.json 執行所有任務
  python playio_tasks.py run --task 簽到   # 只跑名稱含「簽到」的任務
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "logs"
PROBE_DIR = HERE / "probe"

# 點擊目標文字含這些字就立即停止，避免誤觸兌換、付款、錢包簽署等動作
DEFAULT_BLOCK_WORDS = [
    "兌換", "付款", "支付", "扣點", "綁定", "綁卡", "信用卡", "購買", "結帳",
    "儲值", "轉帳", "簽署", "授權", "Approve", "Sign", "Pay", "Buy",
]

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

_log_file = None


def log(msg):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + "\n")
        _log_file.flush()


class StepFailed(Exception):
    pass


class SafetyStop(Exception):
    pass


# ---------------------------------------------------------------- ADB
class Adb:
    def __init__(self, adb="adb", serial=None):
        self.base = [adb] + (["-s", serial] if serial else [])

    def run(self, *args, binary=False, check=True, timeout=60):
        p = subprocess.run(self.base + list(args), capture_output=True, timeout=timeout)
        if check and p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace").strip()
            raise StepFailed(f"adb {' '.join(args)} 失敗：{err}")
        return p.stdout if binary else p.stdout.decode("utf-8", "replace")

    def shell(self, *args, **kw):
        return self.run("shell", *args, **kw)

    def ensure_device(self):
        out = self.run("devices")
        devices = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        if not devices:
            raise SystemExit("找不到裝置：請確認 Seeker 已接上、USB 偵錯已開啟並允許這台電腦。")
        return devices

    def tap(self, x, y):
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1, y1, x2, y2, ms=300):
        self.shell("input", "swipe", *map(str, (x1, y1, x2, y2, ms)))

    def key(self, code):
        self.shell("input", "keyevent", str(code))

    def launch(self, pkg):
        self.shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")

    def screenshot(self, path):
        Path(path).write_bytes(self.run("exec-out", "screencap", "-p", binary=True))

    def dump_xml(self):
        self.shell("uiautomator", "dump", "/sdcard/__ui.xml", check=False)
        return self.run("exec-out", "cat", "/sdcard/__ui.xml")

    def nodes(self):
        return parse_nodes(self.dump_xml())

    def packages(self):
        out = self.shell("pm", "list", "packages")
        return sorted(l.split(":", 1)[1].strip() for l in out.splitlines() if ":" in l)

    def focus(self):
        out = self.shell("dumpsys", "window", check=False)
        for line in out.splitlines():
            if "mCurrentFocus" in line or "mFocusedApp" in line:
                return line.strip()
        return "(未知)"


_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def parse_nodes(xml_text):
    """把 uiautomator dump 轉成 [{label, text, desc, rid, clickable, center}]。"""
    start = xml_text.find("<?xml")
    if start < 0:
        start = xml_text.find("<hierarchy")
    if start < 0:
        return []
    try:
        root = ET.fromstring(xml_text[start:].strip())
    except ET.ParseError:
        return []
    result = []
    for n in root.iter("node"):
        m = _BOUNDS.match(n.get("bounds", ""))
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        if x2 <= x1 or y2 <= y1:
            continue
        text = (n.get("text") or "").strip()
        desc = (n.get("content-desc") or "").strip()
        result.append({
            "label": text or desc,
            "text": text,
            "desc": desc,
            "rid": n.get("resource-id", ""),
            "clickable": n.get("clickable") == "true",
            "center": ((x1 + x2) // 2, (y1 + y2) // 2),
        })
    return result


def find_node(nodes, text=None, regex=None, rid=None):
    """先找完全相同，再找包含；同分時優先可點擊的元素。"""
    cands = []
    for n in nodes:
        if rid and n["rid"] != rid:
            continue
        labels = [n["text"], n["desc"]]
        if regex:
            if any(re.search(regex, l) for l in labels if l):
                cands.append((1, n))
        elif text:
            if text in labels:
                cands.append((0, n))
            elif any(text in l for l in labels if l):
                cands.append((1, n))
        elif rid:
            cands.append((0, n))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], not c[1]["clickable"]))
    return cands[0][1]


# ---------------------------------------------------------------- 執行
class Runner:
    def __init__(self, adb, cfg):
        self.adb = adb
        self.cfg = cfg
        self.pkg = cfg.get("package") or ""
        self.delay = float(cfg.get("step_delay", 1.5))
        self.block = cfg.get("block_words") or DEFAULT_BLOCK_WORDS

    def check_safe(self, label):
        for w in self.block:
            if w and w.lower() in (label or "").lower():
                raise SafetyStop(f"目標「{label}」含禁止字「{w}」，已停止，請手動處理。")

    def wait_node(self, step):
        timeout = float(step.get("timeout", 10))
        end = time.time() + timeout
        while True:
            node = find_node(self.adb.nodes(), step.get("text"), step.get("regex"), step.get("id"))
            if node or time.time() >= end:
                return node
            time.sleep(1)

    def do_step(self, step):
        kind = step["do"]
        desc = step.get("text") or step.get("regex") or step.get("id") or ""
        if kind == "launch":
            pkg = step.get("package") or self.pkg
            log(f"  開啟 {pkg}")
            self.adb.launch(pkg)
        elif kind == "wait":
            time.sleep(float(step.get("sec", 1)))
        elif kind in ("tap_text", "wait_text"):
            node = self.wait_node(step)
            if not node:
                if step.get("optional"):
                    log(f"  （略過，找不到「{desc}」）")
                    return
                raise StepFailed(f"{step.get('timeout', 10)} 秒內找不到「{desc}」")
            if kind == "tap_text":
                self.check_safe(node["label"])
                x, y = node["center"]
                log(f"  點「{node['label']}」({x},{y})")
                self.adb.tap(x, y)
            else:
                log(f"  看到「{node['label']}」")
        elif kind == "tap_xy":
            log(f"  點座標 ({step['x']},{step['y']})")
            self.adb.tap(step["x"], step["y"])
        elif kind == "swipe":
            self.adb.swipe(step["x1"], step["y1"], step["x2"], step["y2"], step.get("ms", 300))
        elif kind == "back":
            for _ in range(int(step.get("times", 1))):
                self.adb.key(4)
                time.sleep(0.8)
        elif kind == "home":
            self.adb.key(3)
        elif kind == "screenshot":
            LOG_DIR.mkdir(exist_ok=True)
            path = LOG_DIR / f"{dt.datetime.now():%Y%m%d_%H%M%S}.png"
            self.adb.screenshot(path)
            log(f"  截圖 {path.name}")
        else:
            raise StepFailed(f"不認得的步驟類型：{kind}")
        time.sleep(self.delay)

    def run_task(self, task):
        for i in range(int(task.get("repeat", 1))):
            suffix = f"（第 {i + 1} 次）" if task.get("repeat", 1) > 1 else ""
            log(f"▶ {task['name']}{suffix}")
            for step in task["steps"]:
                self.do_step(step)

    def run(self, only=None):
        ok, fail = [], []
        for task in self.cfg["tasks"]:
            if not task.get("enabled", True):
                continue
            if only and only not in task["name"]:
                continue
            try:
                self.run_task(task)
                ok.append(task["name"])
            except SafetyStop as e:
                log(f"⛔ {e}")
                fail.append(task["name"])
                break
            except (StepFailed, subprocess.TimeoutExpired) as e:
                log(f"✗ {task['name']} 失敗：{e}")
                fail.append(task["name"])
                try:
                    self.adb.screenshot(LOG_DIR / f"fail_{dt.datetime.now():%Y%m%d_%H%M%S}.png")
                    for _ in range(int(task.get("on_fail_back", 2))):
                        self.adb.key(4)
                        time.sleep(0.8)
                except Exception:
                    pass
        log(f"完成：成功 {len(ok)}、失敗 {len(fail)} {fail if fail else ''}")
        return 0 if not fail else 1


def wake(adb):
    adb.key(224)  # KEYCODE_WAKEUP
    time.sleep(0.5)
    out = adb.shell("dumpsys", "window", check=False)
    if "mDreamingLockscreen=true" in out or "isStatusBarKeyguard=true" in out:
        adb.swipe(540, 1800, 540, 600, 250)
        time.sleep(1)


def resolve_package(adb, cfg):
    if cfg.get("package"):
        return cfg["package"]
    hits = [p for p in adb.packages() if re.search(r"happy.?go|playio", p, re.I)]
    if len(hits) == 1:
        log(f"自動找到套件：{hits[0]}")
        return hits[0]
    raise SystemExit(f"請在 tasks.json 填 package。候選：{hits or '無，請先跑 probe'}")


# ---------------------------------------------------------------- 指令
def cmd_probe(adb, args):
    PROBE_DIR.mkdir(exist_ok=True)
    stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    print("裝置：", adb.ensure_device())
    print("螢幕：", adb.shell("wm", "size").strip())
    print("前景：", adb.focus())
    pk = [p for p in adb.packages() if re.search(r"happy|playio|hgo", p, re.I)]
    print("可能的 HAPPY GO / Playio 套件：", pk or "（沒找到，請用 adb shell pm list packages 自行查）")
    xml = adb.dump_xml()
    (PROBE_DIR / f"{stamp}.xml").write_text(xml, encoding="utf-8")
    adb.screenshot(PROBE_DIR / f"{stamp}.png")
    print("\n目前畫面上的文字（★ = 可點擊）：")
    for n in parse_nodes(xml):
        if n["label"]:
            star = "★" if n["clickable"] else " "
            print(f" {star} {n['label'][:40]:<40} 中心{n['center']}  {n['rid']}")
    print(f"\n已存 probe/{stamp}.png 與 .xml。遊戲畫面若沒有文字，請用截圖座標寫 tap_xy。")


def cmd_run(adb, args):
    global _log_file
    cfg_path = Path(args.config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    LOG_DIR.mkdir(exist_ok=True)
    _log_file = open(LOG_DIR / f"{dt.date.today():%Y%m%d}.log", "a", encoding="utf-8")
    adb.ensure_device()
    cfg["package"] = resolve_package(adb, cfg)
    wake(adb)
    return Runner(adb, cfg).run(args.task)


def main():
    ap = argparse.ArgumentParser(description="HAPPY GO Playio 每日任務（ADB）")
    ap.add_argument("--adb", default=os.environ.get("ADB", "adb"), help="adb 執行檔路徑")
    ap.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"), help="多台裝置時指定序號")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("probe", help="偵察目前畫面")
    r = sub.add_parser("run", help="執行任務")
    r.add_argument("--config", default=str(HERE / "tasks.json"))
    r.add_argument("--task", help="只跑名稱含此字的任務")
    args = ap.parse_args()
    adb = Adb(args.adb, args.serial)
    sys.exit(cmd_probe(adb, args) if args.cmd == "probe" else cmd_run(adb, args))


if __name__ == "__main__":
    main()
