# -*- coding: utf-8 -*-
"""合成测试：自己画一张「杀戮尖塔中文界面」的假截图（数值全是已知的），
用它来量化模型读中文 UI 的准确率，并和 app 实际发送的分辨率做对比。

    python test_synthetic.py

产出：
    _synthetic_1280.jpg / _synthetic_1600.jpg   两张测试图
    控制台打印模型读数 vs 标准答案的命中情况
"""
from __future__ import annotations

import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

import prompts  # noqa: E402
import tray_config as cfg_mod  # noqa: E402
from llm import VisionClient, find_api_key  # noqa: E402

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------- 标准答案（画的时候用的值） -------------------------
TRUTH = {
    "能量": "3/3",
    "血量": "62/80",
    "格挡": "0",
    "金币": "142",
    "层数": "第 12 层",
    "敌人": [
        {"名": "颚虫", "血": "44/44", "意图": "攻击 11"},
        {"名": "酸液史莱姆(中)", "血": "28/32", "意图": "攻击 7 + 上1张黏液"},
    ],
    "手牌": [
        {"名": "打击", "费": 1, "描述": "造成 6 点伤害"},
        {"名": "打击", "费": 1, "描述": "造成 6 点伤害"},
        {"名": "痛击", "费": 2, "描述": "造成 8 点伤害，施加 2 层易伤"},
        {"名": "防御", "费": 1, "描述": "获得 5 点格挡"},
        {"名": "重锤", "费": 3, "描述": "造成 32 点伤害"},
    ],
    "遗物": ["燃烧之血", "破碎核心"],
    "药水": ["火焰药水"],
}


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _center(draw: ImageDraw.ImageDraw, box, text, font, fill):
    x0, y0, x1, y1 = box
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (x0 + (x1 - x0 - (right - left)) / 2 - left, y0 + (y1 - y0 - (bottom - top)) / 2 - top),
        text, font=font, fill=fill,
    )


def _card(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, card: dict, selected=False):
    """一张手牌：底色 + 费用圈 + 名称 + 描述。"""
    body = (34, 40, 58) if not selected else (52, 62, 88)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=body,
                           outline=(150, 180, 230) if selected else (110, 130, 170), width=3)
    # 费用（左上角圆）
    r = int(w * 0.13)
    draw.ellipse([x + 4, y + 4, x + 4 + r * 2, y + 4 + r * 2], fill=(30, 60, 120), outline=(190, 215, 255), width=2)
    _center(draw, (x + 4, y + 4, x + 4 + r * 2, y + 4 + r * 2), str(card["费"]), _font(int(r * 1.5)), (255, 255, 255))
    _center(draw, (x, y + int(h * 0.06), x + w, y + int(h * 0.22)), card["名"], _font(int(w * 0.19)), (255, 240, 200))
    # 描述自动折行
    font = _font(max(12, int(w * 0.12)))
    lines, cur = [], ""
    for ch in card["描述"]:
        if draw.textlength(cur + ch, font=font) > w - 14:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    lines.append(cur)
    for i, line in enumerate(lines[:4]):
        _center(draw, (x, y + int(h * 0.30) + i * int(w * 0.16), x + w, y + int(h * 0.30) + (i + 1) * int(w * 0.16)),
                line, font, (225, 232, 245))


