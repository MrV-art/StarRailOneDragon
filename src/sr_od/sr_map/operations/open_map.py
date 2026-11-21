from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from sr_od.context.sr_context import SrContext
from sr_od.operations.sr_operation import SrOperation
from sr_od.screen_state import common_screen_state
from sr_od.sr_map import large_map_utils


class OpenMap(SrOperation):

    def __init__(self, ctx: SrContext):
        """
        通过按 esc 和 m 打开大地图
        """
        SrOperation.__init__(self, ctx, op_name=gt('打开地图'))

    @operation_node(name='画面识别', node_max_retry_times=10, is_start_node=True)
    def check_screen(self) -> OperationRoundResult:
        screen = self.last_screenshot

        if common_screen_state.is_normal_in_world(self.ctx, screen):  # 主界面
            log.info('尝试打开地图')
            self.ctx.controller.open_map()
            return self.round_wait(wait=2)

        # ⚠️ 2026-10-19 作者指出的设计问题：原来把「能读出当前星球的中文名」当成「在大地图」✗，
        #   一旦遇到清单里没有的新星球（千星城）就死循环 ✗。改用**结构性判据** ✓：
        #   「星轨航图」按钮只在真·大地图上出现 ✓（千星城那个"导航"面板没有它 ✗，只有 规则说明/X/返回）
        #   → 看到它就认定"已经在大地图" ✓，**不再依赖星球名** ✓（星球名只用于日志 ✓）。
        r_star_map = self.round_by_find_area(screen, '大地图', '按钮-星轨航图')
        if r_star_map.is_success:
            planet = large_map_utils.get_planet(self.ctx, screen)
            log.info('在大地图（按「星轨航图」判定）当前星球 %s', planet)
            return self.round_success()

        # 二级地图中 需要返回
        r1 = self.round_by_find_area(screen, '大地图', '标题-导航')
        if r1.is_success:
            # 在导航 但是没有开拓力的图标 说明在子区域中 需要返回
            r2 = self.round_by_find_area(screen, '大地图', '图标-开拓力')
            if not r2.is_success:
                # ⚠️ 2026-10-19 实测（千星城「翻转餐厅」特殊地图）：这里跟特殊的「导航」面板有关 ✓
                #   该面板：左上角"导航/千星城中心城区"、右上角只有 规则说明/X/**返回**，
                #   **没有开拓力图标** ✗、也**没有星轨航图** ✗ —— 会落进这个分支 ✓。
                #   ⚠️ 实测结论：**必须点「返回」** ✓ —— 点了它就回到普通大地图 ✓（左上角就是 `千星城` ✓）；
                #   点 X 只会把地图整个关掉 ✗，下一轮再打开还是同一个特殊面板 ✗（我 10:19 改错过一次 ✗，已改回）✓。
                # ⚠️ 2026-10-19 14:42 实测修正：这里**必须点「返回」** ✓
                #   （我在 10:19 改成"先点 X" ✗ —— 实测 X 会把地图**整个关掉** ✗，
                #     下一轮又打开同一个特殊面板 → 照样死循环 ✗。）
                #   点「返回」才会回到**普通大地图** ✓：千星城实测返回后左上角就是 `千星城` ✓，
                #   而 `planet.yml` 已收录它 ✓；再加上上游的「星轨航图」结构判据 ✓，
                #   回到普通大地图这一轮就能 `round_success()` ✓。
                result = self.round_by_find_and_click_area(screen, '大地图', '子区域-返回')
                if result.is_success:
                    return self.round_wait(result.status, wait=1)
                # 兜底：没有「返回」时（真·子区域）才用 X 关闭
                result = self.round_by_click_area('大地图', '子区域-关闭')
                return self.round_wait(result.status, wait=1)

        planet = large_map_utils.get_planet(self.ctx, screen)
        log.info('当前大地图所处星球 %s', planet)
        if planet is not None:  # 左上角找到星球名字的话 证明在在大地图页面了
            return self.round_success()

        # 其他情况都需要通过返回上级菜单再尝试打开大地图
        log.info('尝试返回上级菜单')
        self.ctx.controller.esc()
        return self.round_retry(wait=2)
