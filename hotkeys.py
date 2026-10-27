# -*- coding: utf-8 -*-
"""全局热键（Windows 原生 RegisterHotKey）。

为什么不用 keyboard 库：它的低层键盘钩子在这台机器的全屏游戏面前完全收不到事件
（日志显示注册"成功"但一次都没触发）。RegisterHotKey 是系统级注册，消息直接投递到
我们的消息循环，全屏游戏、任何焦点状态下都能收到，而且**不需要管理员权限**。

实现：单独一个线程注册热键 + 跑消息循环，收到 WM_HOTKEY 就回调。
回调线程不是 Qt 主线程，所以调用方必须自己切回主线程（main.py 里用 QTimer.singleShot）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

MOD_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "cmd": MOD_WIN,
}

VK_NAMES = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pagedown": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "pause": 0x13, "capslock": 0x14,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _i in range(1, 25):                      # F1 ~ F24
    VK_NAMES["f%d" % _i] = 0x6F + _i
for _c in "0123456789":
    VK_NAMES[_c] = ord(_c)
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_NAMES[_c.lower()] = ord(_c)


def parse_hotkey(combo: str) -> tuple[int, int]:
    """'f8' / 'ctrl+alt+s' -> (modifiers, vk)。解析不了就抛 ValueError。"""
    mods = 0
    vk = None
    for part in str(combo).lower().replace(" ", "").split("+"):
        if not part:
            continue
        if part in MOD_NAMES:
            mods |= MOD_NAMES[part]
        elif part in VK_NAMES:
            vk = VK_NAMES[part]
        else:
            raise ValueError(f"认不出的按键：{part}")
    if vk is None:
        raise ValueError(f"热键里没有主键：{combo}")
    return mods, vk


def is_combo_free(combo: str) -> bool:
    """这个键现在能不能注册（注册后立刻释放）。用来挑备选键。"""
    try:
        mods, vk = parse_hotkey(combo)
    except ValueError:
        return False
    probe_id = 0xE000
    if user32.RegisterHotKey(None, probe_id, mods, vk):
        user32.UnregisterHotKey(None, probe_id)
        return True
    return False


class GlobalHotkeys:
    """注册一组系统级热键；run() 在独立线程里跑消息循环。"""

    def __init__(self, callbacks: dict[str, Callable[[], None]], log: Callable[[str], None] = print):
        self.callbacks = dict(callbacks)
        self.log = log
        self.failures: dict[str, str] = {}
        self.registered: dict[str, int] = {}      # combo -> hotkey id
        self.thread_id: int | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # ------------------------------------------------------------------ 注册
    def run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        next_id = 0xA000
        for combo, _callback in self.callbacks.items():
            try:
                mods, vk = parse_hotkey(combo)
            except ValueError as exc:
                self.failures[combo] = str(exc)
                self.log(f"热键 {combo} 解析失败：{exc}")
                continue
            hk_id = next_id
            next_id += 1
            ok = user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk)
            if not ok:
                err = kernel32.GetLastError()
                ok = user32.RegisterHotKey(None, hk_id, mods, vk)   # 老系统不认 NOREPEAT
                if not ok:
                    self.failures[combo] = f"错误码 {err}（多半被别的软件占用了）"
                    self.log(f"热键 {combo} 注册失败：错误码 {err}")
                    continue
            self.registered[combo] = hk_id
            self.log(f"热键 {combo} 注册成功（id={hk_id}）")
        self._ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                combo = None
                for name, hid in self.registered.items():
                    if hid == int(msg.wParam):
                        combo = name
                        break
                if combo:
                    try:
                        self.callbacks[combo]()
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"热键 {combo} 回调出错：{exc}")
        for hid in self.registered.values():
            user32.UnregisterHotKey(None, hid)

    def start(self, timeout: float = 3.0) -> "GlobalHotkeys":
        self._thread = threading.Thread(target=self.run, name="global-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)

    # ------------------------------------------------------------------ 自测
    def simulate(self, combo: str) -> bool:
        """给自己的消息循环发一条 WM_HOTKEY，用来在没人按键的情况下验证回调链路。"""
        hid = self.registered.get(combo)
        if not self.thread_id or hid is None:
            return False
        return bool(user32.PostThreadMessageW(self.thread_id, WM_HOTKEY, hid, 0))


if __name__ == "__main__":
    import time

    fired: list[str] = []
    hk = GlobalHotkeys({"f8": lambda: fired.append("f8"), "f11": lambda: fired.append("f11")}, log=print)
    hk.start()
    time.sleep(0.4)
    print("注册失败项:", hk.failures or "无")
    print("现在按 F8 / F11 试试（5 秒）…")
    time.sleep(5)
    print("收到的触发:", fired or "没有（可能是没人按键）")
    hk.stop()
