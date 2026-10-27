# -*- coding: utf-8 -*-
"""读取《杀戮尖塔》的卡组/遗物数据，塞进提示词里。

为什么要做这个：模型看图能认出「这一手有什么牌」，但认不出「你整套卡组是什么」，
所以一到选牌、商店、篝火升级的界面，它的建议就只能靠瞎猜。
游戏自己会把每次跑局写成 .run 文件（JSON），里面有完整的 master_deck 和 relics。

数据来源优先级：
  1. saves/ 下正在进行的存档（有的版本有）
  2. runs/<角色>/ 里最近一次跑局（打完或退出时写入）—— 最常用
  3. 程序目录下的 deck.txt（你自己粘贴的卡组，最准）

    python runinfo.py        # 看看现在能读到什么
"""
from __future__ import annotations

import glob
import json
import os
import time

SAVE_ROOT_CANDIDATES = [
    r"C:\Users\dqy20\steam\steamapps\common\SlayTheSpire\saves",
    os.path.join(os.environ.get("APPDATA", ""), "SlayTheSpire"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "SlayTheSpire"),
]
RUN_ROOT_CANDIDATES = [
    r"C:\Users\dqy20\steam\steamapps\common\SlayTheSpire\runs",
    os.path.join(os.environ.get("APPDATA", ""), "SlayTheSpire", "runs"),
]

CHARACTERS = {
    "IRONCLAD": "铁甲战士",
    "THE_SILENT": "静默猎手",
    "DEFECT": "故障机器人",
    "WATCHER": "观者",
}

# 铁甲战士卡牌英文 ID → 中文名（按需补充，认不出的会原样显示英文）
CARD_ZH = {
    "Strike_R": "打击", "Defend_R": "防御", "Bash": "痛击", "Anger": "愤怒",
    "Armaments": "军备", "Body Slam": "全身撞击", "Clash": "冲突", "Cleave": "顺劈斩",
    "Clothesline": "晾衣绳", "Flex": "屈伸", "Havoc": "浩劫", "Headbutt": "铁头功",
    "Heavy Blade": "重刃", "Iron Wave": "铁斩波", "Perfected Strike": "完美打击",
    "Pommel Strike": "柄击", "Shrug It Off": "耸肩无视", "Sword Boomerang": "回旋剑",
    "Thunderclap": "雷霆之击", "True Grit": "坚毅", "Twin Strike": "双重打击",
    "Warcry": "战吼", "Wild Strike": "狂野打击", "Battle Trance": "战斗专注",
    "Blood for Blood": "血债血偿", "Bloodletting": "放血", "Burning Pact": "燃烧契约",
    "Carnage": "杀戮", "Combust": "燃烧", "Dark Embrace": "黑暗之拥",
    "Disarm": "缴械", "Dual Wield": "双持", "Entrench": "巩固", "Evolve": "进化",
    "Feel No Pain": "无痛", "Fire Breathing": "火焰吐息", "Flame Barrier": "火焰屏障",
    "Ghostly Armor": "幽灵护甲", "Hemokinesis": "血液动力", "Infernal Blade": "地狱之刃",
    "Inflame": "火上浇油", "Intimidate": "威吓", "Metallicize": "金属化",
    "Power Through": "强行突进", "Pummel": "重击", "Rage": "暴怒", "Rampage": "横冲直撞",
    "Reckless Charge": "鲁莽冲锋", "Rupture": "破裂", "Searing Blow": "灼热攻击",
    "Second Wind": "重整旗鼓", "Seeing Red": "见红", "Sentinel": "哨兵",
    "Sever Soul": "断魂斩", "Shockwave": "冲击波", "Spot Weakness": "找准弱点",
    "Uppercut": "上勾拳", "Whirlwind": "旋风斩", "Barricade": "壁垒",
    "Berserk": "狂暴", "Bludgeon": "重锤", "Brutality": "残暴", "Corruption": "腐化",
    "Demon Form": "恶魔形态", "Double Tap": "双发", "Exhume": "掘尸", "Feed": "进食",
    "Fiend Fire": "恶魔之焰", "Immolate": "献祭", "Impervious": "铜墙铁壁",
    "Juggernaut": "主宰", "Limit Break": "极限爆发", "Offering": "祭品",
    "Reaper": "收割", "Blind": "致盲", "Bandage Up": "包扎", "Dark Shackles": "黑暗镣铐",
    "Deep Breath": "深呼吸", "Discovery": "发现", "Dramatic Entrance": "华丽登场",
    "Enlightenment": "启蒙", "Finesse": "技巧", "Flash of Steel": "钢之闪光",
    "Forethought": "深思熟虑", "Good Instincts": "良好直觉", "Impatience": "急躁",
    "Jack Of All Trades": "万事通", "Madness": "疯狂", "Master of Strategy": "战略大师",
    "Mayhem": "混乱", "Metamorphosis": "变形", "Panacea": "灵丹妙药",
    "Panic Button": "紧急按钮", "Purity": "纯净", "Swift Strike": "迅捷打击",
    "Trip": "绊倒", "Apotheosis": "升华", "Chrysalis": "蝶蛹", "Hand of Greed": "贪婪之手",
    "Magnetism": "磁性", "Sadistic Nature": "虐待天性", "Secret Technique": "秘技",
    "Secret Weapon": "秘密武器", "Thinking Ahead": "未雨绸缪", "Transmutation": "转化",
    "Violence": "暴力", "CurseOfTheBell": "钟之诅咒", "Ascender's Bane": "攀登者的灾祸",
    "Necronomicurse": "死灵诅咒", "Injury": "损伤", "Clumsy": "笨拙", "Parasite": "寄生虫",
    "Regret": "悔恨", "Pain": "痛苦", "Writhe": "扭曲", "Normality": "常态",
    "Doubt": "怀疑", "Shame": "羞耻", "Decay": "腐朽", "Pride": "傲慢",
}

