# -*- coding: utf-8 -*-
"""AI 游戏陪玩 · 配置文件（改完直接重启程序生效）

唯一必填项是 API Key：
  1) 推荐：设置系统环境变量 DEEPSEEK_API_KEY
  2) 或在本目录建一个 apikey.txt，里面只写一行 key
  3) 或直接填在下面（不推荐，容易误传到 git）
本机 DSH 已登录的情况下，程序会自动读取 C:\\Users\\<你>\\.dsh\\.credentials.yaml
"""

# ============================ 模型 ============================
API = {
    "base_url": "https://api.deepseek.com",
    "path": "/chat/completions",
    "model": "deepseek-v4-flash-vision-exp",   # 必须是能看图的模型
    "api_key": "",                              # 留空则按上面 3 种方式自动找
    "reasoning_effort": "low",   # 实测：minimal 反而更慢更啰嗦；none=最快便宜但仍会思考；low=推荐；medium/high=更聪明更慢更贵
    "max_tokens": 2600,          # 注意：视觉模型的“思考过程”也算在里面，给少了会出现“只有思考没有答案”
    "temperature": 0.4,
    "timeout": 180,                  # 单次请求超时（秒）
}

# ============================ 抓屏 ============================
SCREEN = {
    "monitor": 1,          # 第几块屏，1 = 主屏；多屏时可以改成 2 试试
    "max_side": 1280,      # 发给模型前把长边缩到这个像素（越大越准，越贵）
    "jpeg_quality": 72,    # 40~85，越高越清晰也越慢
    "hide_self_when_capture": True,   # 抓屏瞬间隐藏悬浮窗，避免 AI 看到聊天记录
    "hide_delay_ms": 120,  # 隐藏后等多久再抓（等窗口真正消失）
}

# ============================ 悬浮窗 ============================
WINDOW = {
    "width": 420,
    "height": 560,
    "opacity": 0.93,        # 0.5~1.0 整体透明度
    "font_size": 13,        # 正文字号
    "bg": "#12161f",
    "fg": "#e6e9ef",
    "accent": "#7cc4ff",
    "font_family": "Microsoft YaHei UI",
}

# ============================ 全局热键 ============================
# 说明：F8 看屏幕；F7 切换只读/交互模式（只读=游戏不掉后台）；F9 显示/隐藏窗口；
#       F10 连续陪玩开关；F11 鼠标穿透；F12 清空历史
# 首选键被别的软件占用时会自动换备选键，启动时悬浮窗会告诉你实际生效的是哪个
HOTKEYS = {
    "analyze": "f8",
    "toggle_mode": "f7",
    "toggle_window": "f9",
    "toggle_watch": "f10",
    "toggle_click_through": "f11",
    "clear_history": "f12",
    "enabled": True,
}

# ============================ 行为 ============================
BEHAVIOR = {
    "stream": True,                 # 流式输出（打字机效果）
    "show_reasoning": True,         # 显示模型思考过程（灰色小字）
    "watch_interval_sec": 12,       # 连续陪玩模式下每隔几秒看一次屏
    "history_max_messages": 16,     # 保留最近多少条消息作为上下文
    "history_keep_images": 1,       # 上下文里最多保留几张历史截图（0~2，越多越贵）
    "default_topmost": True,
    "hotkey_release_delay_ms": 250, # 热键触发后多久才真正抓屏（避免键盘高亮还没消失）
}
