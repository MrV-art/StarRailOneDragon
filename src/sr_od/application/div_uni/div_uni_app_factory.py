"""差分宇宙 应用工厂（2026-11-08 新增）

`ApplicationFactoryManager` 会**自动扫描**所有 `*_factory.py` 并注册其中的
`ApplicationFactory` 子类 —— 所以这里不需要在任何地方手动登记。
✅ 新增/删除本应用 = 增删这个目录即可。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from one_dragon.base.config.game_account_config import GameAccountConfig
from one_dragon.base.operation.application.application_factory import ApplicationFactory
from one_dragon.base.operation.application_base import Application
from one_dragon.base.operation.application_run_record import AppRunRecord
from sr_od.application.div_uni import div_uni_const
from sr_od.application.div_uni.div_uni_app import DivUniApp
from sr_od.application.div_uni.div_uni_config import DivUniConfig
from sr_od.application.div_uni.div_uni_run_record import DivUniRunRecord

if TYPE_CHECKING:
    from sr_od.context.sr_context import SrContext


class DivUniAppFactory(ApplicationFactory):

    def __init__(self, ctx: SrContext):
        ApplicationFactory.__init__(self, div_uni_const)
        self.ctx: SrContext = ctx

    def create_application(self, instance_idx: int, group_id: str) -> Application:
        return DivUniApp(self.ctx, DivUniConfig(instance_idx))

    def create_config(self, instance_idx: int, group_id: str) -> DivUniConfig:
        return DivUniConfig(instance_idx)

    def create_run_record(self, instance_idx: int) -> AppRunRecord:
        return DivUniRunRecord(
            DivUniConfig(instance_idx),
            instance_idx,
            GameAccountConfig(instance_idx).game_refresh_hour_offset,
        )
