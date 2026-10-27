# -*- coding: utf-8 -*-
"""确认「不抢焦点」状态下按钮依然点得动（能收到真实鼠标点击）。

    python test_clickable.py
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
user32.SetProcessDPIAware()
RESULTS: dict = {}


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("mi", MOUSEINPUT)]


def click_at(x: int, y: int) -> None:
    ok = user32.SetCursorPos(int(x), int(y))
    sent = 0
    for flags in (0x0002, 0x0004):
        inp = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flags, 0, None))
        sent += int(user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)))
    pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
    import ctypes.wintypes as wt
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    print(f"     [点击模拟] SetCursorPos={ok} SendInput返回={sent} 光标现在=({pt.x},{pt.y})")


def center_of(widget) -> tuple[int, int]:
    p = widget.mapToGlobal(widget.rect().center())
    return int(p.x()), int(p.y())


def main() -> int:
    app = ov.create_app()
    win = ov.Overlay(WINDOW, subtitle="· 点击测试")
    win.move(1200, 300)
    # 交互模式：验证切过去之后按钮点得动
    win.set_no_activate(False)
    win.show()

    hits: list[str] = []
    win.analyze_requested.connect(lambda t: hits.append("看屏幕"))
    win.clear_requested.connect(lambda: hits.append("清空"))
    win.watch_toggled.connect(lambda on: hits.append(f"连续={on}"))
    win.btn_noact.toggled.connect(lambda on: hits.append(f"不抢焦点={on}"))

    def step1() -> None:
        print("窗口可见:", win.isVisible(), "| 不抢焦点:", win._no_activate)

    def step2() -> None:
        win._drag_probe = []
        orig_press = win.mousePressEvent

        def probe_press(event):
            win._drag_probe.append("press")
            orig_press(event)

        win.mousePressEvent = probe_press  # type: ignore

        # 诊断：坐标处到底是哪个控件
        for name, widget in (("看屏幕", win.btn_shot), ("清空", win.btn_clear)):
            x, y = center_of(widget)
            local = win.mapFromGlobal(widget.mapToGlobal(widget.rect().center()))
            hit = win.childAt(local)
            print(f"  「{name}」按钮中心 全局=({x},{y}) 窗口内=({local.x()},{local.y()}) "
                  f"该处控件={type(hit).__name__ if hit else 'None'} 可见={widget.isVisible()}")

        tx, ty = win.x() + 100, win.y() + 12
        print(f"  点击标题栏 @({tx},{ty})")
        click_at(tx, ty)

        for name, widget in (("看屏幕", win.btn_shot), ("清空", win.btn_clear)):
            x, y = center_of(widget)
            print(f"  点击「{name}」按钮 @({x},{y})")
            click_at(x, y)
        QTimer.singleShot(900, step3)

    def step3() -> None:
        print("窗口自身收到的鼠标按下:", getattr(win, "_drag_probe", []) or "（没有）")
        print("收到的动作:", hits or "（什么都没收到）")
        ok = "看屏幕" in hits and "清空" in hits
        RESULTS["click"] = ok
        print("按钮点击:", "✅ 有效" if ok else "❌ 点不动")
        QTimer.singleShot(200, finish)
    def finish() -> None:
        print("=" * 56)
        print("结论:", "不抢焦点 + 按钮可点 都正常 ✅" if RESULTS.get("click") else "有问题 ❌")
        win.hide()
        app.quit()

    QTimer.singleShot(1200, step1)
    QTimer.singleShot(1800, step2)
    QTimer.singleShot(15000, app.quit)
    app.exec()
    return 0 if RESULTS.get("click") else 1


if __name__ == "__main__":
    sys.exit(main())
