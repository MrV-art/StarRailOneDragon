from typing import ClassVar, Dict, List, Optional

from one_dragon.base.operation.operation_edge import node_from
from one_dragon.base.operation.operation_notify import NotifyTiming, node_notify
from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from sr_od.application.trailblaze_power import trailblaze_power_const
from sr_od.app.div_uni.operations.ornamenet_extraction import ChallengeOrnamentExtraction
from sr_od.application.sim_universe.sim_uni_app import SimUniApp
from sr_od.application.sim_universe.sim_uni_data import SimUniWorldEnum
from sr_od.application.sr_application import SrApplication
from sr_od.application.trailblaze_power.trailblaze_power_config import TrailblazePowerPlanItem
from sr_od.challenge_mission.use_trailblaze_power import UseTrailblazePower
from sr_od.context.sr_context import SrContext
from sr_od.interastral_peace_guide.guide_check_power import GuideCheckPower, GuidePowerResult
from sr_od.interastral_peace_guide.guide_def import GuideMission
from sr_od.operations.back_to_normal_world_plus import BackToNormalWorldPlus


class TrailblazePowerApp(SrApplication):

    STATUS_NORMAL_TASK: ClassVar[str] = '普通副本'
    STATUS_SIM_UNI_TASK: ClassVar[str] = '模拟宇宙'
    STATUS_OE_TASK: ClassVar[str] = '饰品提取'
    STATUS_NO_ENOUGH_POWER: ClassVar[str] = '体力不足'
    STATUS_NO_PLAN: ClassVar[str] = '没有开拓力计划'
    STATUS_WITH_PLAN: ClassVar[str] = '有开拓力计划'
    STATUS_PLAN_FINISHED: ClassVar[str] = '完成计划'

    # 这些分类下的关卡 单次消耗的开拓力不是固定的
    # 例如 培养目标 在游戏里只是一个入口 实际打的本可能是 拟造花萼(10)/凝滞虚影(30)/侵蚀隧洞(40)
    # 所以配置里写死的 power 不一定对 不能只靠本地扣体力来推算剩余体力
    UNFIXED_POWER_CATEGORIES: ClassVar[List[str]] = ['培养目标']

    def __init__(self, ctx: SrContext):
        SrApplication.__init__(self, ctx, trailblaze_power_const.APP_ID,
                               op_name=gt('开拓力'),
                               run_record=ctx.power_record)

        self.last_mission: Optional[GuideMission] = None  # 上一个挑战副本
        self.power: int = 0  # 剩余开拓力
        self.qty: int = 0  # 沉浸器数量

        self.power_is_fresh: bool = False  # self.power 是否是在本轮挑战之前刚从游戏读到的
        self.checked_power: Optional[int] = None  # 上一次从游戏读到的真实体力 用于推算单次消耗
        self.finished_times_since_check: int = 0  # 上次读体力之后完成的挑战次数 用于推算单次消耗
        self.plan_power: Dict[str, int] = {}  # 实测到的单次体力消耗 mission_id -> power

    @operation_node(name='检查当前需要挑战的关卡', is_start_node=True)
    def check_task(self) -> OperationRoundResult:
        """
        判断下一个是什么副本
        :return:
        """
        self.ctx.power_config.check_plan_run_times()
        plan: Optional[TrailblazePowerPlanItem] = self.ctx.power_config.get_next_plan()

        if plan is None:
            return self.round_success(status=TrailblazePowerApp.STATUS_NO_PLAN)

        return self.round_success(status=TrailblazePowerApp.STATUS_WITH_PLAN)

    @node_from(from_name='检查当前需要挑战的关卡', status=STATUS_WITH_PLAN)
    @node_from(from_name='执行开拓力计划')
    @operation_node(name='打开指南检查体力')
    def open_guide(self) -> OperationRoundResult:
        op = GuideCheckPower(self.ctx)
        op_result = op.execute()
        if op_result.success:
            power_result: GuidePowerResult = op_result.data
            self.power = power_result.power
            self.qty = power_result.qty
            # 这里读到的就是真实体力 当作本轮挑战的基准
            self.power_is_fresh = True
            self.checked_power = power_result.power
            self.finished_times_since_check = 0
        return self.round_by_op_result(op_result)

    @node_from(from_name='打开指南检查体力')
    @operation_node(name='执行开拓力计划')
    def execute_plan(self) -> OperationRoundResult:
        self.ctx.power_config.check_plan_run_times()
        plan: Optional[TrailblazePowerPlanItem] = self.ctx.power_config.get_next_plan()
        for _ in range(100):
            if plan is None:
                return self.round_success(TrailblazePowerApp.STATUS_NO_PLAN)

            mission: Optional[GuideMission] = self.ctx.guide_data.get_mission_by_unique_id(plan.mission_id)
            if mission is None:
                return self.round_success(TrailblazePowerApp.STATUS_NO_PLAN)

            # 培养目标这类消耗不固定的关卡 本地扣体力会越扣越偏（见类里的 UNFIXED_POWER_CATEGORIES）
            # 每轮挑战之前重新读一次真实体力 避免明明还有体力却被判成体力不足而少刷次数
            if self._has_unfixed_power(mission) and not self.power_is_fresh:
                self._refresh_power()

            can_run_times: int = self.power // self._get_mission_power(mission)
            if mission.cate.cn in ['模拟宇宙', '饰品提取']:  # 模拟宇宙相关的增加沉浸器数量
                can_run_times += self.qty

            if can_run_times > 0:
                break
            plan = self.ctx.power_config.get_next_plan(plan)
            if plan is None:
                break

        # noinspection PyUnboundLocalVariable
        if can_run_times == 0:
            return self.round_success(TrailblazePowerApp.STATUS_NO_ENOUGH_POWER)

        if can_run_times + plan.run_times > plan.plan_times:
            run_times = plan.plan_times - plan.run_times
        else:
            run_times = can_run_times

        log.info(f'准备挑战 {mission.mission_name} 次数 {run_times}')
        if run_times == 0:
            return self.round_success(TrailblazePowerApp.STATUS_PLAN_FINISHED)

        self.ctx.sim_uni_record.check_and_update_status()
        self.last_mission = mission

        if mission.cate.cn == '模拟宇宙':
            sim_num = 0
            for i in SimUniWorldEnum:
                if i.value.name == mission.mission_name:
                    sim_num = i.value.idx

            if sim_num == 0:
                return self.round_fail('未支持模拟宇宙 %s', mission.mission_name)

            op = SimUniApp(self.ctx,
                           specified_uni_num=sim_num,
                           max_reward_to_get=run_times,
                           get_reward_callback=self._on_sim_uni_get_reward
                           )
            return self.round_by_op_result(op.execute())
        elif mission.cate.cn == '饰品提取':
            op = ChallengeOrnamentExtraction(self.ctx, mission,
                                             run_times=run_times,
                                             diff=0,
                                             file_num=plan.file_num,
                                             team_name=plan.team_name,
                                             support_character=plan.support if plan.support != 'none' else None,
                                             get_reward_callback=self.on_oe_get_reward)
        else:
            op = UseTrailblazePower(self.ctx, mission, plan.team_num, run_times,
                                    support=plan.support if plan.support != 'none' else None,
                                    on_battle_success=self._on_normal_task_success)

        return self.round_by_op_result(op.execute())

    def _on_normal_task_success(self, finished_times: int, use_power: int):
        """
        普通副本获取一次奖励时候的回调
        :param finished_times: 完成次数
        :param use_power: 使用的体力
        :return:
        """
        log.info('挑战成功 完成次数 %d 使用体力 %d', finished_times, use_power)

        self.power_is_fresh = False
        self.finished_times_since_check += finished_times

        if self._has_unfixed_power(self.last_mission):
            # 消耗不固定的关卡 use_power 是按配置里的 power 算出来的 不可信
            # 这里不扣本地体力 下一轮 _refresh_power 会把真实体力读回来
            self.ctx.power_config.add_run_times(self.last_mission.unique_id, finished_times)
            return

        self.power -= use_power
        if self.power < 0:
            self.power = 0
        self.ctx.power_config.add_run_times(self.last_mission.unique_id, finished_times)

    def _on_sim_uni_get_reward(self, use_power: int, user_qty: int):
        """
        模拟宇宙 获取沉浸奖励后的回调
        :return:
        """
        log.info('获取沉浸奖励 使用体力 %d 使用沉浸器 %d', use_power, user_qty)
        self.ctx.power_config.add_run_times(self.last_mission.unique_id, 1)

        self.power -= use_power
        if self.power < 0:
            self.power = 0
        self.qty -= user_qty
        if self.qty < 0:
            self.qty = 0

    def on_oe_get_reward(self, qty: int):
        """
        饰品提取 获取奖励后的回调
        :return:
        """
        log.info('饰品提取获取奖励 次数 %d', qty)
        for _ in range(qty):
            if self.qty > 0:  # 优先使用沉浸器
                self.qty -= 1
            elif self.power >= self.last_mission.power:
                self.power -= self.last_mission.power

            self.ctx.power_config.add_run_times(self.last_mission.unique_id, 1)

    def _has_unfixed_power(self, mission: Optional[GuideMission]) -> bool:
        """
        该关卡的体力消耗是否不固定
        :param mission: 挑战关卡 可能为None
        :return:
        """
        if mission is None:
            return False

        return mission.cate.cn in TrailblazePowerApp.UNFIXED_POWER_CATEGORIES

    def _get_mission_power(self, mission: GuideMission) -> int:
        """
        获取关卡单次消耗的开拓力
        优先使用游戏中实测出来的数值 没有实测值时才用配置里的
        :param mission: 挑战关卡
        :return:
        """
        return self.plan_power.get(mission.unique_id, mission.power)

    def _refresh_power(self) -> bool:
        """
        重新从游戏里读取真实体力和沉浸器数量
        同时用两次读取的差值 推算消耗不固定关卡的单次体力消耗
        :return: 是否读取成功
        """
        op = GuideCheckPower(self.ctx)
        op_result = op.execute()
        if not op_result.success:
            # 读不到就继续用本地推算的值 不因为这一次失败直接把整个计划结束掉
            log.warning('重新读取体力失败 继续使用本地推算的体力 %d', self.power)
            return False

        power_result: GuidePowerResult = op_result.data
        log.info('重新读取体力 %d (本地推算 %d) 沉浸器 %d',
                 power_result.power, self.power, power_result.qty)

        # 两次读体力之间完成了若干次挑战 且体力确实下降了 就能反推出单次消耗
        # 挑战期间体力会自然恢复(约1点/6分钟) 恢复量会让差值变小 所以这里向上取整
        # 单次消耗都是10的倍数 只要恢复量不到一次消耗 向上取整就能还原出准确的消耗
        if (self._has_unfixed_power(self.last_mission)
                and self.checked_power is not None
                and self.finished_times_since_check > 0
                and power_result.power < self.checked_power):
            consumed = self.checked_power - power_result.power
            measured = (consumed + self.finished_times_since_check - 1) // self.finished_times_since_check
            # 游戏里最便宜的副本也要10点 太大则说明识别出错了 这类明显异常的值不采用
            if 10 <= measured <= 100:
                if self.plan_power.get(self.last_mission.unique_id) != measured:
                    log.info('实测 %s 单次消耗体力 %d (配置 %d)',
                             self.last_mission.mission_name, measured, self.last_mission.power)
                self.plan_power[self.last_mission.unique_id] = measured

        self.power = power_result.power
        self.qty = power_result.qty
        self.power_is_fresh = True
        self.checked_power = power_result.power
        self.finished_times_since_check = 0
        return True

    @node_from(from_name='检查当前需要挑战的关卡')
    @node_from(from_name='执行开拓力计划', status=STATUS_NO_PLAN)
    @node_from(from_name='执行开拓力计划', status=STATUS_NO_ENOUGH_POWER)
    @node_from(from_name='执行开拓力计划', status=STATUS_PLAN_FINISHED)
    @node_notify(when=NotifyTiming.CURRENT_DONE)
    @operation_node(name='完成后返回')
    def back_at_last(self) -> OperationRoundResult:
        op = BackToNormalWorldPlus(self.ctx)
        return self.round_by_op_result(op.execute())


def debug():
    ctx = SrContext()
    ctx.init_by_config()
    ctx.init_ocr()
    ctx.start_running()
    op = TrailblazePowerApp(ctx)

    op.execute()


if __name__ == '__main__':
    debug()


