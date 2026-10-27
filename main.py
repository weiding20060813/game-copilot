# -*- coding: utf-8 -*-
"""AI 游戏陪玩 · 主程序

运行：python main.py    （或者双击 启动-ai陪玩.bat）
热键：F8 看屏幕分析 / F9 显示隐藏 / F10 连续陪玩 / F11 鼠标穿透 / F12 清空
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal  # noqa: E402
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon  # noqa: E402

import prompts  # noqa: E402
import runinfo  # noqa: E402
import screen as screen_mod  # noqa: E402
from hotkeys import GlobalHotkeys, is_combo_free  # noqa: E402
import tray_config as cfg_mod  # noqa: E402
from llm import LLMError, VisionClient, find_api_key  # noqa: E402
from overlay import Overlay, create_app  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "copilot.log")


def log(msg: str) -> None:
    """写日志。控制台一份、copilot.log 一份（强制 UTF-8，避免中文乱码）。"""
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# ==========================================================================
# 工作线程：抓屏 + 请求模型
#
# 注意：跨线程只通过 Qt 信号回主线程，绝不在子线程碰 UI 对象。
# ==========================================================================
class WorkerSignals(QObject):
    text_delta = Signal(str)
    reasoning_delta = Signal(str)
    finished = Signal(dict)
    failed = Signal(str)


class UiBridge(QObject):
    """热键线程 → Qt 主线程的"投递器"。

    热键消息循环在独立线程，直接在那边改控件会触发 Qt 跨线程错误。
    用信号 + 队列连接是最可靠的做法：对象属于主线程，emit 发生在哪个线程都安全，
    投递一定会在主线程的事件循环里被执行（比 QTimer.singleShot 可靠）。
    """

    dispatch = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.dispatch.connect(self._run, Qt.ConnectionType.QueuedConnection)
        log(f"UiBridge 已就绪（归属线程 {threading.get_ident()}）")

    def _run(self, fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001
            log("主线程执行动作出错:\n" + traceback.format_exc())

    def post(self, fn) -> None:
        self.dispatch.emit(fn)


def worker_loop(work_queue: "queue.Queue", sig: WorkerSignals, client: VisionClient,
                base_prompt: str, deck_getter) -> None:
    """子线程主循环：从队列取任务发请求。

    卡组/遗物资料只在「一条对话的第一句」带上，后面它已经在上下文里了，不用重复发（省 token）。
    """
    sent_deck = False
    while True:
        item = work_queue.get()
        if item is None:
            return
        text, image_url, history, hint, reset = item
        if reset:
            sent_deck = False
        try:
            system_prompt = base_prompt
            if not sent_deck and not history:
                deck = deck_getter()
                if deck:
                    system_prompt = base_prompt + "\n\n" + deck
                    log("已把卡组/遗物资料带进上下文（%d 字）" % len(deck))
                sent_deck = True
            messages = [{"role": "system", "content": system_prompt}] + list(history)
            user_text = text + (("\n" + hint) if hint else "")
            messages.append(VisionClient.build_user_message(user_text, image_url))
            result = client.stream_chat(
                messages, on_text=sig.text_delta.emit, on_reasoning=sig.reasoning_delta.emit
            )
            result["user_text"] = user_text
            result["had_image"] = bool(image_url)
            sig.finished.emit(result)
        except LLMError as exc:
            sig.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            log("worker 异常:\n" + traceback.format_exc())
            sig.failed.emit(f"{type(exc).__name__}: {exc}")


# ==========================================================================
# 主控制器
# ==========================================================================
class Copilot(QObject):
    do_work = Signal(str, object, list, str)
    _ui_bridge: "UiBridge | None" = None      # 启动时创建，热键线程投递动作用

    def __init__(self, app: QApplication):
        super().__init__()
        self.app = app
        self.settings = QSettings("dsh", "ai-game-copilot")
        self.cfg_api = dict(cfg_mod.API)
        self.cfg_screen = dict(cfg_mod.SCREEN)
        self.cfg_window = dict(cfg_mod.WINDOW)
        self.cfg_behavior = dict(cfg_mod.BEHAVIOR)

        key, src = find_api_key(self.cfg_api.get("api_key", ""), [APP_DIR])
        self.key_source = src
        self.client = VisionClient(self.cfg_api, key)
        self.grabber = screen_mod.ScreenGrabber(
            self.cfg_screen["monitor"], self.cfg_screen["max_side"], self.cfg_screen["jpeg_quality"]
        )
        self.system_prompt = prompts.get("slay_the_spire")
        self.history: list[dict] = []
        self.busy = False
        self.watching = False
        self.tokens_used = 0
        self.requests = 0

        # ---- 悬浮窗 ----
        self.win = Overlay(self.cfg_window, subtitle="· 杀戮尖塔")
        self.win.submitted.connect(self.on_user_text)
        self.win.analyze_requested.connect(self.on_analyze)
        self.win.stop_requested.connect(self.on_stop)
        self.win.watch_toggled.connect(self.set_watching)
        self.win.clear_requested.connect(self.on_clear)
        self.win.closed.connect(self.hide_window)
        self.win.geometry_changed.connect(self.save_geometry)
        saved_pos = self.settings.value("window/pos")
        pos = None
        if saved_pos:
            try:
                pos = (int(saved_pos[0]), int(saved_pos[1]))
            except Exception:
                pos = None
        self.win.restore_position(pos)
        self.win.show()

        # ---- 工作线程 ----
        self.signals = WorkerSignals()
        self.signals.text_delta.connect(self.win.push_stream_text)
        self.signals.reasoning_delta.connect(self.win.push_stream_reasoning)
        self.signals.finished.connect(self.on_finished)
        self.signals.failed.connect(self.on_failed)
        self._work_queue: queue.Queue = queue.Queue()
        self._worker_thread = threading.Thread(
            target=worker_loop,
            args=(self._work_queue, self.signals, self.client, self.system_prompt,
                  lambda: runinfo.context_for_prompt([APP_DIR])),
            name="copilot-worker",
            daemon=True,
        )
        self._worker_thread.start()

        # ---- 连续陪玩定时器 ----
        self.watch_timer = QTimer(self)
        self.watch_timer.setInterval(max(4, int(self.cfg_behavior["watch_interval_sec"])) * 1000)
        self.watch_timer.timeout.connect(self.on_watch_tick)

        # ---- 托盘 ----
        self.tray = self._make_tray()

        # ---- 跨线程投递器 ----
        # 必须在主线程、启动阶段就建好：以前是"第一次用到才懒加载"，
        # 连按热键时多个回调会同时创建、互相覆盖，先建的那个待投递任务就没接收者了，
        # 表现就是"按了没反应"（日志里 F8 触发了 7 次只有 1 次真正执行）。
        Copilot._ui_bridge = UiBridge()

        # ---- 热键 ----
        self.hotkeys_ok = False
        self.hotkey_names: dict[str, str] = {}
        self._taken_combos: set[str] = set()   # 给 GlobalHotkeys 看的"已经计划用掉的键"
        self._install_hotkeys()

        self.win.set_status(self._ready_status())
        deck_info = runinfo.summarize([APP_DIR])
        self.win.add_message(
            "sys",
            f"就绪：抓屏 {self.grabber.monitor_rect()['width']}×{self.grabber.monitor_rect()['height']} · "
            f"模型 {self.cfg_api['model']} · Key 来自「{src}」。\n"
            f"热键：{self.key_of('analyze')} 看屏幕 · {self.key_of('toggle_mode')} 切换只读/交互 · "
            f"{self.key_of('toggle_watch')} 连续陪玩 · {self.key_of('toggle_window')} 显示/隐藏 · "
            f"{self.key_of('clear_history')} 清空\n"
            "默认是【只读模式】：游戏永远不会掉后台，但窗口点不中；要打字就按 "
            f"{self.key_of('toggle_mode')} 切成交互模式。\n"
            + ("已读到你的卡组资料：" + deck_info.splitlines()[1] if deck_info else
               "（没读到卡组资料，想聊选牌的话在程序目录建个 deck.txt 粘一份卡组）"),
        )
        if not key:
            self.win.add_message(
                "err",
                "没找到 API Key。请设置环境变量 DEEPSEEK_API_KEY，或在程序目录新建 apikey.txt 写一行 key。",
            )
        else:
            # 后台做一次廉价连通性检查（不发图片），有问题立刻告诉你
            threading.Thread(target=self._health_check, name="health-check", daemon=True).start()

    # ------------------------------------------------------------------ 状态
    def _health_check(self) -> None:
        """启动时的可用性自检：确认 key、模型、网络都通。"""
        try:
            out = self.client.chat([{"role": "user", "content": "只回复两个字：可用"}])
            text = (out.get("text") or "").strip().replace("\n", " ")
            log(f"自检通过：{text[:40]} ({out['elapsed']:.1f}s)")
            QTimer.singleShot(0, lambda: self.win.set_status(
                self._ready_status() + " · 自检通过"
            ))
        except LLMError as exc:
            log(f"自检失败：{exc}")
            QTimer.singleShot(0, lambda: self.win.add_message(
                "err",
                f"⚠️ 启动自检没过：{exc}\n"
                "先解决这个再玩，不然按了 F8 也只会报同样的错。",
            ))

    def _ready_status(self) -> str:
        hk = "热键OK" if self.hotkeys_ok else "热键异常"
        return (f"{hk} · 本窗口 {len(self.win._blocks)} 条消息 · 请求 {self.requests} 次"
                f" · {self.tokens_used} tokens · PID {os.getpid()}")

    def _make_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        icon = self._make_icon()
        tray = QSystemTrayIcon(icon, self)
        tray.setToolTip("AI 游戏陪玩（F8 看屏幕）")
        menu = QMenu()
        act_show = QAction("显示 / 隐藏窗口 (F9)", self)
        act_show.triggered.connect(self.toggle_window)
        act_mode = QAction("切换 只读 / 交互模式 (F7)", self)
        act_mode.triggered.connect(lambda: self.win.set_interactive(self.win._no_activate))
        act_shot = QAction("看屏幕分析 (F8)", self)
        act_shot.triggered.connect(lambda: self.on_analyze(""))
        act_watch = QAction("连续陪玩 (F10)", self)
        act_watch.triggered.connect(lambda: self.set_watching(not self.watching))
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_app)
        for act in (act_show, act_mode, act_shot, act_watch):
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction(act_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: self.toggle_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        return tray

    @staticmethod
    def _make_icon() -> QIcon:
        png = os.path.join(APP_DIR, "icon.png")
        if os.path.isfile(png):
            return QIcon(png)
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor("#7cc4ff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 16, 16)
        painter.setPen(QColor("#06121f"))
        painter.setFont(QFont("Microsoft YaHei UI", 26, QFont.Weight.Bold))
        painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "AI")
        painter.end()
        return QIcon(pix)

    # ------------------------------------------------------------------ 热键
    def _install_hotkeys(self) -> None:
        """用 Windows 原生 RegisterHotKey 注册，支持"首选键被占用就自动换备选键"。

        实测：keyboard 库的低层钩子在游戏全屏时不工作（注册"成功"却从不触发），
        RegisterHotKey 是系统级注册，不需要管理员权限，全屏游戏也能收到。
        """
        spec = cfg_mod.HOTKEYS
        if not spec.get("enabled", True):
            log("热键已在配置里关闭")
            self.win.add_message("sys", "热键已在配置里关闭，请用界面按钮操作。")
            return

        # 首选键来自配置，后面是可以自动顶上的备选键
        plan: dict[str, tuple[list[str], object]] = {
            "analyze": ([spec["analyze"], "ctrl+alt+z"], lambda: self.on_analyze("")),
            "toggle_mode": ([spec.get("toggle_mode", "f7"), "ctrl+alt+m"],
                            lambda: self.win.set_interactive(self.win._no_activate)),
            "toggle_window": ([spec["toggle_window"], "ctrl+alt+x"], self.toggle_window),
            "toggle_watch": ([spec["toggle_watch"], "ctrl+alt+c"], lambda: self.set_watching(not self.watching)),
            "toggle_click_through": (
                [spec["toggle_click_through"], "ctrl+alt+v"],
                lambda: self.win.set_click_through(not self.win._click_through),
            ),
            "clear_history": ([spec["clear_history"], "ctrl+alt+b", "ctrl+shift+f12"], self.on_clear),
        }
        names = {
            "analyze": "看屏幕分析", "toggle_mode": "只读/交互模式", "toggle_window": "显示/隐藏窗口",
            "toggle_watch": "连续陪玩", "toggle_click_through": "鼠标穿透", "clear_history": "清空上下文",
        }
        # 动作表在"触发时"查，这样随时替换动作都生效（也方便自测）
        self._actions = {
            "analyze": lambda: self.on_analyze(""),
            "toggle_mode": lambda: self.win.set_interactive(self.win._no_activate),
            "toggle_window": self.toggle_window,
            "toggle_watch": lambda: self.set_watching(not self.watching),
            "toggle_click_through": lambda: self.win.set_click_through(not self.win._click_through),
            "clear_history": self.on_clear,
        }

        # 逐个动作、逐个候选键地试：注册不上就换下一个候选键
        combo_to_action: dict[str, str] = {}
        for action, (candidates, _fn) in plan.items():
            for combo in candidates:
                if combo in combo_to_action:
                    continue                       # 别的动作用过了
                if self.hotkey_probe(combo):        # 试注册，成功才正式占用
                    combo_to_action[combo] = action
                    break
        log("准备注册的热键：" + "、".join(f"{names[a]}={c}" for c, a in combo_to_action.items()))

        def make_callback(action: str):
            def wrapped() -> None:
                log(f"热键触发：{action}")
                self.ui_call(self._actions[action])   # 触发时才取，保证拿的是最新的
            return wrapped

        callbacks = {combo: make_callback(action) for combo, action in combo_to_action.items()}
        self.hotkey_mgr = GlobalHotkeys(callbacks, log=log).start()
        self.hotkey_names = {v: k for k, v in combo_to_action.items() if k in self.hotkey_mgr.registered}

        failed = [c for c in combo_to_action if c not in self.hotkey_mgr.registered]
        self.hotkeys_ok = not failed
        log("实际生效的热键：" + "、".join(f"{names[a]}={c}" for a, c in self.hotkey_names.items()))
        if failed:
            log("以下候选键都没注册上：" + "、".join(failed))
            self.win.add_message(
                "err",
                "有热键连备选键都被占用了：" + "、".join(failed)
                + "\n去 tray_config.py 的 HOTKEYS 里换成别的键，或者直接用界面按钮。",
            )

    @staticmethod
    def hotkey_probe(combo: str) -> bool:
        """试注册一个热键看是否空闲（立刻释放）。用来在正式注册前挑候选键。"""
        free = is_combo_free(combo)
        if not free:
            log(f"热键候选 {combo} 被占用，换下一个候选")
        return free

    def key_of(self, action: str) -> str:
        return getattr(self, "hotkey_names", {}).get(action, "未生效")

    @staticmethod
    def ui_call(fn):
        """从热键线程回到 Qt 主线程执行（见 UiBridge 的说明）。"""
        bridge = Copilot._ui_bridge
        if bridge is None:                      # 兜底：正常情况下启动时就建好了
            bridge = Copilot._ui_bridge = UiBridge()
            log("警告：UiBridge 是临时补建的，说明启动流程有遗漏")
        bridge.post(fn)

    # ------------------------------------------------------------------ 视窗
    def toggle_window(self) -> None:
        if self.win.isVisible():
            self.hide_window()
        else:
            self.show_window()

    def show_window(self) -> None:
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()

    def hide_window(self) -> None:
        self.win.hide()

    def save_geometry(self) -> None:
        self.settings.setValue("window/pos", self.win.current_position())
        self.settings.setValue("window/size", (self.win.width(), self.win.height()))

    # -------------------------------------------------------------- 任务提交
    def submit(self, text: str, image_url: str | None, hint: str = "", reset: bool = False) -> None:
        self.busy = True
        self.win.set_busy(True)
        self.win.set_status("正在看图…" if image_url else "正在思考…")
        self.win.begin_stream()
        self._work_queue.put((text, image_url, list(self.history), hint, reset))

    # ------------------------------------------------------------------ 抓屏
    def capture(self) -> tuple[str | None, str]:
        """抓一帧，返回 (data_url, 说明文字)。抓屏瞬间临时藏起悬浮窗，
        这样 AI 不会把聊天记录也当成画面内容；抓完再把焦点还给你。"""
        hide_self = self.cfg_screen.get("hide_self_when_capture", True)
        restore_focus = False
        if hide_self and self.win.isVisible():
            restore_focus = self.win.isActiveWindow() or self.win.input.hasFocus()
            self.win.hide()
            QApplication.processEvents()
            try:
                time.sleep(max(0, int(self.cfg_screen.get("hide_delay_ms", 120))) / 1000.0)
                jpeg, original, sent, cost = self.grabber.grab_jpeg()
            finally:
                self.win.show()
                if restore_focus:
                    self.win.raise_()
                    self.win.activateWindow()
                    self.win.input.setFocus()
        else:
            jpeg, original, sent, cost = self.grabber.grab_jpeg()
        note = f"抓屏 {original[0]}×{original[1]} → {sent[0]}×{sent[1]} · {len(jpeg)//1024}KB · {cost*1000:.0f}ms"
        return screen_mod.ScreenGrabber.to_data_url(jpeg), note

    # ------------------------------------------------------------------ 入口
    def on_user_text(self, text: str) -> None:
        if self.busy:
            log("上一个请求还没结束，这次输入被忽略")
            return
        log(f"收到输入：{text[:40]}")
        image_url, note = self.capture()
        self.win.add_message("user", text, meta=note)
        self.submit(text, image_url, hint=prompts.CHAT_HINT)

    def on_analyze(self, extra_text: str) -> None:
        if self.busy:
            log("上一个请求还没结束，忽略这次看屏")
            return
        log("开始看屏幕分析")
        text = extra_text.strip() or prompts.QUICK_ANALYZE
        if extra_text.strip():
            text = f"{extra_text.strip()}\n（顺便看一眼我的屏幕）"
        image_url, note = self.capture()
        self.win.add_message("user", "📷 " + (extra_text.strip() or "看看现在该怎么打"), meta=note)
        delay = max(0, int(self.cfg_behavior.get("hotkey_release_delay_ms", 250)))
        if delay:
            QTimer.singleShot(delay, lambda: self.submit(text, image_url))
            self.busy = True
            self.win.set_busy(True)
        else:
            self.submit(text, image_url)

    def on_watch_tick(self) -> None:
        if self.busy or not self.watching:
            return
        image_url, note = self.capture()
        self.win.add_message("sys", "⏱ 自动巡检", meta=note)
        self.submit(prompts.WATCH_ANALYZE, image_url, hint="如果画面没有需要处理的变化，只回「没变化」。")

    def on_stop(self) -> None:
        self.client.cancel()
        self.win.set_status("已请求中断…")

    def on_clear(self) -> None:
        log("清空对话上下文")
        self.history.clear()
        self.win.clear_chat()
        self.win.add_message("sys", "对话上下文已清空（token 计数保留）。下次提问会重新带上卡组资料。")
        self.win.set_status(self._ready_status())

    def set_watching(self, on: bool) -> None:
        self.watching = bool(on)
        self.win.set_watch_state(self.watching)
        if self.watching:
            self.watch_timer.start()
            self.win.set_status(f"连续陪玩中：每 {self.watch_timer.interval()//1000}s 看一次屏")
        else:
            self.watch_timer.stop()
            self.win.set_status(self._ready_status())

    # ------------------------------------------------------------------ 回调
    def on_finished(self, result: dict) -> None:
        usage = result.get("usage") or {}
        total = int(usage.get("total_tokens") or 0)
        self.tokens_used += total
        self.requests += 1
        meta = "%.1fs（首字 %.1fs）· %d tok" % (
            result.get("elapsed", 0), result.get("ttfb", 0), total
        )
        self.win.end_stream(meta=meta)
        self.win.set_busy(False)
        self.busy = False

        # 维护上下文
        self.history.append({"role": "user", "content": result.get("user_text", "")})
        self.history.append({"role": "assistant", "content": result.get("text", "")})
        self._prune_history()
        self.win.set_status(self._ready_status())

    def on_failed(self, message: str) -> None:
        self.win.end_stream(meta="失败")
        self.win.add_message("err", "出错了：" + message)
        self.win.set_busy(False)
        self.busy = False
        self.win.set_status(self._ready_status())

    def _prune_history(self) -> None:
        """上下文裁剪：只留最近 N 条，历史里最多留 K 张图（图很贵，默认只留 1 张）。"""
        limit = max(2, int(self.cfg_behavior.get("history_max_messages", 16)))
        self.history = self.history[-limit:]
        keep_images = int(self.cfg_behavior.get("history_keep_images", 0))
        image_idx = [
            i for i, msg in enumerate(self.history)
            if isinstance(msg.get("content"), list)
        ]
        for i in image_idx[:-keep_images] if keep_images else image_idx:
            texts = [p.get("text", "") for p in self.history[i]["content"] if p.get("type") == "text"]
            self.history[i] = {"role": "user", "content": "".join(texts) or "(上一张截图)"}

    # ------------------------------------------------------------------ 退出
    def quit_app(self) -> None:
        self.save_geometry()
        try:
            self.watch_timer.stop()
            self.client.cancel()
            self._work_queue.put(None)
            self.hotkey_mgr.stop()
        except Exception:
            pass
        if self.tray:
            self.tray.hide()
        self.app.quit()


# ==========================================================================
# 单实例锁：开两份会互相抢热键，也会出现"消息看着消失了"的错觉
# ==========================================================================
_LOCK_HANDLE = None


def acquire_single_instance() -> bool:
    """抢独占锁；抢不到说明已经有一份在跑了。锁随进程结束自动释放。"""
    global _LOCK_HANDLE
    try:
        import msvcrt
        path = os.path.join(os.environ.get("TEMP", APP_DIR), "ai_game_copilot.lock")
        _LOCK_HANDLE = open(path, "a+", encoding="utf-8")
        msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
        _LOCK_HANDLE.seek(0)
        _LOCK_HANDLE.truncate()
        _LOCK_HANDLE.write(str(os.getpid()))
        _LOCK_HANDLE.flush()
        return True
    except OSError:
        return False
    except Exception as exc:  # 锁机制出问题不该挡着人用
        log(f"单实例锁异常（忽略）：{exc}")
        return True


# ==========================================================================
def main() -> int:
    app = create_app()
    if not acquire_single_instance():
        log("已经有一份在运行了，这次启动取消")
        QMessageBox.information(
            None, "AI 游戏陪玩",
            "已经有一个「AI 游戏陪玩」在运行了。\n\n"
            "再开一份会让两个窗口抢同一批热键，看起来就像热键失灵、消息丢失。\n"
            "请用已经在跑的那个窗口（按 F9 可以显示/隐藏，任务栏托盘也有图标）。",
        )
        return 0
    try:
        bot = Copilot(app)
    except Exception:
        log("启动失败:\n" + traceback.format_exc())
        QMessageBox.critical(None, "AI 游戏陪玩", "启动失败：\n" + traceback.format_exc()[-800:])
        return 1
    log(f"启动完成 PID={os.getpid()}")
    code = app.exec()
    log("退出")
    return code


if __name__ == "__main__":
    sys.exit(main())