RELIC_ZH = {
    "Burning Blood": "燃烧之血", "Oddly Smooth Stone": "异常光滑的石头",
    "Bag of Marbles": "弹珠袋", "Vajra": "金刚杵", "Shovel": "铲子",
    "Calling Bell": "召唤铃", "Lantern": "提灯", "Pantograph": "缩放仪",
    "Mango": "芒果", "Bag of Preparation": "准备袋", "Enchiridion": "手册",
    "Nunchaku": "双节棍", "Boot": "靴子", "Membership Card": "会员卡",
    "TungstenRod": "钨钢棒", "Sozu": "崇", "Gambling Chip": "赌徒筹码",
    "Paper Frog": "纸青蛙", "Eternal Feather": "永恒之羽", "Medical Kit": "医疗箱",
    "Anchor": "锚", "Ancient Tea Set": "古茶具", "Art of War": "战争艺术",
    "Blood Vial": "血瓶", "Bronze Scales": "青铜鳞片", "Centennial Puzzle": "百年拼图",
    "Ceramic Fish": "陶瓷鱼", "Cracked Core": "破碎核心", "Cursed Key": "被诅咒的钥匙",
    "Darkstone Periapt": "暗石护符", "Dream Catcher": "捕梦网", "Du-Vu Doll": "巫毒娃娃",
    "Ectoplasm": "外质", "Frozen Egg": "冰冻蛋", "Ginger": "姜",
    "Gremlin Horn": "哥布林之角", "Happy Flower": "快乐花", "Ice Cream": "冰淇淋",
    "Incense Burner": "香炉", "Kunai": "苦无", "Letter Opener": "开信刀",
    "Lizard Tail": "蜥蜴尾", "Maw Bank": "巨口钱罐", "Meat on the Bone": "带肉骨头",
    "Mercury Hourglass": "水银沙漏", "Molten Egg": "熔岩蛋", "Mummified Hand": "木乃伊之手",
    "Orichalcum": "山铜", "Ornamental Fan": "装饰扇", "Peace Pipe": "和平烟斗",
    "Pen Nib": "笔尖", "Philosopher's Stone": "贤者之石", "Pocketwatch": "怀表",
    "Preserved Insect": "保存的昆虫", "Question Card": "问号卡", "Regal Pillow": "华丽枕头",
    "Runic Cube": "符文立方", "Runic Dome": "符文圆顶", "Runic Pyramid": "符文金字塔",
    "Shuriken": "手里剑", "Singing Bowl": "唱钵", "Snecko Eye": "蛇眼",
    "Strawberry": "草莓", "The Courier": "信使", "Tiny Chest": "小箱子",
    "Toxic Egg": "剧毒蛋", "Toy Ornithopter": "玩具扑翼机", "Velvet Choker": "天鹅绒项圈",
    "White Beast Statue": "白色野兽雕像", "Wrist Blade": "腕刃",
}


