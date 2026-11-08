"""差分宇宙 运行记录（2026-11-08）

记录 `run_time` / `run_status`，一条龙界面的「上次运行」和通知汇总都读它。
差分宇宙按**周**结算积分，但一条龙是每天跑一条龙，所以这里按 base 的每日重置走，
意义是"今天有没有跑成功过一次"，不参与周积分判断（积分是否满由游戏内决定）。
"""

from typing import Optional

from one_dragon.base.operation.application_run_record import AppRunRecord
from sr_od.application.div_uni import div_uni_const
from sr_od.application.div_uni.div_uni_config import DivUniConfig


class DivUniRunRecord(AppRunRecord):

    def __init__(self, config: Optional[DivUniConfig] = None,
                 instance_idx: Optional[int] = None,
                 game_refresh_hour_offset: int = 0):
        AppRunRecord.__init__(
            self,
            div_uni_const.APP_ID,
            instance_idx=instance_idx,
            game_refresh_hour_offset=game_refresh_hour_offset,
        )
        self.config = config
