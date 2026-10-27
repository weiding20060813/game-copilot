# -*- coding: utf-8 -*-
"""可拖动的置顶悬浮对话框（PySide6）。

特点：
  * 无边框 / 半透明 / 可拖动 / 可缩放 / 可隐藏标题栏
  * 鼠标穿透模式（点得到底下的游戏）
  * 流式打字：AI 一边生成一边显示
  * 支持多显示器、记忆窗口位置
"""
from __future__ import annotations

import html
import re
from datetime import datetime

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizeGrip,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------------
# Windows 原生窗口技巧：让悬浮窗"点得到但抢不走焦点"
#
# 游戏玩家最烦的就是点一下助手窗口，游戏就掉到后台（键盘鼠标全失效）。
# WS_EX_NOACTIVATE 能让窗口永远不成为前台窗口，但鼠标点击照样落在控件上。
# 配合 WM_MOUSEACTIVATE 返回 MA_NOACTIVATE 双保险。
# --------------------------------------------------------------------------
import ctypes
import sys

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3

# Qt 原生事件的数值区间（QEvent::NativeMouseMove .. QEvent::NativeGesture）
TYPE_NATIVE_MOUSEMOVE = 154
TYPE_NATIVE_GESTURE = 160

_IS_WIN = sys.platform == "win32"
if _IS_WIN:
    _user32 = ctypes.windll.user32
    try:
        _GetWindowLongPtr = _user32.GetWindowLongPtrW
        _SetWindowLongPtr = _user32.SetWindowLongPtrW
    except AttributeError:              # 32 位 Python 没有 Ptr 版本
        _GetWindowLongPtr = _user32.GetWindowLongW
        _SetWindowLongPtr = _user32.SetWindowLongW
    try:
        _SetWindowLongPtr.restype = ctypes.c_void_p
        _GetWindowLongPtr.restype = ctypes.c_void_p
    except Exception:
        pass
else:
    _user32 = None


def apply_no_activate(hwnd: int, enable: bool = True) -> int:
    """给窗口加上/去掉 WS_EX_NOACTIVATE，返回修改后的扩展样式。"""
    if not _IS_WIN or not hwnd:
        return 0
    style = int(_GetWindowLongPtr(hwnd, GWL_EXSTYLE) or 0)
    new_style = (style | WS_EX_NOACTIVATE) if enable else (style & ~WS_EX_NOACTIVATE)
    if new_style != style:
        _SetWindowLongPtr(hwnd, GWL_EXSTYLE, new_style)
    return new_style


ROLE_STYLE = {
    "user": ("#1d4ed8", "#eaf2ff"),
    "ai": ("#232a36", "#e6e9ef"),
    "sys": ("#1a1f2b", "#9aa7bd"),
    "err": ("#4a1f24", "#ffb4b4"),
}


def _md_lite(text: str) -> str:
    """极简 Markdown：转义 HTML，保留换行，处理 **粗体** 和 `代码`。"""
    out = html.escape(text or "")
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return out.replace("\n", "<br>")