def _find_latest(pattern: str) -> str | None:
    files = glob.glob(pattern, recursive=True)
    files = [f for f in files if os.path.isfile(f)]
    return max(files, key=os.path.getmtime) if files else None


def _card_zh(card_id: str) -> str:
    upgraded = card_id.endswith("+1") or card_id.endswith("+")
    base = card_id.rstrip("+").removesuffix("+1")
    name = CARD_ZH.get(base, base)
    return name + ("+" if upgraded else "")


def _relic_zh(relic_id: str) -> str:
    return RELIC_ZH.get(relic_id, relic_id)


def read_last_run() -> dict | None:
    """读最近一次跑局的数据（含卡组、遗物、达到楼层、胜负）。"""
    candidates = [os.path.join(root, "**", "*.run") for root in RUN_ROOT_CANDIDATES]
    newest, newest_time = None, 0.0
    for pattern in candidates:
        path = _find_latest(pattern)
        if path and os.path.getmtime(path) > newest_time:
            newest, newest_time = path, os.path.getmtime(path)
    if not newest:
        return None
    try:
        with open(newest, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        return None
    return {
        "file": newest,
        "mtime": newest_time,
        "character": CHARACTERS.get(raw.get("character_chosen", ""), raw.get("character_chosen", "?")),
        "ascension": raw.get("ascension_level") if raw.get("is_ascension_mode") else 0,
        "floor": raw.get("floor_reached"),
        "victory": bool(raw.get("victory")),
        "killed_by": raw.get("killed_by"),
        "deck": [_card_zh(c) for c in (raw.get("master_deck") or [])],
        "relics": [_relic_zh(r) for r in (raw.get("relics") or [])],
        "gold": raw.get("gold"),
    }


def read_deck_file(extra_dirs: list[str] | None = None) -> str | None:
    """deck.txt：你自己粘贴的卡组/遗物，优先级最高。"""
    for folder in (extra_dirs or []) + [os.path.dirname(os.path.abspath(__file__))]:
        path = os.path.join(folder, "deck.txt")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    text = fh.read().strip()
                if text:
                    return text
            except Exception:
                pass
    return None


def summarize(extra_dirs: list[str] | None = None, max_age_hours: float = 12.0) -> str:
    """生成给提示词用的「我现在的牌组」段落。没有数据就返回空串。"""
    manual = read_deck_file(extra_dirs)
    if manual:
        return "【我的牌组/遗物（我自己提供的，可信）】\n" + manual[:1500]

    run = read_last_run()
    if not run:
        return ""
    age_h = (time.time() - run["mtime"]) / 3600.0
    stale = age_h > max_age_hours
    head = (
        f"【我最近一次跑局的数据（游戏写在自己目录里的，{age_h:.1f} 小时前）】\n"
        f"角色：{run['character']}"
        + (f"（进阶 {run['ascension']}）" if run["ascension"] else "")
        + f"，打到第 {run['floor']} 层，{'通关' if run['victory'] else '失败'}"
        + (f"（死于 {run['killed_by']}）" if run.get("killed_by") else "")
        + "\n"
        f"当时卡组（{len(run['deck'])} 张）：" + "、".join(run["deck"]) + "\n"
        f"当时遗物（{len(run['relics'])} 个）：" + "、".join(run["relics"]) + "\n"
    )
    if stale:
        head += (
            "⚠️ 注意：上面的数据是上一次跑局的，不一定是你现在这一局。"
            "如果在选牌/商店界面，先看截图里实际显示的卡，再结合上面的卡组风格给建议；"
            "不确定就当参考，别当成当前状态。\n"
        )
    else:
        head += "（如果这是你正在打的这一局，就直接按这套卡组和遗物给建议。）\n"
    return head


def context_for_prompt(extra_dirs: list[str] | None = None) -> str:
    return summarize(extra_dirs)


if __name__ == "__main__":
    print("saves 目录:", [d for d in SAVE_ROOT_CANDIDATES if os.path.isdir(d)])
    print("runs 目录:", [d for d in RUN_ROOT_CANDIDATES if os.path.isdir(d)])
    print()
    text = summarize()
    print(text or "（没读到任何卡组数据；可以自己在程序目录建 deck.txt 粘一份）")
