"""差分宇宙 应用实现（2026-11-08 新增）

设计取舍（为什么是"子线程 + 轮询"而不是一堆 operation node）：

    差分宇宙的一局是**连续十几分钟**的手感流程：进场 → 清区域 → 事件对话 → 战斗 →
    选站点卡 → 过门 → …… → 13/13 结算。每一步都强依赖上一步的画面，天然是"一条剧本"，
    而不是"每帧判一次状态再决策"。这套剧本已经写在 `sr_od/app/div_uni/div_uni_auto.py`
    里（含 OCR 判界面、SendInput 转向、门模板 + 时间一致性、奇迹/站点卡策略等，实测通关过）。

    所以这里**不重写流程**，只把它挂进应用框架：
      · 起一个 daemon 线程跑 `div_uni_auto.run()`；
      · operation node 每 2 秒回来看一眼线程活没活，上报「运行中」；
      · 线程结束后按返回值判 round_success / round_fail。

    好处：框架的界面、停止按钮、通知、运行记录都还能正常工作，
    不会被一个十几分钟的阻塞调用卡死。

⚠️ 该流程内部自己截图、自己发按键（pyautogui/SendInput），不走 ctx.controller ——
   这是它能在独立脚本里跑通的原因，保持原样，别在这里改成 ctx.controller 的调用。
"""

import threading
from typing import Optional

from one_dragon.base.operation.operation_node import operation_node
from one_dragon.base.operation.operation_round_result import OperationRoundResult
from one_dragon.utils.i18_utils import gt
from one_dragon.utils.log_utils import log
from sr_od.application.div_uni import div_uni_const
from sr_od.application.div_uni.div_uni_config import DivUniConfig
from sr_od.application.sr_application import SrApplication
from sr_od.context.sr_context import SrContext

# 一局的硬上限（秒）。13 个区域实测 30~60 分钟，给 90 分钟兜底，防止线程永久挂着。
FLOW_TIMEOUT_SECONDS = 90 * 60


class DivUniApp(SrApplication):

    def __init__(self, ctx: SrContext, config: Optional[DivUniConfig] = None):
        self.config = config if config is not None else DivUniConfig()
        SrApplication.__init__(
            self,
            ctx,
            div_uni_const.APP_ID,
            op_name=gt(div_uni_const.APP_NAME),
            need_check_game_win=True,
        )
        self._thread: Optional[threading.Thread] = None
        self._result: Optional[int] = None      # 0 = 成功，其它 = 失败
        self._error: Optional[str] = None
        self._started_at: float = 0.0
        self._last_report: int = 0              # 上次上报的整分钟数

    # ---------------- operation node ----------------

    @operation_node(name='差分宇宙', is_start_node=True)
    def run_flow(self) -> OperationRoundResult:
        if self._thread is None:
            self._start_worker()
            return self.round_wait('已启动差分宇宙流程', wait=2)

        if self._thread.is_alive():
            self._report_progress()
            if self._elapsed() > FLOW_TIMEOUT_SECONDS:
                log.warning('差分宇宙流程超过 %d 秒仍未结束，判失败' % FLOW_TIMEOUT_SECONDS)
                return self.round_fail('流程超时')
            return self.round_wait('差分宇宙运行中', wait=2)

        if self._error:
            return self.round_fail(self._error)
        if self._result == 0:
            return self.round_success()
        return self.round_fail('流程未成功（返回 %s）' % self._result)

    # ---------------- 内部 ----------------

    def _start_worker(self) -> None:
        import time as _time
        self._started_at = _time.time()
        self._thread = threading.Thread(
            target=self._worker,
            name='div_uni_flow',
            daemon=True,
        )
        self._thread.start()
        log.info('差分宇宙：已启动后台流程线程（max_areas=%d, skip_entry=%s）'
                 % (self.config.max_areas, self.config.skip_entry))

    def _worker(self) -> None:
        try:
            # 延迟导入：div_uni_auto 会拉起 cv2 / onnxruntime / 模板等，
            # 不在应用注册阶段导入，避免拖慢一条龙启动与插件扫描。
            from sr_od.app.div_uni import div_uni_auto
            self._result = div_uni_auto.run(
                max_areas=self.config.max_areas,
                dry=False,
                skip_entry=self.config.skip_entry,
            )
        except Exception as e:                      # noqa: BLE001 保底，别让线程静默死掉
            import traceback
            self._error = '%s: %s' % (type(e).__name__, e)
            log.error('差分宇宙流程异常：%s' % self._error)
            log.error(traceback.format_exc())
            self._result = -1

    def _elapsed(self) -> float:
        import time as _time
        return _time.time() - self._started_at if self._started_at else 0.0

    def _report_progress(self) -> None:
        """每分钟写一条日志，方便看它到底跑到哪儿了（流程自己也会打详细日志）。"""
        minute = int(self._elapsed() // 60)
        if minute > self._last_report:
            self._last_report = minute
            log.info('差分宇宙：已运行 %d 分钟，仍在处理中…' % minute)


def __debug():
    ctx = SrContext()
    ctx.init_ocr()
    ctx.init_by_config()
    ctx.init_for_sim_uni()
    ctx.start_running()
    app = DivUniApp(ctx)
    app.execute()
    ctx.stop_running()


if __name__ == '__main__':
    __debug()
