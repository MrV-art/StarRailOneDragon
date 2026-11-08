"""差分宇宙 配置（2026-11-08）

落在 `config/<实例>/div_uni.yml`。字段刻意很少 —— 现在只有「跑几个区域」和
「要不要自己进场」两个开关，其余（区域识别、事件、清场、结算）全由流程自动判断。

⚠️ 只放**用户可能需要改**的项；不要把超时、坐标之类的内部参数搬进来，
   那些属于实现细节（见 div_uni_auto.py 里的常量）。
"""

from typing import Optional

from one_dragon.base.config.yaml_config import YamlConfig


class DivUniConfig(YamlConfig):

    def __init__(self, instance_idx: Optional[int] = None):
        YamlConfig.__init__(self, 'div_uni', instance_idx=instance_idx)

    @property
    def max_areas(self) -> int:
        """最多跑几个区域。

        周期演算一局是 13 个区域（(n/13)），默认给 14 留一次余量，
        跑满 13/13 打出最终首领后会走「结束并结算」收尾。
        """
        try:
            return int(self.get('max_areas', 14))
        except (TypeError, ValueError):
            return 14

    @max_areas.setter
    def max_areas(self, new_value: int) -> None:
        self.update('max_areas', int(new_value))

    @property
    def skip_entry(self) -> bool:
        """是否跳过「从大世界进场」。

        False（默认）= **自动判断从哪开始**（2026-11-08 起）：
                        已在局内 → 直接从当前节点接着跑，不回主界面；
                        在差分宇宙大厅 → 只点「周期演算 → 继续进度」；
                        在大世界 → 才走完整进场流程（指南 → 差分宇宙 → …）。
        True         = 不做任何检测，直接假定已在副本里（调试用）。
        """
        v = self.get('skip_entry', False)
        if isinstance(v, str):
            return v.strip().lower() in ('1', 'true', 'yes', 'on')
        return bool(v)

    @skip_entry.setter
    def skip_entry(self, new_value: bool) -> None:
        self.update('skip_entry', bool(new_value))
