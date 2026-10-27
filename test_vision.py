# -*- coding: utf-8 -*-
"""用真实游戏截图测试「看图读牌 → 给建议」的准确度。

    python test_vision.py 图片路径 [问题]
    python test_vision.py a.jpg b.jpg       # 多张依次测
"""
from __future__ import annotations

import base64
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

import prompts  # noqa: E402
import tray_config as cfg_mod  # noqa: E402
from llm import VisionClient, find_api_key  # noqa: E402

DEFAULT_Q = (
    "看这张《杀戮尖塔》截图，用你自己的话念一遍你看到的关键信息："
    "（1）对手是谁、什么意图、多少血；（2）我手里有哪些牌、每张几费；"
    "（3）我的能量/血量/格挡；（4）有哪些遗物或药水。"
    "然后给这一回合该怎么打。看不清的直说，不要编。"
)


def prep(path: str, max_side: int = 1280, quality: int = 72) -> tuple[str, str]:
    img = Image.open(path).convert("RGB")
    original = img.size
    if max(img.size) > max_side:
        scale = max_side / float(max(img.size))
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    data = buf.getvalue()
    return (
        "data:image/jpeg;base64," + base64.b64encode(data).decode(),
        f"{path} 原图{original[0]}x{original[1]} → 发送{img.size[0]}x{img.size[1]} {len(data)//1024}KB",
    )


def main() -> int:
    paths = [p for p in sys.argv[1:] if os.path.isfile(p)]
    question = DEFAULT_Q
    if not paths:
        print("用法: python test_vision.py 图片 [图片...]")
        return 2
    key, src = find_api_key(cfg_mod.API.get("api_key", ""), [os.path.dirname(os.path.abspath(__file__))])
    client = VisionClient(cfg_mod.API, key)
    print(f"模型 {client.model} · key 来源 {src} · reasoning_effort={client.reasoning_effort}")
    for path in paths:
        url, info = prep(path)
        print("\n" + "=" * 70)
        print(info)
        messages = [
            {"role": "system", "content": prompts.get("slay_the_spire")},
            VisionClient.build_user_message(question, url),
        ]
        t0 = time.time()
        try:
            out = client.stream_chat(messages)
        except Exception as exc:  # noqa: BLE001
            print("失败:", exc)
            continue
        print("-" * 70)
        print(out["text"].strip())
        usage = out["usage"] or {}
        print("-" * 70)
        print(
            "耗时 %.1fs（首字 %.1fs）· prompt %s + completion %s = %s tok · 其中思考 %s tok"
            % (
                out["elapsed"], out["ttfb"],
                usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"),
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
