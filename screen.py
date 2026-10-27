# -*- coding: utf-8 -*-
"""抓屏模块：DPI 感知 + 原生分辨率抓帧 + 压缩成适合视觉模型的 JPEG。

纯标准库 + mss + Pillow，不依赖 Qt，可单独测试：
    python screen.py            # 抓一帧存到 _selftest_shot.jpg
"""
from __future__ import annotations

import base64
import ctypes
import io
import os
import sys
import threading
import time

import mss
from PIL import Image


# --------------------------------------------------------------------------
# DPI：不做这一步，2560x1600 / 150% 缩放的屏幕上会只抓到 1707x1067，画面发虚
# --------------------------------------------------------------------------
def enable_dpi_awareness() -> str:
    if sys.platform != "win32":
        return "non-windows"
    try:  # Win10 1703+ 每显示器 DPI 感知
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


enable_dpi_awareness()


class ScreenGrabber:
    """线程安全的抓屏器（内部复用 mss 实例，避免反复初始化）。"""

    def __init__(self, monitor: int = 1, max_side: int = 1280, jpeg_quality: int = 72):
        self.monitor_index = max(1, int(monitor))
        self.max_side = int(max_side)
        self.jpeg_quality = int(jpeg_quality)
        self._local = threading.local()

    # ---------------------------------------------------------------- 基础
    def _sct(self) -> "mss.base.MSSBase":
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._local.sct = sct
        return sct

    def monitors(self) -> list[dict]:
        return list(self._sct().monitors)

    def monitor_rect(self) -> dict:
        mons = self.monitors()
        idx = self.monitor_index if self.monitor_index < len(mons) else 1
        return dict(mons[idx])

    def close(self) -> None:
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            self._local.sct = None

    # ---------------------------------------------------------------- 抓帧
    def grab_image(self, region: dict | None = None, retries: int = 3) -> Image.Image:
        """抓一帧并返回 PIL Image（RGB）。region 为 {'left','top','width','height'}。"""
        rect = region or self.monitor_rect()
        last_err: Exception | None = None
        for _ in range(max(1, retries)):
            try:
                raw = self._sct().grab(rect)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                return img
            except Exception as exc:  # 分辨率切换/独占全屏瞬间可能失败，重试
                last_err = exc
                self.close()
                time.sleep(0.15)
        raise RuntimeError(f"抓屏失败：{last_err}")

    # ------------------------------------------------------------ 压缩编码
    def encode(self, img: Image.Image) -> tuple[bytes, tuple[int, int], tuple[int, int]]:
        """缩放 + 存 JPEG。返回 (jpeg_bytes, 原始尺寸, 缩放后尺寸)。"""
        original = img.size
        if self.max_side and max(img.size) > self.max_side:
            scale = self.max_side / float(max(img.size))
            new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
            img = img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=self.jpeg_quality, optimize=False)
        return buf.getvalue(), original, img.size

    def grab_jpeg(self, region: dict | None = None) -> tuple[bytes, tuple[int, int], tuple[int, int], float]:
        """一步到位：抓帧 -> JPEG。返回 (bytes, 原尺寸, 压缩后尺寸, 耗时秒)。"""
        t0 = time.perf_counter()
        img = self.grab_image(region)
        data, original, sent = self.encode(img)
        return data, original, sent, time.perf_counter() - t0

    @staticmethod
    def to_data_url(jpeg: bytes) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def grab_data_url(monitor: int = 1, max_side: int = 1280, quality: int = 72) -> tuple[str, dict]:
    """便捷函数：直接拿到可以塞进 messages 的 data URL。"""
    g = ScreenGrabber(monitor, max_side, quality)
    try:
        jpeg, original, sent, cost = g.grab_jpeg()
        return ScreenGrabber.to_data_url(jpeg), {
            "original": original, "sent": sent, "bytes": len(jpeg), "seconds": round(cost, 3),
        }
    finally:
        g.close()


if __name__ == "__main__":
    info = {"dpi": enable_dpi_awareness()}
    g = ScreenGrabber()
    info["monitors"] = g.monitors()
    url, meta = grab_data_url()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_selftest_shot.jpg")
    with open(out, "wb") as fh:
        fh.write(base64.b64decode(url.split(",", 1)[1]))
    print("DPI:", info["dpi"])
    print("monitors:", info["monitors"])
    print("抓帧结果:", meta)
    print("已保存:", out)
