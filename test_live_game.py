# -*- coding: utf-8 -*-
"""真机实测：自动启动《杀戮尖塔》，等窗口出来，走一遍完整链路（抓屏→模型→悬浮窗），
并把现场截图存档。不需要人工操作，也不需要进战斗。

    python test_live_game.py            # 启动游戏并测（游戏已在跑就直接测）
    python test_live_game.py --keep     # 测完不关游戏
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

import main as main_mod  # noqa: E402
from overlay import create_app  # noqa: E402

GAME_EXE = r"C:\Users\dqy20\steam\steamapps\common\SlayTheSpire\SlayTheSpire.exe"
SHOT = r"C:\Users\dqy20\AppData\Local\Temp\gc_live_test.png"
KEEP = "--keep" in sys.argv

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def find_game_window() -> int:
    """找 SlayTheSpire 的顶层窗口句柄。"""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.GetWindowTextW(hwnd, buf, length + 2)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if "slay" in buf.value.lower() or _pid_is_game(pid.value):
            found.append(hwnd)
        return True

    user32.EnumWindows(cb, None)
    return found[0] if found else 0


def _pid_is_game(pid: int) -> bool:
    """注意：游戏是用自带的 javaw.exe 跑的，进程名不是 SlayTheSpire.exe。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout.lower()
        return "slaythespire" in out or "javaw" in out
    except Exception:
        return False


def focus_window(hwnd: int) -> None:
    """把游戏窗口切到前台（要抓游戏画面就得让它显示在最上面）。"""
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 2)
    return buf.value


def main() -> int:
    hwnd = find_game_window()
    if not hwnd:
        if not os.path.isfile(GAME_EXE):
            print("找不到游戏可执行文件:", GAME_EXE)
            return 2
        print("启动游戏:", GAME_EXE)
        subprocess.Popen([GAME_EXE], cwd=os.path.dirname(GAME_EXE))
        deadline = time.time() + 90
        while time.time() < deadline and not hwnd:
            time.sleep(2)
            hwnd = find_game_window()
    if not hwnd:
        print("等了 90 秒游戏窗口还没出现，放弃")
        return 3

    print("找到窗口:", hex(hwnd), window_title(hwnd))
    focus_window(hwnd)          # 切到前台，否则抓到的是别的窗口
    time.sleep(2)
    time.sleep(4)               # 让游戏把画面画完

    app = create_app()
    bot = main_mod.Copilot(app)
    result: dict = {}

    def on_finish(res: dict) -> None:
        result.update(res)
        QTimer.singleShot(900, shoot)

    def on_fail(msg: str) -> None:
        result["error"] = msg
        QTimer.singleShot(600, shoot)

    def shoot() -> None:
        pix = QGuiApplication.primaryScreen().grabWindow(0)
        pix.save(SHOT, "PNG")
        print("现场截图:", SHOT)
        QTimer.singleShot(300, app.quit)

    bot.signals.finished.connect(on_finish)
    bot.signals.failed.connect(on_fail)
    QTimer.singleShot(400, lambda: bot.on_analyze("这是游戏的什么界面？我现在能做什么？"))
    QTimer.singleShot(120_000, app.quit)
    app.exec()

    print("=" * 72)
    if result.get("error"):
        print("失败:", result["error"])
    else:
        usage = result.get("usage") or {}
        print("模型回答:\n" + (result.get("text") or "").strip()[:1200])
        print("-" * 72)
        print("性能: %.1fs（首字 %.1fs）· %s tok · 思考 %s tok" % (
            result.get("elapsed", 0), result.get("ttfb", 0), usage.get("total_tokens"),
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        ))
    print("抓屏: ", bot.grabber.monitor_rect())
    print("累计 tokens:", bot.tokens_used, "· 请求数:", bot.requests)

    if not KEEP:
        try:
            subprocess.run(["taskkill", "/IM", "SlayTheSpire.exe", "/F"], capture_output=True, timeout=20)
            print("已关闭游戏（加 --keep 可保留）")
        except Exception as exc:
            print("关游戏失败:", exc)
    return 0 if result.get("text") else 1


if __name__ == "__main__":
    raise SystemExit(main())