class Overlay(QWidget):
    submitted = Signal(str)          # 用户发了消息（纯文字，不带图）
    analyze_requested = Signal(str)  # 用户要求「看屏幕」并附带一句话
    stop_requested = Signal()
    watch_toggled = Signal(bool)
    clear_requested = Signal()
    closed = Signal()
    geometry_changed = Signal()

    def __init__(self, cfg: dict, subtitle: str = ""):
        super().__init__()
        self.cfg = dict(cfg)
        self._blocks: list[dict] = []       # 聊天记录（渲染用）
        self._streaming: dict | None = None  # 正在流式输出的块
        self._drag_offset: QPoint | None = None
        self._resize_from: QPoint | None = None
        self._start_geo: QRect | None = None
        self._busy = False
        self._watching = False
        self._click_through = False

        self.setWindowTitle("AI 游戏陪玩")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(320, 220)
        self.resize(int(self.cfg.get("width", 420)), int(self.cfg.get("height", 560)))

        if self.cfg.get("font_family"):
            self.setFont(QFont(self.cfg["font_family"], int(self.cfg.get("font_size", 13)) // 2 + 6))

        self._build_ui(subtitle)
        self._apply_style()

        # 界面建完再装 app 级事件过滤器 + 定时重贴"不抢焦点"样式
        self.installEventFilter(self)
        self._noact_timer = QTimer(self)
        self._noact_timer.setInterval(3000)
        self._noact_timer.timeout.connect(self._reassert_no_activate)
        self._noact_timer.start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self, subtitle: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ---------- 标题栏（拖这里移动窗口） ----------
        self.title_bar = QFrame()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(30)
        tb = QHBoxLayout(self.title_bar)
        tb.setContentsMargins(6, 0, 4, 0)
        tb.setSpacing(6)

        dot = QLabel("●")
        dot.setObjectName("dot")
        self.title_label = QLabel("AI 游戏陪玩")
        self.title_label.setObjectName("titleLabel")
        hint = QLabel(subtitle)
        hint.setObjectName("hintLabel")

        self.btn_min = QPushButton("—")
        self.btn_min.setObjectName("chipBtn")
        self.btn_min.setFixedSize(22, 22)
        self.btn_min.setToolTip("收起标题栏（再按一次展开）")
        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("chipBtn")
        self.btn_close.setFixedSize(22, 22)
        self.btn_close.setToolTip("隐藏窗口（热键或托盘可再打开）")

        tb.addWidget(dot)
        tb.addWidget(self.title_label)
        tb.addWidget(hint)
        tb.addStretch(1)
        tb.addWidget(self.btn_min)
        tb.addWidget(self.btn_close)
        root.addWidget(self.title_bar)

        # ---------- 对话区 ----------
        self.view = QTextBrowser()
        self.view.setObjectName("chatView")
        self.view.setOpenExternalLinks(False)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self.view, 1)

        # ---------- 工具条 ----------
        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.btn_shot = QPushButton("📷 看屏幕")
        self.btn_shot.setObjectName("toolBtn")
        self.btn_shot.setToolTip("立刻截屏分析（热键 F8）")
        self.btn_watch = QPushButton("▶ 连续陪玩")
        self.btn_watch.setObjectName("toolBtn")
        self.btn_watch.setCheckable(True)
        self.btn_watch.setToolTip("每隔几秒自动看一次屏（热键 F10）")
        self.btn_stop = QPushButton("■ 停止")
        self.btn_stop.setObjectName("toolBtn")
        self.btn_stop.setEnabled(False)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("toolBtn")
        self.btn_noact = QPushButton("🔒 只读模式")
        self.btn_noact.setObjectName("toolBtn")
        self.btn_noact.setCheckable(True)
        self.btn_noact.setChecked(True)
        self.btn_noact.setToolTip(
            "只读模式（默认）：我点不中，但游戏永远不会掉到后台，AI 建议实时显示\n"
            "交互模式：能点按钮和打字，代价是点我游戏会掉到后台\n"
            "切换方式：按 F7，或右键任务栏托盘图标"
        )
        self.btn_through = QPushButton("穿透")
        self.btn_through.setObjectName("toolBtn")
        self.btn_through.setCheckable(True)
        self.btn_through.setToolTip("鼠标穿透：点击直接落到游戏上（热键 F11）")
        for b in (self.btn_shot, self.btn_watch, self.btn_stop, self.btn_clear,
                  self.btn_noact, self.btn_through):
            tools.addWidget(b)
        root.addLayout(tools)

        # ---------- 输入区 ----------
        row = QHBoxLayout()
        row.setSpacing(6)
        self.input = QLineEdit()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText("打字问我，回车发送…")
        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("sendBtn")
        self.btn_send.setFixedWidth(64)
        row.addWidget(self.input, 1)
        row.addWidget(self.btn_send)
        root.addLayout(row)

        # ---------- 状态 + 缩放手柄 ----------
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("就绪")
        self.status.setObjectName("statusLabel")
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        root.addLayout(bottom)

        # ---------- 事件 ----------
        self.btn_send.clicked.connect(self._on_send)
        self.input.returnPressed.connect(self._on_send)
        self.btn_shot.clicked.connect(lambda: self.analyze_requested.emit(self.input.text().strip()))
        self.btn_watch.toggled.connect(self._on_watch_toggled)
        self.btn_stop.clicked.connect(self.stop_requested.emit)
        self.btn_clear.clicked.connect(self.clear_requested.emit)
        self.btn_through.toggled.connect(self.set_click_through)
        self.btn_noact.toggled.connect(self.set_no_activate)
        self.btn_min.clicked.connect(self._toggle_title_collapse)
        self.btn_close.clicked.connect(self._on_close)
        self.title_bar.installEventFilter(self)
        # 注意：app 级事件过滤器等界面全部建好再装，避免构造期间递归触发
        self._no_activate = True

    def _apply_style(self) -> None:
        c = self.cfg
        fs = int(c.get("font_size", 13))
        self.setStyleSheet(f"""
        QWidget {{ color: {c['fg']}; }}
        #titleBar {{ background: rgba(255,255,255,0.05); border-radius: 8px; }}
        #titleLabel {{ font-size: {fs}px; font-weight: 600; color: {c['fg']}; }}
        #hintLabel {{ font-size: {max(9, fs-3)}px; color: #8b97ac; }}
        #dot {{ color: #45d67d; font-size: {fs}px; }}
        #chatView {{
            background: rgba(8,10,16,0.55);
            border: 1px solid rgba(255,255,255,0.07);
            border-radius: 10px;
            padding: 8px;
            font-size: {fs}px;
        }}
        QPushButton {{
            background: rgba(255,255,255,0.07);
            border: 1px solid rgba(255,255,255,0.10);
            border-radius: 7px;
            padding: 4px 8px;
            font-size: {max(10, fs-2)}px;
            color: {c['fg']};
        }}
        QPushButton:hover {{ background: rgba(255,255,255,0.14); }}
        QPushButton:checked {{ background: {c['accent']}; color: #06121f; border-color: {c['accent']}; }}
        QPushButton:disabled {{ color: #6d788c; }}
        #sendBtn {{ background: {c['accent']}; color: #06121f; font-weight: 600; }}
        #chatInput {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
            padding: 6px 8px;
            font-size: {fs}px;
            selection-background-color: {c['accent']};
        }}
        #chatInput:focus {{ border-color: {c['accent']}; }}
        #statusLabel {{ color: #8b97ac; font-size: {max(9, fs-3)}px; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 4px; min-height: 24px; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
        """)

    # ------------------------------------------------------------- 绘制背景
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(self.cfg.get("bg", "#12161f")))
        alpha = int(max(0.3, min(1.0, float(self.cfg.get("opacity", 0.93)))) * 255)
        col = QColor(self.cfg.get("bg", "#12161f"))
        col.setAlpha(alpha)
        painter.setBrush(col)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.drawRoundedRect(rect, 12, 12)

    # --------------------------------------------------------------- 聊天渲染
    def _render(self) -> None:
        parts = []
        for blk in self._blocks:
            role = blk.get("role", "ai")
            bg, fg = ROLE_STYLE.get(role, ROLE_STYLE["ai"])
            body = _md_lite(blk.get("text", ""))
            if role == "ai" and blk.get("reasoning"):
                body = (
                    '<div style="color:#8b97ac;font-size:11px;line-height:1.35;'
                    'border-left:2px solid #3a4256;padding-left:6px;margin-bottom:6px;">'
                    f'💭 {_md_lite(blk["reasoning"])[:400]}</div>' + body
                )
            meta = blk.get("meta", "")
            meta_html = (
                f'<div style="color:#7c8798;font-size:10px;margin-top:3px;">{html.escape(meta)}</div>'
                if meta else ""
            )
            cursor = "▌" if blk.get("streaming") else ""
            parts.append(
                f'<div style="background:{bg};border-radius:9px;padding:7px 9px;margin:4px 0;'
                f'line-height:1.5;">{body}{cursor}{meta_html}</div>'
            )
        doc = "".join(parts) or (
            '<div style="color:#7c8798;font-size:12px;padding:10px;">'
            '按 <b>F8</b> 让我看一眼当前画面；在下面输入框里打字可以随时问我。<br>'
            '把游戏改成 <b>无边框窗口</b> 模式，我就能一直浮在画面上。</div>'
        )
        self.view.setHtml(f'<div style="font-family:inherit;">{doc}</div>')
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------ 追加
    def add_message(self, role: str, text: str, meta: str = "", reasoning: str = "") -> dict:
        blk = {"role": role, "text": text, "meta": meta, "reasoning": reasoning}
        self._blocks.append(blk)
        self._render()
        return blk

    def begin_stream(self, meta_prefix: str = "") -> None:
        self._streaming = {"role": "ai", "text": "", "reasoning": "", "meta": "", "streaming": True}
        self._blocks.append(self._streaming)
        self._render()

    def push_stream_text(self, piece: str) -> None:
        if self._streaming is None:
            self.begin_stream()
        self._streaming["text"] += piece
        self._render()

    def push_stream_reasoning(self, piece: str) -> None:
        if self._streaming is None:
            self.begin_stream()
        self._streaming["reasoning"] += piece
        if self.cfg.get("show_reasoning", True):
            self._render()

    def end_stream(self, meta: str = "") -> None:
        if self._streaming is None:
            return
        self._streaming["streaming"] = False
        self._streaming["meta"] = meta
        if not self._streaming["text"].strip():
            self._streaming["text"] = "（这次没有输出内容）"
        if not self.cfg.get("show_reasoning", True):
            self._streaming["reasoning"] = ""
        self._streaming = None
        self._render()

    def clear_chat(self) -> None:
        self._blocks.clear()
        self._streaming = None
        self._render()

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_stop.setEnabled(busy)
        self.btn_shot.setEnabled(not busy)
        if not busy:
            self.btn_shot.setText("📷 看屏幕")

    # ------------------------------------------------------------------ 交互
    def _on_send(self) -> None:
        text = self.input.text().strip()
        if not text or self._busy:
            return
        self.input.clear()
        self.submitted.emit(text)

    def _on_watch_toggled(self, on: bool) -> None:
        self._watching = on
        self.btn_watch.setText("⏸ 连续中" if on else "▶ 连续陪玩")
        self.watch_toggled.emit(on)

    def set_watch_state(self, on: bool) -> None:
        self.btn_watch.blockSignals(True)
        self.btn_watch.setChecked(on)
        self.btn_watch.setText("⏸ 连续中" if on else "▶ 连续陪玩")
        self.btn_watch.blockSignals(False)
        self._watching = on

    def set_click_through(self, on: bool) -> None:
        self._click_through = bool(on)
        self.btn_through.blockSignals(True)
        self.btn_through.setChecked(self._click_through)
        self.btn_through.blockSignals(False)
        if self._click_through:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
            self.show()  # 改 flag 后需要重新 show
            self.set_status("穿透中：点不中这个窗口，按 F11 恢复")
        else:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, False)
            self.show()
            self.set_status("已恢复鼠标操作")
        self._apply_no_activate()

    # ------------------------------------------------------- 工作模式（核心）
    #
    # WS_EX_NOACTIVATE 是操作系统层面的"不激活"：窗口收不到鼠标输入，但也永远不会
    # 把游戏顶到后台。所以两种模式天生互斥，用热键/托盘切换：
    #   只读模式（默认）：F8 看屏幕，AI 的建议实时显示，游戏永远不掉后台
    #   交互模式：窗口可点可打字，代价是点它游戏会掉后台
    def set_no_activate(self, on: bool) -> None:
        self._no_activate = bool(on)
        self.btn_noact.blockSignals(True)
        self.btn_noact.setChecked(self._no_activate)
        self.btn_noact.blockSignals(False)
        self.btn_noact.setText("🔒 只读模式" if self._no_activate else "🔓 交互模式")
        self._apply_no_activate()
        if self._no_activate:
            self.set_status("只读模式：点不中我，游戏不会掉后台 · 按 F7 切换成交互模式")
        else:
            self.set_status("交互模式：可以点按钮、打字了 · 按 F7 切回只读模式")

    def set_interactive(self, on: bool) -> None:
        """给热键/托盘用的入口：True = 可交互（会抢焦点）。"""
        self.set_no_activate(not on)

    def _apply_no_activate(self) -> None:
        """把 WS_EX_NOACTIVATE 贴到窗口上（Qt 改窗口 flag 会重建原生窗口，所以要重贴）。"""
        if not hasattr(self, "_no_activate"):
            self._no_activate = True
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        apply_no_activate(hwnd, self._no_activate)

    def _reassert_no_activate(self) -> None:
        """定时重贴一次：万一被别的操作刷掉了，能自己恢复。"""
        if getattr(self, "_no_activate", True) and self.isVisible():
            self._apply_no_activate()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_no_activate()

    def toggle_topmost(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.show()
        self._apply_no_activate()
        if on:
            self._apply_no_activate()

    def _toggle_title_collapse(self) -> None:
        """收起/展开标题栏（收起后仍可用工具条的按钮，热键照常работа）。"""
        self._collapsed = not getattr(self, "_collapsed", False)
        for widget in (self.title_label, self.btn_min, self.btn_close):
            widget.setVisible(not self._collapsed)
        self.title_bar.setFixedHeight(12 if self._collapsed else 30)
        self.btn_min.setText("▢" if self._collapsed else "—")

    def _on_close(self) -> None:
        self.closed.emit()

    # ---------------------------------------------------------------- 拖动/缩放
    def eventFilter(self, obj, event):  # noqa: N802
        # 重要：Qt 的原生事件是 154~160 这个数值区间（没有 QEvent.Type.Native 这个枚举，
        # 早期版本误用它导致过滤器抛 AttributeError，进而弄脏 Python 异常状态、
        # 让后续 QTimer 创建失败）。所以这里用数值区间判断，并且**绝不抛异常**。
        try:
            etype = event.type()
            if TYPE_NATIVE_MOUSEMOVE <= int(etype) <= TYPE_NATIVE_GESTURE:
                # 原生事件：阻止"点击窗口 → 游戏掉到后台"
                if obj is self and getattr(self, "_no_activate", True):
                    if int(event.nativeEventType()) == WM_MOUSEACTIVATE:
                        event.setResult(MA_NOACTIVATE)
                        return True
                return False
        except Exception:
            return False

        if obj is self.title_bar:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event.type() == QEvent.Type.MouseMove and self._drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
                self.geometry_changed.emit()
                return True
        return False

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 右下角 18px 区域用来缩放
        if event.button() == Qt.MouseButton.LeftButton and self._near_grip(event.position().toPoint()):
            self._resize_from = event.globalPosition().toPoint()
            self._start_geo = self.geometry()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resize_from is not None and self._start_geo is not None:
            delta = event.globalPosition().toPoint() - self._resize_from
            w = max(self.minimumWidth(), self._start_geo.width() + delta.x())
            h = max(self.minimumHeight(), self._start_geo.height() + delta.y())
            self.resize(QSize(w, h))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._resize_from is not None:
            self._resize_from = None
            self._start_geo = None
            self.geometry_changed.emit()
            return
        super().mouseReleaseEvent(event)

    def _near_grip(self, pos: QPoint) -> bool:
        return pos.x() > self.width() - 20 and pos.y() > self.height() - 20

    # ------------------------------------------------------------ 位置持久化
    def restore_position(self, pos: tuple[int, int] | None) -> None:
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        if pos:
            x, y = int(pos[0]), int(pos[1])
            x = max(area.left(), min(x, area.right() - 200))
            y = max(area.top(), min(y, area.bottom() - 120))
            self.move(x, y)
        else:
            self.move(max(area.left(), area.right() - self.width() - 24), area.top() + 60)

    def current_position(self) -> tuple[int, int]:
        p = self.pos()
        return (p.x(), p.y())


def create_app() -> QApplication:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("AI 游戏陪玩")
    app.setQuitOnLastWindowClosed(False)
    return app


if __name__ == "__main__":
    import sys
    from tray_config import WINDOW

    app = create_app()
    win = Overlay(WINDOW, subtitle="· 自检")
    win.restore_position(None)
    win.add_message("sys", "悬浮窗自检：拖动我试试。")
    win.add_message("user", "这回合我该打哪张牌？")
    win.begin_stream()
    win.push_stream_reasoning("先看意图……敌人 12 点攻击。")
    win.push_stream_text("先打**痛击**上易伤，再补一张打击。")
    win.end_stream(meta="1.2s · 780 tok")
    win.show()
    sys.exit(app.exec())
