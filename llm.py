# -*- coding: utf-8 -*-
"""视觉大模型流式客户端（只用标准库 urllib，不依赖 openai SDK）。

要点：
  * 支持 OpenAI 兼容的 /chat/completions，带 image_url（data:base64）
  * support SSE 流式输出，逐字回调
  * 兼容 deepseek 的 reasoning_content（思考过程）字段
  * 自动从环境变量 / apikey.txt / DSH 凭据文件里找 key
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, Iterable


# --------------------------------------------------------------------------
# API Key 解析：环境变量 -> apikey.txt -> 配置 -> DSH 凭据
# --------------------------------------------------------------------------
_DSH_CRED = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")


def find_api_key(explicit: str = "", search_dirs: Iterable[str] = ()) -> tuple[str, str]:
    """返回 (key, 来源描述)。"""
    if explicit and explicit.strip():
        return explicit.strip(), "配置文件"
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env, "环境变量 DEEPSEEK_API_KEY"
    for folder in list(search_dirs) + [os.path.dirname(os.path.abspath(__file__))]:
        path = os.path.join(folder, "apikey.txt")
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig") as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
            if lines:
                return lines[0], f"apikey.txt ({path})"
    if os.path.isfile(_DSH_CRED):
        try:
            with open(_DSH_CRED, "r", encoding="utf-8") as fh:
                text = fh.read()
            m = re.search(r"DEEPSEEK_API_KEY\s*:\s*(\S+)", text)
            if m:
                return m.group(1).strip().strip("'\""), "DSH 登录凭据"
        except Exception:
            pass
    return "", "未找到"


# --------------------------------------------------------------------------
class LLMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class VisionClient:
    """一个极简的流式视觉对话客户端。"""

    def __init__(self, cfg: dict, api_key: str = ""):
        self.cfg = dict(cfg)
        self.base_url = self.cfg.get("base_url", "https://api.deepseek.com").rstrip("/")
        self.path = self.cfg.get("path", "/chat/completions")
        self.model = self.cfg.get("model", "deepseek-v4-flash-vision-exp")
        self.max_tokens = int(self.cfg.get("max_tokens", 900))
        self.temperature = float(self.cfg.get("temperature", 0.4))
        self.timeout = int(self.cfg.get("timeout", 180))
        self.reasoning_effort = str(self.cfg.get("reasoning_effort", "") or "").strip()
        self.supports_reasoning_effort = bool(self.reasoning_effort)  # 若接口不认这个参数则自动关掉
        self.api_key = api_key
        self._cancel = threading.Event()
        self.last_usage: dict = {}

    # ------------------------------------------------------------------ 内部
    @property
    def url(self) -> str:
        return self.base_url + self.path

    def cancel(self) -> None:
        self._cancel.set()

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if self.reasoning_effort and self.supports_reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return body

    # ---------------------------------------------------------------- 主接口
    def stream_chat(
        self,
        messages: list[dict],
        on_text: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> dict:
        """流式对话。返回 {'text','reasoning','usage','elapsed','ttfb'}。"""
        if not self.api_key:
            raise LLMError("没有找到 API Key：请设置环境变量 DEEPSEEK_API_KEY，或在程序目录放 apikey.txt")
        self._cancel.clear()
        body = self._payload(messages, stream=True)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=data,
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "game-copilot/1.0",
            },
            method="POST",
        )
        started = time.perf_counter()
        text_parts: list[str] = []
        reason_parts: list[str] = []
        usage: dict = {}
        ttfb = 0.0
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:
                    if self._cancel.is_set():
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            if not ttfb:
                                ttfb = time.perf_counter() - started
                            text_parts.append(piece)
                            if on_text:
                                on_text(piece)
                        think = delta.get("reasoning_content") or delta.get("reasoning")
                        if think:
                            if not ttfb:
                                ttfb = time.perf_counter() - started
                            reason_parts.append(think)
                            if on_reasoning:
                                on_reasoning(think)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            if exc.code == 400 and "reasoning_effort" in raw and self.supports_reasoning_effort:
                # 这个接口不认 reasoning_effort，关掉重来一次
                self.supports_reasoning_effort = False
                return self.stream_chat(messages, on_text, on_reasoning)
            raise LLMError(self._explain_http(exc.code, raw), exc.code, raw) from None
        except urllib.error.URLError as exc:
            raise LLMError(f"网络连不上 {self.url}：{exc.reason}") from None
        except TimeoutError:
            raise LLMError(f"请求超时（>{self.timeout}s），可调大 config.py 里的 timeout") from None

        self.last_usage = usage
        text = "".join(text_parts)
        reasoning = "".join(reason_parts)
        truncated = False
        if not text.strip() and reasoning.strip():
            # 思考把 token 预算吃光了，正文没来得及输出
            truncated = True
            text = (
                "⚠️ 这次只输出了思考、没输出答案：模型把 token 预算全花在“想”上了。\n"
                "解决办法：把 config.py 里的 max_tokens 调大（比如 3500），"
                "或把 reasoning_effort 设为 none。"
            )
        return {
            "text": text,
            "reasoning": reasoning,
            "usage": usage,
            "elapsed": time.perf_counter() - started,
            "ttfb": ttfb,
            "truncated": truncated,
        }

    def chat(self, messages: list[dict]) -> dict:
        """非流式（备用/测试）。"""
        if not self.api_key:
            raise LLMError("没有找到 API Key")
        body = self._payload(messages, stream=False)
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "game-copilot/1.0",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                obj = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise LLMError(self._explain_http(exc.code, raw), exc.code, raw) from None
        except urllib.error.URLError as exc:
            raise LLMError(f"网络连不上 {self.url}：{exc.reason}") from None
        msg = (obj.get("choices") or [{}])[0].get("message") or {}
        return {
            "text": msg.get("content") or "",
            "reasoning": msg.get("reasoning_content") or "",
            "usage": obj.get("usage") or {},
            "elapsed": time.perf_counter() - started,
            "ttfb": 0.0,
        }

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _explain_http(code: int, raw: str) -> str:
        detail = ""
        try:
            detail = (json.loads(raw).get("error") or {}).get("message", "")
        except Exception:
            detail = raw[:200]
        friendly = {
            401: "API Key 无效或过期",
            402: "账户余额不足",
            429: "请求太频繁（连续陪玩间隔调大一些，或降频）",
            400: "请求参数有问题",
            404: "模型名或接口地址不对",
        }.get(code, "")
        return f"HTTP {code} {friendly} :: {detail[:300]}"

    @staticmethod
    def build_user_message(text: str, image_data_url: str | None = None) -> dict:
        if not image_data_url:
            return {"role": "user", "content": text}
        return {
            "role": "user",
            "content": [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ],
        }


# --------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import tray_config as cfg_mod
    import screen as screen_mod

    key, src = find_api_key(cfg_mod.API.get("api_key", ""))
    print("API Key:", ("找到(" + src + ")") if key else "没找到")
    client = VisionClient(cfg_mod.API, key)

    url, meta = screen_mod.grab_data_url(
        cfg_mod.SCREEN["monitor"], cfg_mod.SCREEN["max_side"], cfg_mod.SCREEN["jpeg_quality"]
    )
    print("截图:", meta)

    msgs = [
        {"role": "system", "content": "你是一个中文游戏助手，回答简短，30字以内。"},
        VisionClient.build_user_message("这张截图里是什么？", url),
    ]
    print("--- 流式输出 ---")
    out = client.stream_chat(msgs, on_text=lambda s: (sys.stdout.write(s), sys.stdout.flush()))
    print()
    print("--- 结果 ---")
    print("耗时: %.1fs  首字: %.1fs" % (out["elapsed"], out["ttfb"]))
    print("usage:", out["usage"])
    print("文本:", out["text"][:300])
