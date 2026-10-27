# -*- coding: utf-8 -*-
"""无头自检：走一遍「抓屏 → 请求模型 → 渲染」的全链路，不需要人动手。

    python test_headless.py            # 用桌面画面测
    python test_headless.py shot.jpg   # 用指定图片当假画面测（离线可跑）
"""
from __future__ import annotations

import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as main_mod  # noqa: E402
from overlay import create_app  # noqa: E402

RESULT: dict = {"done": False, "ok": False, "text": "", "err": "", "meta": ""}


def run() -> int:
    app = create_app()
    bot = main_mod.Copilot(app)

    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1], "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
        fake_url = "data:image/jpeg;base64," + data
        bot.capture = lambda: (fake_url, "假画面（本地图片）")  # type: ignore

    bot.signals.finished.connect(lambda r: finish(r))
    bot.signals.failed.connect(lambda m: fail(m))

    def finish(result: dict) -> None:
        usage = result.get("usage") or {}
        RESULT.update(
            done=True, ok=bool(result.get("text", "").strip()), text=result["text"],
            meta="%.1fs 首字 %.1fs %s tok" % (result.get("elapsed", 0), result.get("ttfb", 0), usage.get("total_tokens")),
        )
        QTimer.singleShot(200, app.quit)

    def fail(msg: str) -> None:
        RESULT.update(done=True, ok=False, err=msg)
        QTimer.singleShot(200, app.quit)

    QTimer.singleShot(300, lambda: bot.on_analyze(""))
    QTimer.singleShot(120_000, app.quit)  # 全局超时
    app.exec()

    print("=" * 60)
    print("抓屏能力:", bot.grabber.monitor_rect())
    print("热键注册:", bot.hotkeys_ok)
    print("API Key 来源:", bot.key_source)
    print("对话轮数:", len(bot.history), "累计 tokens:", bot.tokens_used)
    print("流式渲染块数:", len(bot.win._blocks))
    print("busy 已复位:", not bot.busy)
    print("-" * 60)
    if RESULT["ok"]:
        print("模型回答:", RESULT["text"][:600])
        print("性能:", RESULT["meta"])
        print("结果: 通过 ✅")
        return 0
    print("结果: 失败 ❌", RESULT["err"] or "没有拿到回答")
    return 1


if __name__ == "__main__":
    sys.exit(run())
