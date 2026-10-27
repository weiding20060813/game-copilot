# -*- coding: utf-8 -*-
"""热键链路自测：不开游戏、不按键，直接用 WM_HOTKEY 消息验证
「系统热键 → 回调 → Qt 主线程 → 动作」这条链是通的。

    python test_hotkeys.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["COPILOT_SYNC_HOTKEYS"] = "1"   # 无头环境没有事件循环，让热键回调直接执行

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as main_mod  # noqa: E402
from overlay import create_app  # noqa: E402

RESULTS: dict[str, bool] = {}


def main() -> int:
    app = create_app()
    bot = main_mod.Copilot(app)
    mgr = bot.hotkey_mgr

    print("=" * 66)
    print("实际生效的热键:", bot.hotkey_names)
    print("注册失败:", mgr.failures or "无")
    missing = [a for a in ("analyze", "toggle_window", "toggle_watch", "toggle_click_through")
               if a not in bot.hotkey_names]
    if missing:
        print("❌ 这些动作连备选键都没注册上:", missing)
        return 1

    # 把每个热键动作包一层，记录有没有被触发（改动作表，热键触发时会现查）
    fired: dict[str, list] = {"analyze": [], "toggle_window": [], "watch": [], "through": []}
    bot._actions.update({
        "analyze": lambda: fired["analyze"].append(1),
        "toggle_window": lambda: fired["toggle_window"].append(1),
        "toggle_watch": lambda: fired["watch"].append(1),
        "toggle_click_through": lambda: fired["through"].append(1),
    })

    import tray_config as cfg
    hk = cfg.HOTKEYS
    plan = [
        ("analyze", hk["analyze"]),
        ("toggle_window", hk["toggle_window"]),
        ("toggle_watch", hk["toggle_watch"]),
        ("toggle_click_through", hk["toggle_click_through"]),
    ]

    def send_all() -> None:
        for action, _cfg_key in plan:
            combo = bot.key_of(action)          # 用真正注册上的那个键
            ok = mgr.simulate(combo)
            print(f"  发送 {action} -> {combo}: {'已投递' if ok else '投递失败'}")

    def verify() -> None:
        QApplication.processEvents()      # 把排队的 Qt 调用放出来
        print("-" * 66)
        ok = True
        for name, expects in (("analyze", 1), ("toggle_window", 1), ("watch", 1), ("through", 1)):
            got = len(fired[name])
            flag = "✅" if got >= expects else "❌"
            if got < expects:
                ok = False
            print(f"{flag} {name}: 触发 {got} 次（期望 ≥{expects}）")
        print("=" * 66)
        print("结论:", "热键链路全部打通 ✅" if ok else "有热键没反应 ❌")
        RESULTS["ok"] = ok
        QTimer.singleShot(200, app.quit)

    QTimer.singleShot(600, send_all)
    QTimer.singleShot(900, lambda: [QApplication.processEvents() for _ in range(5)])
    QTimer.singleShot(2500, verify)
    QTimer.singleShot(15000, app.quit)
    app.exec()
    try:
        bot.hotkey_mgr.stop()
    except Exception:
        pass
    return 0 if RESULTS.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
