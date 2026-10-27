# -*- coding: utf-8 -*-
"""F8 全链路 + 消息保留验证。

做两件事：
  1. 用真实的 WM_HOTKEY 消息触发 F8（消息循环 → 动作表 → 排队回主线程 → on_analyze），
     验证「按 F8 就能看屏幕」这条链在生产路径上是通的。
  2. 连续跑 3 轮，确认悬浮窗里的历史消息逐轮递增，不会被吞掉。

    python test_f8_flow.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as main_mod  # noqa: E402
from overlay import create_app  # noqa: E402

TOTAL_ROUNDS = 3
STATE: dict = {"round": 0, "ok": False, "checkpoints": [], "started": False}


def main() -> int:
    app = create_app()
    bot = main_mod.Copilot(app)

    def finish(ok: bool) -> None:
        STATE["ok"] = ok
        QTimer.singleShot(100, app.quit)

    def report() -> None:
        print("\n" + "=" * 66)
        print("消息增长轨迹（应逐轮递增，不能变少）：")
        for label, count in STATE["checkpoints"]:
            print(f"  {label} -> 窗口里 {count} 条")
        counts = [c for _, c in STATE["checkpoints"]]
        growing = len(counts) == TOTAL_ROUNDS and all(
            counts[i] < counts[i + 1] for i in range(len(counts) - 1)
        )
        print("逐轮递增:", "✅" if growing else "❌ 有消息被吞掉")
        print("最终窗口消息数:", len(bot.win._blocks), "· 上下文条数:", len(bot.history))
        print("累计 tokens:", bot.tokens_used)
        print("=" * 66)
        print("结论:", "F8 全流程 + 消息保留 都正常 ✅" if growing else "没通过 ❌")
        finish(bool(growing))

    def on_finished(result: dict) -> None:
        rnd = len(STATE["checkpoints"]) + 1
        text = (result.get("text") or "").strip()
        STATE["checkpoints"].append((f"第{rnd}轮 {len(text)}字", len(bot.win._blocks)))
        print(f"  ✅ 第{rnd}轮完成：回答 {len(text)} 字，窗口里共 {len(bot.win._blocks)} 条消息", flush=True)
        print("     回答开头:", text[:70].replace("\n", " "), flush=True)
        if rnd < TOTAL_ROUNDS:
            QTimer.singleShot(600, drive)
        else:
            QTimer.singleShot(400, report)

    def drive() -> None:
        """每一轮用不同入口，覆盖「按热键」和「打字提问」两条路径。"""
        n = len(STATE["checkpoints"]) + 1
        if n == 1:
            # 对照实验：主线程自己投递一次，验证桥本身是否可用
            main_mod.Copilot.ui_call(lambda: print("  [对照] 主线程自投递执行 ✅", flush=True))
            combo = bot.key_of("analyze")
            print(f"\n[第1轮] 模拟按 {combo}（走真实热键链路）", flush=True)
            bot.hotkey_mgr.simulate(combo)
            QTimer.singleShot(2500, check_started)
        else:
            print(f"\n[第{n}轮] 模拟打字提问", flush=True)
            bot.on_user_text(f"第{n}轮：用一句话说说画面里现在是什么。")

    def check_started() -> None:
        if bot.busy or len(STATE["checkpoints"]) >= 1:
            return
        print("❌ 按了 F8 但动作没有触发（热键消息没送到）")
        finish(False)

    def on_failed(msg: str) -> None:
        print("❌ 请求失败:", msg, flush=True)
        finish(False)

    bot.signals.finished.connect(on_finished)
    bot.signals.failed.connect(on_failed)
    QTimer.singleShot(1500, drive)          # 等启动自检先跑完
    QTimer.singleShot(240_000, app.quit)     # 全局兜底超时
    app.exec()
    return 0 if STATE["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
