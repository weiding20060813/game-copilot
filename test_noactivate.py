# -*- coding: utf-8 -*-
"""验证「点悬浮窗不会把游戏顶到后台」这个功能真的生效。

检查两件事：
  1. 窗口扩展样式里确实带上了 WS_EX_NOACTIVATE
  2. 窗口显示并模拟点击后，它**没有**变成前台窗口

    python test_noactivate.py
"""
from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QTimer  # noqa: E402

import overlay as ov  # noqa: E402
from tray_config import WINDOW  # noqa: E402

user32 = ctypes.windll.user32
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
user32.SetProcessDPIAware()

RESULTS: dict = {}


def fg_title() -> str:
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 2)
    return buf.value or f"(无标题 hwnd={hwnd})"


def click_at(x: int, y: int) -> None:
    """用 SendInput 模拟一次真实鼠标点击（不需要管理员权限）。"""
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]

    user32.SetCursorPos(int(x), int(y))
    for flags in (0x0002, 0x0004):        # LEFTDOWN, LEFTUP
        inp = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flags, 0, None))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def main() -> int:
    app = ov.create_app()
    win = ov.Overlay(WINDOW, subtitle="· 焦点测试")
    win.move(1200, 300)
    win.show()
    win.raise_()
    hwnd = int(win.winId())

    def step1() -> None:
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        has = bool(style & WS_EX_NOACTIVATE)
        RESULTS["style"] = has
        print(f"1) 窗口扩展样式 = 0x{style:08X}，含 WS_EX_NOACTIVATE: {'✅ 是' if has else '❌ 否'}")

    def step2() -> None:
        before = fg_title()
        cx = win.x() + win.width() // 2
        cy = win.y() + win.height() - 40          # 点底部按钮区
        print(f"2) 点击前的前台窗口: {before}")
        click_at(cx, cy)
        QTimer.singleShot(600, lambda: step3(before, cx, cy))

    def step3(before: str, cx: int, cy: int) -> None:
        after = fg_title()
        print(f"   点击后 ({cx},{cy}) 的前台窗口: {after}")
        kept = (after == before)
        RESULTS["focus"] = kept
        print(f"   前台窗口没被抢走: {'✅ 是' if kept else '❌ 否（被顶到前台了）'}")
        # 关掉这个开关后，应该变回"会被激活"
        win.set_no_activate(False)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        off = not bool(style & WS_EX_NOACTIVATE)
        print(f"3) 关掉开关后样式里已去掉 NOACTIVATE: {'✅ 是' if off else '❌ 否'}")
        RESULTS["toggle"] = off
        QTimer.singleShot(300, finish)

    def finish() -> None:
        print("=" * 60)
        ok = all(RESULTS.get(k) for k in ("style", "focus", "toggle"))
        print("结论:", "不抢焦点功能正常 ✅" if ok else f"有问题 ❌ {RESULTS}")
        win.hide()
        app.quit()

    QTimer.singleShot(1200, step1)
    QTimer.singleShot(2000, step2)
    QTimer.singleShot(12000, app.quit)
    app.exec()
    return 0 if all(RESULTS.get(k) for k in ("style", "focus", "toggle")) else 1


if __name__ == "__main__":
    sys.exit(main())
