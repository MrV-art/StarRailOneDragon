"""差分宇宙 · 应用常量（2026-11-08 新增）

⚠️ 本模块文件名必须以 `_const` 结尾 —— `ApplicationFactoryManager` 按后缀扫描，
   同一个目录下**只能有一个** `*_const.py` 和一个 `*_factory.py`，否则整组被跳过。
"""

APP_ID = "div_uni"
APP_NAME = "差分宇宙"

# True = 出现在「一条龙」的应用列表里（用户勾选后每次一条龙都会跑）
DEFAULT_GROUP = True

# True = 跑完参与一条龙的通知汇总
NEED_NOTIFY = True