def render(width: int = 1920, height: int = 1080) -> Image.Image:
    img = Image.new("RGB", (width, height), (26, 22, 34))
    d = ImageDraw.Draw(img)
    sx = width / 1920.0
    sy = height / 1080.0
    S = lambda v: int(v * min(sx, sy))  # noqa: E731

    # 背景装饰
    for i in range(0, height, S(80)):
        d.line([(0, i), (width, i)], fill=(32, 27, 42), width=2)
    d.rectangle([0, 0, width, S(64)], fill=(20, 18, 28))
    d.rectangle([0, height - S(30), width, height], fill=(20, 18, 28))

    # 顶部状态：血量 / 金币 / 药水 / 层数
    _center(d, (S(20), 0, S(200), S(64)), f"❤ {TRUTH['血量']}", _font(S(30)), (255, 120, 120))
    _center(d, (S(210), 0, S(340), S(64)), f"金币 {TRUTH['金币']}", _font(S(26)), (240, 220, 140))
    d.ellipse([S(360), S(16), S(392), S(48)], fill=(90, 60, 150), outline=(200, 180, 255), width=2)
    _center(d, (S(360), S(16), S(392), S(48)), "药", _font(S(18)), (255, 255, 255))
    _center(d, (S(820), 0, S(1100), S(64)), TRUTH["层数"], _font(S(26)), (225, 225, 235))
    _center(d, (S(1100), 0, S(1300), S(64)), f"格挡 {TRUTH['格挡']}", _font(S(24)), (170, 200, 255))

    # 遗物（左上竖排）
    for i, name in enumerate(TRUTH["遗物"]):
        y = S(90) + i * S(56)
        d.rounded_rectangle([S(20), y, S(52), y + S(32)], radius=6, fill=(70, 55, 40), outline=(200, 170, 110), width=2)
        d.text((S(60), y + S(2)), name, font=_font(S(20)), fill=(230, 210, 170))

    # 敌人
    ex = S(420)
    for i, enemy in enumerate(TRUTH["敌人"]):
        cx = ex + i * S(430)
        cy = S(300)
        # 血条 + 血量文字
        d.rectangle([cx, cy + S(150), cx + S(240), cy + S(170)], fill=(60, 20, 20))
        d.rectangle([cx, cy + S(150), cx + S(240), cy + S(170)], fill=(180, 50, 50))
        _center(d, (cx, cy + S(150), cx + S(240), cy + S(170)), enemy["血"], _font(S(18)), (255, 255, 255))
        # 意图：图标 + 数字
        d.ellipse([cx + S(90), cy - S(70), cx + S(150), cy - S(10)], fill=(140, 40, 40), outline=(255, 160, 160), width=2)
        _center(d, (cx + S(90), cy - S(70), cx + S(150), cy - S(10)), "⚔", _font(S(30)), (255, 230, 230))
        d.text((cx + S(60), cy - S(45)), enemy["意图"], font=_font(S(22)), fill=(255, 200, 120))
        d.text((cx, cy + S(178)), enemy["名"], font=_font(S(22)), fill=(235, 225, 225))
        # 身体
        d.rounded_rectangle([cx + S(40), cy, cx + S(200), cy + S(150)], radius=S(30), fill=(60, 80, 70))

    # 能量球
    d.ellipse([S(60), height - S(230), S(60) + S(130), height - S(100)], fill=(25, 60, 130), outline=(150, 200, 255), width=4)
    _center(d, (S(60), height - S(230), S(60) + S(130), height - S(100)), TRUTH["能量"], _font(S(46)), (255, 255, 255))

    # 手牌
    cw, ch = S(210), S(300)
    total = len(TRUTH["手牌"]) * (cw + S(14))
    start = (width - total) // 2
    for i, card in enumerate(TRUTH["手牌"]):
        _card(d, start + i * (cw + S(14)), height - S(330), cw, ch, card, selected=(i == 2))

    # 结束回合按钮
    d.rounded_rectangle([width - S(230), height - S(250), width - S(30), height - S(180)],
                        radius=S(24), fill=(70, 60, 40), outline=(210, 190, 120), width=3)
    _center(d, (width - S(230), height - S(250), width - S(30), height - S(180)), "结束回合", _font(S(30)), (255, 245, 210))
    return img


def send(img_path: str, question: str, max_side: int) -> dict:
    img = Image.open(img_path).convert("RGB")
    if max(img.size) > max_side:
        sc = max_side / float(max(img.size))
        img = img.resize((int(img.width * sc), int(img.height * sc)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=cfg_mod.SCREEN["jpeg_quality"])
    url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    key, _ = find_api_key(cfg_mod.API.get("api_key", ""), [APP_DIR])
    client = VisionClient(cfg_mod.API, key)
    msgs = [
        {"role": "system", "content": prompts.get("slay_the_spire")},
        VisionClient.build_user_message(question, url),
    ]
    out = client.stream_chat(msgs)
    out["sent_size"] = img.size
    return out


Q = (
    "这是一张《杀戮尖塔》战斗截图。请严格按下面格式逐行回答，不要加解释：\n"
    "能量=\n血量=\n格挡=\n金币=\n层数=\n"
    "敌人1=名字|血量|意图\n敌人2=名字|血量|意图\n"
    "手牌=名字(费),名字(费),...\n"
    "遗物=\n药水=\n"
    "最后一行写：打法=一句话建议"
)


def score(text: str) -> None:
    """把模型输出和标准答案做关键词命中统计。"""
    want = [
        TRUTH["能量"], TRUTH["血量"], "0", TRUTH["金币"], "12",
        "颚虫", "44/44", "11",
        "史莱姆", "28/32", "7",
        "打击", "痛击", "防御", "重锤",
        "打击(1)", "痛击(2)", "重锤(3)",
        "燃烧之血", "破碎核心", "火焰药水",
    ]
    hit = [w for w in want if w in text]
    miss = [w for w in want if w not in text]
    print(f"  命中 {len(hit)}/{len(want)}")
    if miss:
        print("  漏读:", "、".join(miss))


def main() -> int:
    src = os.path.join(APP_DIR, "_synthetic_truth.png")
    render(1920, 1080).save(src)
    print("已生成合成截图:", src)

    for max_side in (1280, 1600):
        path = os.path.join(APP_DIR, f"_synthetic_{max_side}.jpg")
        img = Image.open(src)
        sc = max_side / max(img.size)
        img.resize((max_side, int(img.height * sc)), Image.LANCZOS).save(path, quality=cfg_mod.SCREEN["jpeg_quality"])
        print("\n" + "=" * 72)
        print(f"发送分辨率 {max_side}px（{path}，{os.path.getsize(path)//1024}KB）")
        out = send(path, Q, max_side)
        u = out["usage"] or {}
        print(f"耗时 {out['elapsed']:.1f}s（首字 {out['ttfb']:.1f}s）· "
              f"prompt {u.get('prompt_tokens')} + completion {u.get('completion_tokens')} = {u.get('total_tokens')} tok"
              f" · 思考 {(u.get('completion_tokens_details') or {}).get('reasoning_tokens')} tok")
        print("-" * 72)
        print(out["text"].strip())
        print("-" * 72)
        score(out["text"])
        if out.get("truncated"):
            print("  ⚠️ 被 token 上限截断")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
