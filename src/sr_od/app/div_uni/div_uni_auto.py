# -*- coding: utf-8 -*-
"""差分宇宙 · 周期演算 自动跑（第一版，2026-11-07）

════════════════════════════════════════════════════════════════════
这是"能单独跑"的版本：不需要启动一条龙 GUI，直接 python 跑本文件即可。
放在 sr_od/app/div_uni/ 里，是为了让它成为一条龙代码树的一部分 ✓
════════════════════════════════════════════════════════════════════

设计依据（全部来自 2026-11-07 的实战笔记）：
  1. **门会睡觉** ✓ —— 区域里还有互动没清完时，门上有「Z Z」，按 F 无效 ✗
  2. **暂离→重进 = 传送回区域入口** ✓ —— 重进后门必然在**正前方** ✓
  3. **只可能太远** ✗ —— [F] 不出就是还没走到 ✓，绝不后退 ✗
  4. 走路**小步**（0.4~0.7 秒）✓，一次按 W 超 1.2 秒必过站 ✗

所以本版的核心套路是（对每个区域都适用 ✓）：
    做区域动作（能做的都做）→ 暂离 → 重进 → 直着往前走 5 小步 → F
这比"找门/对中"稳得多 ✓ —— 门就在正前方，不需要识别 ✗。

用法：
    python src/sr_od/app/div_uni/div_uni_auto.py            # 从头开跑
    python src/sr_od/app/div_uni/div_uni_auto.py --areas 5  # 只跑 5 个区域试试
    python src/sr_od/app/div_uni/div_uni_auto.py --dry      # 只打印计划，不动手
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# ---- 复用 Learn 里的导航工具（已验证 ✓）----
_LEARN_TOOLS = r'D:\_PROGRAM_PythonProjects\Learn\tools'
if os.path.isdir(_LEARN_TOOLS) and _LEARN_TOOLS not in sys.path:
    sys.path.insert(0, _LEARN_TOOLS)

import win32con  # noqa: E402
import win32gui  # noqa: E402
import pyautogui  # noqa: E402

pyautogui.FAILSAFE = False      # ★ 关掉"鼠标到角落就抛异常"的保护（2026-11-07 实测会误触 ✓）

GAME_TITLE = '崩坏：星穹铁道'

# ---- 物理坐标（左屏 3840x2160；1920x1080 预览 ×2）----
P = {
    'menu_guide':      (2690, 1502),   # ESC 后「指南」
    'tab_planet':      (1186, 404),    # 星球页签
    'entry_div_uni':   (712, 962),     # 差分宇宙入口
    'go_participate':  (2952, 1746),   # 前往参与
    'btn_start':       (2630, 1710),   # 开始游戏
    'periodic':        (750, 980),    # 周期演算
    'btn_launch':      (2950, 1712),   # 启动
    'btn_continue':    (1180, 1494),   # 继续进度
    'btn_leave':       (3012, 1810),   # 暂离
    'btn_settle':      (3012, 1944),   # 结束并结算
    'confirm_settle':  (2327, 1568),   # 结算确认
    'card1':           (944, 1000),    # 站点卡第 1 张
    'btn_confirm':     (2282, 1946),   # 站点卡「确定」
    'dialog_next':     (3148, 1438),   # 事件 ▼
}


def log(msg):
    print(msg, flush=True)


# ================= OCR（用一条龙自带的 onnxocr ✓）=================
# ★ 2026-11-07 教训：**坐标永远别写死** ✗
#   实测定位差 200px（我按截图估「启动」=1475,855，OCR 实测 1678,968 ✗）
#   → 前台锁修好后仍然点不中 ✓ 因为位置就是错的 ✓
#   现在一律：**OCR 找文字 → 取文字中心 → 点** ✓✓

_OCR = None


def get_ocr():
    """懒加载一条龙自带的 PP-OCRv5（项目里有现成模型 ✓）。"""
    global _OCR
    if _OCR is not None:
        return _OCR
    root = r'D:\_GAME_miHoYo_StarRail_OneDragon'
    sys.path.insert(0, os.path.join(root, 'src'))
    base = os.path.join(root, 'assets', 'models', 'onnx_ocr', 'ppocrv5')
    from onnxocr.onnx_paddleocr import ONNXPaddleOcr
    _OCR = ONNXPaddleOcr(
        det_model_dir=os.path.join(base, 'det.onnx'),
        rec_model_dir=os.path.join(base, 'rec.onnx'),
        cls_model_dir=os.path.join(base, 'cls.onnx'),
        rec_char_dict_path=os.path.join(base, 'ppocrv5_dict.txt'),
        use_gpu=False,
        use_angle_cls=False,
    )
    return _OCR


def ocr_items(img=None):
    """把屏幕（1920 逻辑分辨率）OCR 一遍，返回 [(text, score, cx, cy), ...]（坐标是 1920 空间）。"""
    import numpy as np
    import cv2
    if img is None:
        img = np.array(pyautogui.screenshot())[:, :, ::-1]     # RGB->BGR
    res = get_ocr().ocr(img)
    out = []
    if isinstance(res, list) and res and isinstance(res[0], list):
        for item in res[0]:
            try:
                box, (txt, score) = item[0], item[1]
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                out.append((txt, float(score), sum(xs) / len(xs), sum(ys) / len(ys)))
            except Exception:
                continue
    return out


def ocr_find(text, must_contain=True, min_score=0.6, tries=10, interval=1.2):
    """在屏幕上找文字，返回**物理**坐标 (x, y) 或 None。"""
    for k in range(tries):
        for txt, score, cx, cy in ocr_items():
            if score < min_score:
                continue
            hit = (text in txt) if must_contain else (txt.strip() == text)
            if hit:
                # ★ 不乘 2 ✓ —— pyautogui.screenshot() 给的就是物理分辨率（3840x2160）✓
                return int(cx), int(cy)
        time.sleep(interval)
    return None


def click_text(text, min_score=0.6, tries=10, interval=1.2, settle=1.2):
    """OCR 找到就点它中心 ✓ 找不到返回 False（不瞎点 ✗）。"""
    pos = ocr_find(text, min_score=min_score, tries=tries, interval=interval)
    if pos is None:
        log('      ✗ 屏幕上找不到「%s」' % text)
        return False
    log('      点「%s」@ %s' % (text, pos))
    click_phys(pos[0], pos[1], settle=settle)
    return True


# ================= 图像识别 + 界面状态机 =================
# 参考：一条龙 screen_utils.find_template_coord_in_area / BetterGI 的"识别当前界面 → 交给对应 handler"
# 原则：**先用 OCR 读文字判定当前在哪个界面** ✓，再用**模板匹配**找按钮/图标 ✓，最后才点 ✓
#       —— 绝不"盲点"✗（今天所有事故都是盲点造成的）

def screenshot():
    """物理分辨率截图（3840x2160，BGR）。"""
    import numpy as np
    return np.array(pyautogui.screenshot())[:, :, ::-1]


def find_template(tpl_path, threshold=0.75, area=None, img=None):
    """模板匹配，返回**物理**坐标中心或 None。

    area = (x1, y1, x2, y2) 物理坐标，限定搜索区域（快很多 ✓，也少误匹配 ✓）
    """
    import numpy as np
    import cv2
    if img is None:
        img = screenshot()
    tpl = cv2.imread(tpl_path)
    if tpl is None:
        return None
    x1 = y1 = 0
    if area is not None:
        x1, y1, x2, y2 = area
        img = img[y1:y2, x1:x2]
    if img.shape[0] < tpl.shape[0] or img.shape[1] < tpl.shape[1]:
        return None
    res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
    _, maxv, _, maxloc = cv2.minMaxLoc(res)
    if maxv < threshold:
        return None
    cx = x1 + maxloc[0] + tpl.shape[1] // 2
    cy = y1 + maxloc[1] + tpl.shape[0] // 2
    return int(cx), int(cy), float(maxv)


def click_template(tpl_path, threshold=0.75, area=None, settle=1.0):
    pos = find_template(tpl_path, threshold=threshold, area=area)
    if pos is None:
        log('      ✗ 模板没匹配上: %s' % os.path.basename(tpl_path))
        return False
    log('      点模板 %s @ (%d,%d) score=%.2f' % (os.path.basename(tpl_path), pos[0], pos[1], pos[2]))
    click_phys(pos[0], pos[1], settle=settle)
    return True


# ---- 目标检测（多尺度模板匹配）+ 走过去 ----
# 为什么必须加这块（2026-11-07 晚实测）：
#   脚本原先只会「直走 + 盲按 F」，可**宝箱/怪常常偏在直线旁边几米**，
#   直走永远撞不到 → 于是空转、重置、再空转。加上"看见就往它走"才治本。
K_TURN = 2.2
K_MAX = 1.9          # 实测转向增益上限（屏像素/鼠标像素）
K_DOOR = 0.62        # 实测：SendInput +400 鼠标 ≈ 门 +249 屏像素          # 屏像素 / 鼠标像素（2026-11-07 实测：400 鼠标像素 ≈ 880 屏像素）


# ---- 本地 YOLO 检测（仓库自带 yolov8n-640-simuni-0601，2026-11-07 接入）----
# 为什么加：脚本原先只能"贴脸读提示 + 直走盲按 F"，认不出几米外的门/事件框/怪，
#   于是反复空转。仓库自带的这个模型类别里现成就有：
#     0061-mnyz-sj        模拟宇宙事件          ← 事件画框
#     0060-mnyz-xcrk      模拟宇宙下层入口       ← 出口门
#     0066-mnyz-xcrkwjh   模拟宇宙下层入口未激活 ← 门在睡觉
#     0062-mnyz-cjjl      模拟宇宙沉浸奖励
#     普通怪 / 精英怪 / 可破坏物
YOLO_DIR = r'D:\_GAME_miHoYo_StarRail_OneDragon\assets\models\sim_uni\yolov8n-640-simuni-0601'
_YOLO = {'sess': None, 'idx2': None, 'inp': None, 'out': None, 'fail': False}

CATE_EVENT = ('模拟宇宙事件',)
CATE_DOOR = ('模拟宇宙下层入口',)
CATE_DOOR_OFF = ('模拟宇宙下层入口未激活',)
CATE_REWARD = ('模拟宇宙沉浸奖励',)
CATE_ENEMY = ('普通怪', '精英怪')
CATE_BREAK = ('可破坏物',)


def _yolo_init():
    if _YOLO['sess'] is not None or _YOLO['fail']:
        return _YOLO['sess'] is not None
    try:
        import onnxruntime as ort
        model = os.path.join(YOLO_DIR, 'model.onnx')
        labels = os.path.join(YOLO_DIR, 'labels.csv')
        if not (os.path.exists(model) and os.path.exists(labels)):
            log('      ⚠ 没找到 YOLO 模型：%s' % YOLO_DIR)
            _YOLO['fail'] = True
            return False
        try:
            sess = ort.InferenceSession(model, providers=['DmlExecutionProvider', 'CPUExecutionProvider'])
        except Exception:
            sess = ort.InferenceSession(model, providers=['CPUExecutionProvider'])
        idx2 = {}
        with open(labels, encoding='utf-8') as f:
            next(f)
            for line in f:
                p = line.strip().split(',')
                if len(p) >= 3:
                    idx2[int(p[0])] = (p[1], p[2])
        _YOLO.update(sess=sess, idx2=idx2,
                     inp=sess.get_inputs()[0], out=sess.get_outputs()[0])
        log('      · YOLO 就绪（%s）' % sess.get_providers()[0])
        return True
    except Exception as e:
        log('      ⚠ YOLO 初始化失败: %s' % e)
        _YOLO['fail'] = True
        return False


def yolo_detect(min_conf=0.35, img=None):
    """返回 [(cate, label, conf, cx, cy, w, h)]，坐标是**物理像素**。"""
    if not _yolo_init():
        return []
    import numpy as np
    import cv2
    if img is None:
        img = screenshot()
    W, H = 640, 384
    canvas = cv2.resize(img, (W, H))
    blob = canvas[:, :, ::-1].astype(np.float32) / 255.0
    blob = np.transpose(blob, (2, 0, 1))[None, ...]
    pred = _YOLO['sess'].run([_YOLO['out'].name], {_YOLO['inp'].name: blob})[0][0].T
    conf_all = pred[:, 4:].max(axis=1)
    cls_all = pred[:, 4:].argmax(axis=1)
    keep = conf_all > min_conf
    if not keep.any():
        return []
    pred, conf_all, cls_all = pred[keep], conf_all[keep], cls_all[keep]
    boxes, confs = [], []
    for row in pred:
        cx, cy, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
        boxes.append([cx - bw / 2, cy - bh / 2, bw, bh])
        confs.append(1.0)
    idxs = cv2.dnn.NMSBoxes(boxes, list(map(float, conf_all)), float(min_conf), 0.45)
    flat_idx = []
    try:
        flat_idx = [int(i) for i in np.array(idxs).flatten()]
    except Exception:
        flat_idx = []
    sx, sy = img.shape[1] / float(W), img.shape[0] / float(H)
    out = []
    for i in flat_idx:
        row = pred[i]
        cx, cy, bw, bh = float(row[0]), float(row[1]), float(row[2]), float(row[3])
        label, cate = _YOLO['idx2'].get(int(cls_all[i]), ('?', '?'))
        out.append((cate, label, float(conf_all[i]),
                    int(cx * sx), int(cy * sy), int(bw * sx), int(bh * sy)))
    out.sort(key=lambda t: -t[2])
    return out


def yolo_pick(cates, min_conf=0.35, min_w=40):
    """在指定类别里挑一个目标（面积最大优先）。返回 dict 或 None。"""
    best = None
    for cate, label, conf, cx, cy, w, h in yolo_detect(min_conf=min_conf):
        if cate not in cates or w < min_w:
            continue
        if best is None or w * h > best['w'] * best['h']:
            best = {'cate': cate, 'label': label, 'conf': conf,
                    'cx': cx, 'cy': cy, 'w': w, 'h': h}
    return best


def go_to_target(cates, name, min_conf=0.35, close_frac=0.16, max_seconds=30,
                 use_tech=False, first=None):
    """朝 YOLO 找到的目标走，够近就按 F（怪则先放秘技）。返回 True = 出现「选择下一站」/过门。

    first = 调用方已经识别到的目标（省一次重复检测，也避免"这一帧刚好没认到"就放弃）。
    """
    t0 = time.time()
    tech_used = not use_tech
    lost = 0
    seen = 0
    d = first
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            return True
        hits = find_prompts()
        if hits:
            log('      · 到了，看到提示「%s」→ F' % hits[0][0])
            if interact_here():
                return True
            return False
        if d is not None:
            # ★ 只用一次就作废：否则"偏移量"永远是第一次那个值，日志会出现连着 5 次
            #   一模一样的「偏-935」，人不走、秘技乱放（2026-11-07 实测）。
            first = None
            d = None
        d = yolo_pick(cates, min_conf=min_conf)
        if d is None:
            lost += 1
            log('      · 这一帧没认到%s（第 %d 次）→ 原地小幅扫视/前进' % (name, lost))
            if lost >= 6:
                return False
            walk(0.5)
            press('f', 1.2)
            if on_station_picker() or area_passed():
                return True
            if is_battle_screen():
                wait_battle()
                return True
            turn_mouse(400)
            continue
        lost = 0
        off = d['cx'] - 1920
        frac = d['w'] / 3840.0
        log('      · %s %s %.2f 宽%.0f%% 偏%+d' % (name, d['label'], d['conf'], frac * 100, off))
        seen += 1
        if use_tech and not tech_used and frac > 0.04 and seen >= 2:
            log('      · 连续 %d 帧认到怪 → 放秘技 E' % seen)
            press('e', 0.9)
            tech_used = True
        if abs(off) > 150:
            # ★★ 2026-11-08 实测更正：真实关系是 Δ偏 = -1.2 × mx（SendInput 正值 = 目标左移），
            #   所以要把 off 拉回中心应该发 mx = +off / 1.2。
            #   旧代码用 off / K_TURN(2.2) —— 方向对但只有应有量的 55% ⇒
            #   目标偏 -1847 这种大偏移永远追不上，追两次就丢，日志表现为
            #   "锁定怪 → 这一帧没认到 → 直走 60 秒没结果" ✗。
            mx = max(-1600.0, min(1600.0, off / 1.2 * 0.9))
            turn_mouse(mx)
        elif frac < close_frac:
            walk(0.6)
        else:
            log('      · 够近 → F')
            press('f', 2.4)
            if on_station_picker() or area_passed():
                return True
            if is_event_ui():
                do_event_dialog()
                return True
            if in_battle():
                wait_battle()
                if '选择祝福' in '|'.join(_texts(ocr_items())):
                    pick_bless_inline()
                return True
            time.sleep(0.8)
    return on_station_picker() or area_passed()


def is_battle_screen(items=None):
    return in_battle(items)


# 区域类型（左上角那条「（n/13）第X位面-XX」里的 XX）
AREA_KINDS = ('战斗', '首领', '事件', '财富', '商店', '铸造', '休整', '空白',
              '精英', '冒险', '奖励', '异常')
NO_ENEMY_KINDS = ('休整', '空白', '商店', '财富', '铸造')


def area_kind(items=None):
    """从左上角 HUD 读区域类型（空串 = 读不到）。

    为什么必须读（2026-11-07 实测）：休整/空白区里**根本没有怪**，
      但 YOLO 会把场景误报成「普通怪 0.46」，脚本就去追空气、还乱放秘技 ✗。

    ★ 2026-11-08 改法：整屏 OCR 时这行字经常被拆成
      「（1/13）第一」+「位面-战斗」，甚至只剩「战斗」两个字的一部分 ⇒ 读成"未知" ✗。
      改成**裁左上角那条 HUD 区域、放大 2 倍再 OCR** ⇒ 命中率高得多 ✓。
    """
    import numpy as np
    import cv2
    if items is None:
        img = screenshot()
        crop = img[15:145, 110:1150]                      # 物理坐标，HUD 条
        crop = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2),
                          interpolation=cv2.INTER_CUBIC)
        items2 = ocr_items(crop)
    else:
        items2 = items
    joined = ''.join(t.strip() for t, s, _, _ in items2 if s >= 0.4)
    for k in AREA_KINDS:
        if k in joined:
            return k
    # OCR 仍可能只剩一个字（如「休整」读成「休」）→ 单字兜底
    for k in AREA_KINDS:
        if k[0] in joined:
            return k
    return ''


def charge_enemy(d, max_seconds=12.0):
    """锁定敌人后**一头撞过去**（commit 式），不再逐帧依赖 YOLO。

    为什么（2026-11-08 实测）：DU 里的怪 YOLO 检测**一走就丢** ——
      日志连续出现「怪 0.86 偏-263 → 这一帧没认到怪（第 1 次）→ … → 放弃」，
    逐帧伺服的结果是"锁定 3 次、一次都没打上" ✗。
    所以锁定后按最后已知方向连续前进 + 盲按 F，直到进战斗/出现站点卡为止 ✓。
    """
    off = d['cx'] - 1920
    if abs(off) > 200:
        turn_mouse(max(-1600.0, min(1600.0, off / 1.2 * 0.9)))
    press('e', 0.8)                  # 先放秘技（罩住怪）
    # ★ 2026-11-08：**扇形冲锋** —— 一次直冲经常撞空（怪在边沿、被柱子挡住、
    #   或我转向时它已经挪了位置）。改成 3 个方向各冲一小段：
    #   先朝当前方向，撞不到就往左/右各偏 ~40° 再冲，比"冲一次就放弃"稳得多 ✓
    # 次数排布：**前两次同向直冲**（加起来约 2/3 的时间都在朝怪走），
    #   之后才左右各偏一次。实测"每次只冲 2.3 秒就换方向"会一直够不着 10~15 米外的怪 ✗。
    for k, bias in enumerate((0, 0, 600, -1200)):
        if bias:
            turn_mouse(bias)
            log('      · 扇形第 %d 次（偏 %+d 鼠标像素）' % (k + 1, bias))
        if _charge_once(max_seconds / 3.0):
            return True
    log('      · 扇形三次都没撞到（怪可能跑开/在障碍后）')
    return False


def _charge_once(max_seconds=2.3):
    """朝当前方向冲一小段，撞到怪返回 True。"""
    t0 = time.time()
    while time.time() - t0 < max_seconds:
        if in_battle():
            log('      · 撞到怪 → 进战斗')
            wait_battle(timeout=420)
            txt = '|'.join(_texts(ocr_items()))
            if '选择祝福' in txt or '选择一项祝福' in txt:
                pick_bless_inline()
            return True
        if on_station_picker() or area_passed():
            return True
        walk(0.45)
        press('f', 0.45)
        if in_battle():
            log('      · F 进战斗')
            wait_battle(timeout=420)
            txt = '|'.join(_texts(ocr_items()))
            if '选择祝福' in txt or '选择一项祝福' in txt:
                pick_bless_inline()
            return True
    return False


def enemy_indicator():
    """顶部「等级NN 敌人名」= 附近有敌人（作者 2026-11-08 指出）。

    ★ 实测（2026-11-08 晚，首领区）：这个标识出现在 **y≈119、x≈1932（正上方居中）**，
      文字形如「等级88 深寒徘徊者」。
      ⚠️ 它**只是"附近有敌人"的提示，不给方向**（转身时它不动、始终居中）。
      但它比 YOLO 可靠得多：同场景 YOLO 只给 0.26~0.34 的玄学检测、而且看见「普通怪×1」
      也不去打 ✗；而标识稳定出现 ✓。
    返回敌人名（字符串）或 None。
    """
    import re as _re
    try:
        for t, s, cx, cy in ocr_items():
            if s >= 0.5 and cy < 280 and _re.search(r'等级\s*\d+', t):
                return t.strip()
    except Exception:
        pass
    return None


def engage_battle(max_seconds=45.0):
    """**有敌人标识时主动开打**：开 E（秘技）→ 边走边小转，让秘技圈罩住敌人 → 进战斗。

    作者 2026-11-08 原话："屏幕上方有敌人标识诶。然后你就开E然后移动，直到你的圈罩住敌人"。
    """
    ind = enemy_indicator()
    if ind is None:
        return False
    log('      · 顶部敌人标识: %s → 开 E 秘技并扫动' % ind)
    t0 = time.time()
    press('e', 0.9)
    step = 0
    while time.time() - t0 < max_seconds:
        if in_battle():
            log('      · ★ 进战斗了')
            return True
        step += 1
        pyautogui.keyDown('w')
        time.sleep(0.8)
        pyautogui.keyUp('w')
        # 每走一步转一点，让圈扫过周围（标识不给方向，只能扫）
        _send_input_move(700, 0)
        time.sleep(0.3)
        if in_battle():
            log('      · ★ 进战斗了（第 %d 步）' % step)
            return True
        if step % 3 == 0:
            ind = enemy_indicator()
            if ind is None:
                log('      · 敌人标识消失（可能走远/已进战斗）')
                if in_battle():
                    return True
            press('e', 0.7)         # 秘技可能有多次
    return in_battle()


def smart_clear_step():
    """按优先级看画面里有什么，做一件事。返回 True = 这一趟结束（过门/进界面）。

    优先级：事件框 > 出口门 > 沉浸奖励 > 怪 > 可破坏物
    """
    if on_station_picker() or area_passed():
        return True
    log('      · 区域类型: %s' % (area_kind() or '未知'))
    dets = yolo_detect(min_conf=0.4)
    if dets:
        cates = {}
        for cate, label, conf, cx, cy, w, h in dets:
            cates.setdefault(cate, 0)
            cates[cate] += 1
        log('      · YOLO 看到: %s' % ', '.join('%s×%d' % (k, v) for k, v in cates.items()))
    d = yolo_pick(CATE_EVENT, min_conf=0.4)
    if d is not None:
        return go_to_target(CATE_EVENT, '事件框', first=d)
    d = yolo_pick(CATE_REWARD, min_conf=0.4)
    if d is not None:
        return go_to_target(CATE_REWARD, '沉浸奖励', first=d)
    # ★★ 2026-11-08 晚：**有敌人标识就先打**（作者指出）。
    #   之前门在怪前面 ⇒ 首领区满地粉/黄气球被误判成门，白白追 60 秒，首领一次没打 ✗
    if enemy_indicator() is not None:
        if engage_battle():
            return True
    d = yolo_pick(CATE_DOOR, min_conf=0.3)
    if d is not None:
        return go_to_target(CATE_DOOR, '出口门', close_frac=0.14, first=d)
    # ★ 2026-11-08：门"未激活"= 这一区还有怪没清完。此时把门的方位记进日志，
    #   但动作仍然优先去清怪（清完门才会亮）。
    _off = yolo_pick(CATE_DOOR_OFF, min_conf=0.3)
    if _off is not None:
        log('      · 看到「下层入口未激活」偏%+d ⇒ 先清完怪门才会亮' % (_off['cx'] - 1920))
    kind = area_kind()
    if kind and kind not in NO_ENEMY_KINDS:
        # ★ 2026-11-08 放宽：原来 min_conf=0.55 / min_w=90 太严，
        #   实测这一区的怪只认到 0.42~0.78、宽度也偏小 ⇒ 看着有怪却从不去打 ✗。
        d = yolo_pick(CATE_ENEMY, min_conf=0.45, min_w=60)
        if d is not None:
            log('      · 锁定 %s conf=%.2f 偏%+d → 冲锋' % (d['label'], d['conf'], d['cx'] - 1920))
            return charge_enemy(d)
    elif kind:
        log('      · 区域类型=%s，不追怪（这里没怪，YOLO 常误报）' % kind)
    return False


def detect_tpl(name, min_score=0.6, scales=(0.5, 0.7, 0.85, 1.0, 1.2, 1.45)):
    """多尺度模板匹配（缩到 1/3 分辨率做，快），返回 dict 或 None。"""
    import cv2
    tpl0 = cv2.imread(os.path.join(TPL_DIR, name))
    if tpl0 is None:
        return None
    img = screenshot()
    SH = 3
    small = cv2.resize(img, (img.shape[1] // SH, img.shape[0] // SH), interpolation=cv2.INTER_AREA)
    best = None
    for s in scales:
        w = max(10, int(tpl0.shape[1] * s / SH))
        h = max(10, int(tpl0.shape[0] * s / SH))
        if w >= small.shape[1] or h >= small.shape[0]:
            continue
        tpl = cv2.resize(tpl0, (w, h), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        if mx >= min_score and (best is None or mx > best['score']):
            best = {'score': float(mx), 'x0': loc[0] * SH, 'y0': loc[1] * SH,
                    'w': w * SH, 'h': h * SH,
                    'cx': loc[0] * SH + (w * SH) // 2,
                    'cy': loc[1] * SH + (h * SH) // 2}
    return best


def approach_object(name, label, min_score=0.6, close_frac=0.12, max_seconds=40):
    """看见就往它走；近到占屏宽 close_frac 就按 F。返回 True = 出现「选择下一站」/过门。"""
    t0 = time.time()
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            return True
        d = detect_tpl(name, min_score=min_score)
        if d is None:
            # ★ 不能一丢就放弃：贴太近时模板会失真/被遮挡，往前顶一步 + 按 F 再试
            lost = globals().setdefault('_LOST', {})
            n = lost.get(name, 0) + 1
            lost[name] = n
            log('      · 视野里没有%s了（第 %d 次）→ 往前顶一步再按 F' % (label, n))
            walk(0.6)
            press('f', 2.0)
            if on_station_picker() or area_passed():
                lost[name] = 0
                return True
            if is_event_ui():
                do_event_dialog()
                lost[name] = 0
                return True
            if in_battle():
                wait_battle()
                lost[name] = 0
                return True
            if n >= 4:
                lost[name] = 0
                return False
            continue
        else:
            globals().setdefault('_LOST', {})[name] = 0
        off = d['cx'] - 1920
        frac = d['w'] / 3840.0
        log('      · %s score=%.2f 宽%.0f%% 偏%+d' % (label, d['score'], frac * 100, off))
        if abs(off) > 160:
            turn_mouse(off / K_TURN)
        elif frac < close_frac:
            walk(0.6)
        else:
            log('      · 到%s跟前 → F' % label)
            press('f', 2.5)
            if on_station_picker() or area_passed():
                return True
            if is_event_ui():
                do_event_dialog()
                return True
            if in_battle():
                wait_battle()
                if '选择祝福' in '|'.join(_texts(ocr_items())):
                    pick_bless_inline()
                return True
            time.sleep(0.8)
    return on_station_picker() or area_passed()


# ---- 界面状态（用 OCR 文字判定 ✓，比模板稳 ✓）----
# 每个元素是 (状态名, [特征文字...])，**按顺序**匹配，先中先回
STATE_RULES = [
    ('bless_choose',   ['选择祝福', '可选择1次祝福']),
    ('popup_blank',    ['点击空白处关闭']),
    ('pick_equation',  ['请选择方程', '选择方程']),
    ('pick_mask',      ['选择一张面具']),
    # ★ 2026-11-08：两种措辞都要认 ——『选择一张站点卡』/『选择站点卡』
    #   （后者出现在「奇迹=指定区域变为空白」的选择环节）。
    ('pick_station',   ['选择一张站点卡', '选择站点卡', '站点卡']),
    # ★ 2026-11-08：「奇迹」界面有**两种措辞** —— 以前只认『选择惊世奇迹』，
    #   实测这次是『选择你的奇迹』(+『愿力满盈』标题)，于是被误判成 event_dialog、
    #   然后一直走『清场』分支，卡在同一个界面上无限循环 ✗。
    ('pick_miracle',   ['选择惊世奇迹', '选择你的奇迹', '愿力满盈']),
    ('pause_panel',    ['继续进度', '暂离']),
    # ★ 2026-11-08：造物调试台（电饭煲上的包子）—— 按 F 会开这个面板，
    #   里面有「选择加权奇物」，**必须点右上 X 关掉**，否则流程卡住。
    ('device_curio',   ['选择加权奇物', '装配或调整加权奇物']),
    # ★ 2026-11-08：ESC 连按会把暂停面板关掉、停在**手机菜单**上（实测卡在这里出不来）
    ('phone_menu',     ['梦境护照', '均衡等级', '开拓等级']),
    ('settle',         ['结束并结算', '探索成功']),
    ('bless_choose',   ['选择祝福', '选择一项祝福']),
    ('event_dialog',   ['获得', '确定']),
    ('in_area',        ['差分宇宙', '位面']),
    ('main_menu',      ['开始游戏', '乐园漫记']),
    ('mode_select',    ['常规演算', '周期演算']),
]


def detect_state(verbose=True):
    """OCR 一遍屏幕，返回 (state_name, texts)。"""
    try:
        items = ocr_items()
    except Exception as e:
        log('      OCR 失败: %s' % e)
        return None, []
    texts = [t for t, s, _, _ in items if s >= 0.6]
    joined = '|'.join(texts)
    for name, keys in STATE_RULES:
        if any(k in joined for k in keys):
            if verbose:
                log('      当前界面: %s  (识别到 %d 段文字)' % (name, len(texts)))
            return name, texts
    if verbose:
        log('      当前界面: ???  识别到: %s' % (texts[:12],))
    return None, texts


# 模板路径（用 Learn 的 templates 目录统一管理 ✓）
TPL_DIR = r'D:\_PROGRAM_PythonProjects\Learn\tools\templates'
TPL_MASK = os.path.join(TPL_DIR, 'mask_card.png')          # 「选择一张面具」的面具卡（含左箭头）
TPL_BOARD = os.path.join(TPL_DIR, 'board.png')             # 大世界/副本里的事件板
TPL_DOOR = os.path.join(TPL_DIR, 'door5.png')             # 随意门


def pick_first_card(row=0, n_cols=1):
    """「选择一张面具 / 站点卡 / 惊世奇迹」——**优先用模板匹配** ✓

    ★ 2026-11-07 血泪：面具卡我手算 (840,1635) ✗ 实际 (340,1638) ✓ 偏了 500px。
      模板匹配 score=1.0000 一击命中 ✓ —— **坐标永远别写死** ✗。
    """
    pos = find_template(TPL_MASK, threshold=0.70)
    if pos is not None:
        log('      · 模板找到面具卡 @ (%d,%d) score=%.3f' % (pos[0], pos[1], pos[2]))
        # ★ 实测：点模板中心 / 点名字标签都**选不中** ✗
        #   只有点**卡片图标**（模板框左上区域）才有效 ✓ —— 2026-11-07 实测 (300,1600) 成功 ✓
        ix, iy = pos[0] - 40, pos[1] - 38
        log('      · 点卡片图标 (%d,%d)' % (ix, iy))
        click_phys(ix, iy, settle=1.5)
        return True
    log('      ⚠ 模板没匹配到面具卡，退回按位置猜（可能不准 ✗）')
    spots = [(340, 1638, '左'), (1920, 1638, '中'), (3000, 1638, '右')]
    x, y, tag = spots[row % 3]
    log('      · 猜%s边那张' % tag)
    click_phys(x, y, settle=1.2)
    return True


# ---- 清场：把区域里的互动都点掉，门才会醒 ----
def _send_input_move(dx, dy=0):
    """SendInput 注入相对鼠标位移（= BetterGI 的 MoveMouseBy）。

    ★★ 为什么要换掉 mouse_event（2026-11-07 深夜实测）：
      mouse_event 在这台机器上**十次有九次被吞**（转完画面完全不动）；
      SendInput 每次返回 1 且确实转动。这一条是整晚转向不稳的真正原因。
    """
    import ctypes
    from ctypes import wintypes
    ulong_ptr = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG),
                    ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                    ('time', wintypes.DWORD), ('dwExtraInfo', ulong_ptr)]

    class _U(ctypes.Union):
        _fields_ = [('mi', MOUSEINPUT), ('pad', ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [('type', wintypes.DWORD), ('u', _U)]

    inp = INPUT()
    inp.type = 0                     # INPUT_MOUSE
    inp.u.mi.dx = int(dx)
    inp.u.mi.dy = int(dy)
    inp.u.mi.mouseData = 0
    inp.u.mi.dwFlags = 0x0001        # MOUSEEVENTF_MOVE
    inp.u.mi.time = 0
    inp.u.mi.dwExtraInfo = 0
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def turn_mouse(mx):
    """转视角：mx>0 = 视角**往左**（画面内容右移）。实测正负见下。

    实测（2026-11-07）：send_move(+400) → 门 x 由 79 变 328（内容右移）⇒ 正是视角左转。
    """
    mx = int(mx)
    if mx == 0:
        return
    _send_input_move(mx, 0)
    time.sleep(0.45)


def turn(dx_px=340):
    """兼容旧调用：dx_px 是"往左为正"的屏像素估计（内部转成 turn_mouse）。"""
    turn_mouse(-dx_px)


# ---- 交互提示 / 事件界面 / 清场（2026-11-07 晚重写，逐条实测）----
# 实测依据：
#   ① 门上有「Z Z」= 门在睡觉 → 区域里还有互动没做完，按 F 无效（笔记 §7）
#   ② 互动点走近会弹黑底提示框 `[F] 事件` / `[F] 随意门`，**OCR 能读到来判断** ✓
#   ③ 走路一次 7 秒会**冲过**事件板（实测事件板在第 8 小步 ≈ 3.2 秒处）→ 必须小步走
#   ④ 事件界面：右侧剧情 + 右下 ▼ 翻页 → 出现「选择」→ 选项（每个 2 行）→「确定」
#      →「获得站点卡 / 点击空白处关闭」→ 可能还有一段剧情 → 再 ▼ 才退出
#   ⑤ 「确定」按钮的 y 会随右栏内容重排而漂移 40~50px → **每次点前重新 OCR**

PROMPT_WORDS = ('事件', '随意门', '调查', '贵重战利品', '开启',
                '造物调试台', '维护市容', '补给箱')
DIALOG_NEXT = (3157, 1452)         # 事件界面右下的 ▼（实测）
BLANK_POINT = (1920, 1900)         # "点击空白处关闭" 用的空白处

EVENT_IGNORE = ('差分宇宙', '事件', '模拟宇宙', '查看祝福与方程', '规则说明',
                'UID', '确定', '确认', '选择', '获得站点卡', '获得数量',
                '重随', '重抽', '待抽区', '弃牌区')

# 站点类型优先级（作者 2026-11-07 定：商店/空白最省事，事件/铸造最耗操作）
AREA_RANK = {'商店': 0, '空白': 1, '战斗': 2, '财富': 3,
             '铸造': 4, '事件': 5, '精英': 6, '冒险': 7}


def _texts(items, min_score=0.55):
    return [t.strip() for t, s, _, _ in items if s >= min_score and t.strip()]


# 交互提示框的**固定屏幕位置**（实测 2026-11-07）：
#   「[F] 事件」出现过 (2422,1223) / (2420,1223) / (2439,1225) —— 三次数值几乎一样，
#   说明它不是"贴着物体画"的，而是**固定 HUD 位**。
#   ⚠️ 反例：「造物调试台」这类**世界浮空标签**也在 PROMPT_WORDS 里（笔记记过它代表可交互），
#   但它的坐标是 (3650,540) / (2728,611) —— 离得很远。不加位置约束就会被它骗，
#   脚本会对着空气狂按 F（实测空转 400 多秒 ✗）。
PROMPT_BOX = (1980, 1040, 2920, 1420)     # x1, y1, x2, y2


def find_prompts():
    """找**交互提示框**（黑底 `[F] xxx`）。返回 [(词, x, y)]。"""
    x1, y1, x2, y2 = PROMPT_BOX
    hits = []
    for t, s, cx, cy in ocr_items():
        tt = t.strip()
        if s < 0.55:
            continue
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        if tt in PROMPT_WORDS:
            hits.append((tt, int(cx), int(cy)))
    return hits


def is_world(items=None):
    """在区域里（不是全屏界面）：左上角有位面计数，或右上「状态效果」。"""
    items = items if items is not None else ocr_items()
    txt = '|'.join(_texts(items))
    return ('位面' in txt) or ('状态效果' in txt)


def is_event_ui(items=None):
    """事件全屏界面：左上「差分宇宙 / 事件」，且不是区域。"""
    items = items if items is not None else ocr_items()
    txt = '|'.join(_texts(items))
    return ('差分宇宙' in txt) and ('事件' in txt) and not is_world(items)


def area_passed(items=None):
    """是不是"已经过门/区域结束"了。

    实测（2026-11-07 晚）：过门**不一定**弹「选择下一站」——
      6/13 战斗区就是**直接穿过去**，然后弹「机铠面具 · 面具已升级 / 获得愿力」，
      再往后才是「愿力满盈 / 选择你的奇迹」。
    所以这几个界面都要当成"这一趟走完了"，交给下一轮的状态机去点。
    """
    items = items if items is not None else ocr_items()
    txt = '|'.join(_texts(items))
    for k in ('选择下一站', '选择一张站点卡', '面具已升级',
              '愿力满盈', '选择你的奇迹', '获得愿力'):
        if k in txt:
            return True
    return False


def on_station_picker(items=None):
    """已在「选择下一站」界面。"""
    items = items if items is not None else ocr_items()
    txt = '|'.join(_texts(items))
    return ('选择下一站' in txt) or ('选择一张站点卡' in txt)


def _score_option(text):
    """选项打分：站点类型优先（商店最省事），同级再比等级，带信标略降。"""
    import re
    rank = 9
    for k, v in AREA_RANK.items():
        if ('【%s】' % k) in text:
            rank = v
            break
    lv = 0
    mt = re.search(r'等级为\s*(\d+)', text)
    if mt:
        lv = int(mt.group(1))
    if '信标' in text:
        lv -= 1
    return -rank * 100 + lv


def _collect_options(items):
    """把右侧面板里成簇的文本当作选项（每个选项 = 标题行 + 收益行）。"""
    lines = []
    for t, s, cx, cy in items:
        tt = t.strip()
        if s < 0.6 or len(tt) < 2 or cx < 2500:
            continue
        if not (380 <= cy <= 1800):
            continue
        if any(k in tt for k in EVENT_IGNORE):
            continue
        lines.append((cy, cx, tt))
    lines.sort()
    clusters = []
    for cy, cx, tt in lines:
        if clusters and cy - clusters[-1][-1][0] <= 70:
            clusters[-1].append((cy, cx, tt))
        else:
            clusters.append([(cy, cx, tt)])
    opts = []
    for c in clusters:
        joined = ' '.join(x[2] for x in c)
        if not any(k in joined for k in ('❇', '获得')):
            continue
        opts.append({'text': joined, 'label': c[0][2],
                     'x': c[0][1], 'y': c[0][0]})
    return opts


def do_event_dialog(max_steps=20):
    """事件全屏界面的通用推进器。返回 True = 已回到区域（事件结束）。"""
    for _ in range(max_steps):
        items = ocr_items()
        joined = '|'.join(_texts(items))
        if '选择下一站' in joined:
            return True
        if is_world(items):
            log('      · 事件结束（已回到区域）')
            return True
        if '点击空白处关闭' in joined:
            log('      · 点空白处关掉「获得」弹窗')
            click_phys(*BLANK_POINT, settle=0.6)
            time.sleep(1.4)
            continue

        # ① 先认「确定」：只有已经选好选项时它才出现，且坐标会漂移 → 每次重读
        btn = None
        for t, s, cx, cy in items:
            if s >= 0.6 and t.strip() in ('确定', '确认') and cx > 3000:
                btn = (int(cx), int(cy))
                break
        if btn:
            log('      · 点「确定」@ (%d,%d)' % btn)
            click_phys(btn[0], btn[1], settle=1.0)
            time.sleep(2.0)
            continue

        # ② 选项列表（≥2 个才认，避免把剧情正文当成选项）
        opts = _collect_options(items)
        if len(opts) >= 2:
            best = max(opts, key=lambda o: _score_option(o['text']))
            log('      · 选「%s」@ (%d,%d)' % (best['label'], best['x'], best['y']))
            click_phys(best['x'], best['y'] + 15, settle=1.0)
            time.sleep(2.0)
            continue

        # ③「选择」按钮（翻完页出现 / 选完后继续）
        sel = None
        for t, s, cx, cy in items:
            if s >= 0.6 and t.strip() == '选择':
                sel = (int(cx), int(cy))
                break
        if sel:
            log('      · 点「选择」@ (%d,%d)' % sel)
            click_phys(sel[0], sel[1], settle=1.0)
            time.sleep(2.0)
            continue

        # ④ 都没有 → 翻页
        click_phys(*DIALOG_NEXT, settle=0.5)
        time.sleep(1.5)
    log('      ⚠ 事件界面 %d 步没走完' % max_steps)
    return False


def interact_here():
    """对当前提示按 F 并按结果处理。返回 True = 已弹「选择下一站」。"""
    press('f', 2.6)
    if on_station_picker():
        return True
    if is_event_ui():
        log('      · 进了事件界面')
        do_event_dialog()
    return on_station_picker()


BATTLE_WORDS = ('弱点', '回合', '自动战斗', '战斗开始', '技能', '终结技', '战技点')


def in_battle(items=None):
    """是不是在战斗里（头顶弱点字/回合/自动战斗按钮）。"""
    items = items if items is not None else ocr_items()
    txt = '|'.join(_texts(items))
    return any(k in txt for k in BATTLE_WORDS)


def wait_battle(timeout=420):
    """战斗开始后等它打完（自动战斗）。

    实测（2026-11-07 晚）：放秘技 E 后往前走一步就命中怪（画面出现「弱点」）→
    战斗自动打完 → 弹「你战胜了普通敌人，可选择 1 次祝福」→「选择祝福」界面。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        items = ocr_items()
        txt = '|'.join(_texts(items))
        if ('选择祝福' in txt) or ('战胜' in txt) or ('点击空白处' in txt):
            log('      · 战斗结束（%.0f 秒）' % (time.time() - t0))
            return True
        if not in_battle(items) and ('位面' in txt or '状态效果' in txt):
            log('      · 已回到区域（%.0f 秒）' % (time.time() - t0))
            return True
        time.sleep(3)
    log('      ⚠ 等战斗超时 %d 秒' % int(timeout))
    return False


def pick_bless_inline():
    """战斗后顺手领一次祝福（优先带书标的卡，没有就选第 1 张）。"""
    badge = find_template(os.path.join(TPL_DIR, 'badge_book.png'), threshold=0.65)
    col_x = badge[0] if badge is not None else 966
    log('      · 选祝福（x=%d）' % col_x)
    click_phys(col_x, 1300, settle=1.3)
    time.sleep(1.6)
    if not click_text('确', tries=5, interval=1.2):
        click_phys(3374, 1912, settle=1.2)
    time.sleep(7.0)


def frame_rotation(a, b):
    """两帧之间相机转了**多少度**（正 = 画面内容整体左移，即视角右转）。

    为什么不用相位相关（实测 2026-11-07）：
      相机绕自身旋转，画面是**旋转场**不是平移场 → phaseCorrelate 直接解成 0 ✗
      （两帧平均差 35.3，dx 却是 -0）。
    做法：ORB 提特征 → BFMatcher 匹配 → estimateAffinePartial2D → 从矩阵读角度。
    返回 (角度, 置信度=内点数)；匹配太少时角度为 0、置信度 0。
    """
    import numpy as np
    import cv2
    ga = cv2.cvtColor(a[300:1700, :, :], cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b[300:1700, :, :], cv2.COLOR_BGR2GRAY)
    ga = cv2.resize(ga, (960, 350), interpolation=cv2.INTER_AREA)
    gb = cv2.resize(gb, (960, 350), interpolation=cv2.INTER_AREA)
    orb = cv2.ORB_create(1200)
    ka, da = orb.detectAndCompute(ga, None)
    kb, db = orb.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return 0.0, 0
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(da, db)
    if len(matches) < 12:
        return 0.0, 0
    matches = sorted(matches, key=lambda m: m.distance)[:300]
    pa = np.float32([ka[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    pb = np.float32([kb[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    M, inliers = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC,
                                             ransacReprojThreshold=3.0)
    if M is None:
        return 0.0, 0
    ang = float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
    inl = int(inliers.sum()) if inliers is not None else 0
    return ang, inl


_DOOR_LAST = {'cx': None, 'miss': 0}


def detect_door_cands(min_score=0.60, scales=(0.35, 0.45, 0.55, 0.7, 0.9, 1.1, 1.4)):
    """列出**所有**疑似门的候选（多尺度 + 连通域去重），返回按分数排序的列表。

    为什么需要"多候选"（2026-11-07 深夜实测）：
      场上常有两三个粉色物体，只取最高分会导致目标帧间跳变
      （实测在 -200 和 +727 之间来回摆，爬山法永远收敛不了 ✗）。
      拿到候选列表后，用**时间一致性**（优先离上一帧 ≤400px 的）就能锁住同一扇门 ✓。
    """
    import numpy as np
    import cv2
    tpl0 = cv2.imread(os.path.join(TPL_DIR, 'door5.png'))
    if tpl0 is None:
        return []
    img = screenshot()
    SH = 3
    small = cv2.resize(img, (img.shape[1] // SH, img.shape[0] // SH),
                       interpolation=cv2.INTER_AREA)
    cands = []
    for s in scales:
        w = max(12, int(tpl0.shape[1] * s / SH))
        h = max(12, int(tpl0.shape[0] * s / SH))
        if w >= small.shape[1] or h >= small.shape[0]:
            continue
        tpl = cv2.resize(tpl0, (w, h), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED)
        mask = (res >= min_score).astype(np.uint8)
        if not mask.any():
            continue
        n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, n):
            x0, y0, bw, bh, area = stats[i]
            if area < 4:
                continue
            sub = res[y0:y0 + bh, x0:x0 + bw]
            _, mx, _, loc = cv2.minMaxLoc(sub)
            cx = (x0 + loc[0]) * SH + (w * SH) // 2
            cy = (y0 + loc[1]) * SH + (h * SH) // 2
            cands.append({'score': float(mx), 'cx': int(cx), 'cy': int(cy),
                          'w': w * SH, 'h': h * SH})
    cands.sort(key=lambda c: -c['score'])
    return cands[:8]


# ---- 转向修正系数（2026-11-08 实测重定）----
# 实测：Δ偏 ≈ -1.05 ~ -1.2 × Δ鼠标。要把偏拉回 0 必须发 mx = off / K。
# ⚠️ **K 太小时会震荡**：旧值 K=1.2 配 damping 0.9 ≈ 0.75 倍修正，实测收敛不了
#    （偏 -258 → -368 → -528 → -768 单调变差）；实测 K=2.4 / damping 0.85
#    ≈ 0.35 倍修正稳定收敛（-258 → -148 → -86 → -50 → 居中 ✓）。
#    **宁可欠修正，不要过修正。**
TURN_K = 2.4
TURN_DAMP = 0.85


def turn_to_center_of(finder, name='目标', tol=60, max_iter=16, sweep=140):
    """**铁律**：要去某个目标，必须先把目标转到画面正中心，再直走。

    ★ 2026-11-08 晚加**"转了没效果"守卫**：实测角色卡在墙边时，第三人称相机会被挤到
      角色身上（画面 60% 是人脸），此时
        · 转视角**画面完全不变**（日志：偏 -1155 → 转 -250 → 偏 -1155 → … 12 次都一样）
        · 模板还会在静态画面上误匹配
      守卫做法：连续两次转向后偏移几乎没变（|Δ| < 25）⇒ 判定"相机被顶住"，
      **按 S 后退**把镜头拉开再继续。这是这一区所有识图失效的真正根因。
    """
    last_off = None
    no_effect = 0
    for _ in range(max_iter):
        # ★ 中位数平滑：模板匹配在 ±60 之间抖（实测 -174 → -114 → -222 → -138…），
        #   单帧偏移根本收敛不了 ⇒ 连采 3 帧取中位再算修正。
        _offs = []
        for _k in range(3):
            _dd = finder()
            if _dd is not None:
                _offs.append(_dd['cx'] - 1920)
            time.sleep(0.12)
        if not _offs:
            _send_input_move(sweep, 0)
            time.sleep(0.3)
            continue
        _offs.sort()
        off = _offs[len(_offs) // 2]
        if abs(off) <= tol:
            log('      · %s 已居中（偏 %+d）' % (name, off))
            return True
        if last_off is not None and abs(off - last_off) < 25:
            no_effect += 1
            if no_effect >= 2:
                log('      · 转了没效果（偏 %+d 不变）⇒ 相机被顶住 → 后退拉开' % off)
                pyautogui.keyDown('s')
                time.sleep(1.3)
                pyautogui.keyUp('s')
                time.sleep(0.6)
                no_effect = 0
                last_off = None
                continue
        else:
            no_effect = 0
        last_off = off
        mx = max(-250.0, min(250.0, off / TURN_K * TURN_DAMP))
        if int(mx) == 0:
            mx = 1.0 if off > 0 else -1.0
        log('      · %s 偏 %+d → 转 %+d' % (name, off, int(mx)))
        _send_input_move(int(mx), 0)
        time.sleep(0.45)
    return False


# 模板匹配的时间一致性基准（跨调用保留）
_TPL_LAST = {'cx': None, 'miss': 0}


# ---------------- 全景兜底（作者 2026-11-08 方案）----------------
# 定位：**找不到目标时**才走这条路。
# 算法（2026-11-08 实测选定）：
#   ① `cv2.Stitcher_SCANS` —— 假设**纯绕光心旋转**，不需要估相机内参
#      （默认 PANORAMA 模式在本场景直接 `status=3 ERR_CAMERA_PARAMS_ADJUST_FAIL` ✗，
#        SCANS 则 2.9 秒成功、输出 1973×1002 ≈ 2.2 屏宽，几何正确 ✓）
#   ② 降级：ORB 特征 → RANSAC 单应 → **只取中位平移量累加**
#      （⚠️ 不要连乘 25 个单应：实测漂移到 23104×15022 的画布，直接废掉 ✗）
#   采样要点：裁掉 HUD/队伍栏/技能栏（CROP），每帧转固定步长（实测 240 鼠标≈123 px 位移）。
PANO_CROP = (0.28, 0.86, 0.08, 0.80)


def capture_rotation_frames(n=22, step=240, width=900, abort_on_battle=True):
    """转一圈采样（只取纯世界区域）。返回 [ndarray, ...]。"""
    import cv2
    frames = []
    for _ in range(n):
        img = screenshot()
        if abort_on_battle and in_battle():
            break
        H, W = img.shape[:2]
        c = img[int(H * PANO_CROP[2]):int(H * PANO_CROP[3]),
                int(W * PANO_CROP[0]):int(W * PANO_CROP[1])]
        frames.append(cv2.resize(c, (width, int(c.shape[0] * width / c.shape[1])),
                                 interpolation=cv2.INTER_AREA))
        _send_input_move(step, 0)
        time.sleep(0.4)
    return frames


def stitch_panorama(frames, prefer='orb'):
    """全景拼接。`prefer='orb'`（默认）走 ORB 位移累加，`'scans'` 走 Stitcher_SCANS。

    ★ 2026-11-08 晚实测定案：**闭环伺服要的是 ORB 位移条，不是 SCANS 全景**。
      * `Stitcher_SCANS`：出图漂亮（几何对、有纵深），但
        ① 帧数一多就"假成功"（56 帧只拼出 1058 px，等于一帧）；
        ② 柱面投影让 x 与转角**非线性**，没法用来算要转多少 ✗
      * ORB + 中位位移累加：x **严格线性**于转角（x = 累积位移），
        56 帧 × 300 鼠标稳定得到 3421 px（3.8 屏），每帧都有 150+ 内点 ✓
      ⇒ 伺服用 ORB 条；`scans` 只作"给人看的一张图"。
    """
    import cv2
    import numpy as np
    if len(frames) < 4:
        return None, 'too-few'

    def _orb():
        orb = cv2.ORB_create(4000)
        kps, des = [], []
        for im in frames:
            k, d = orb.detectAndCompute(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), None)
            kps.append(k)
            des.append(d)
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        shifts = [0.0]
        for i in range(len(frames) - 1):
            dx = None
            if des[i] is not None and des[i + 1] is not None:
                mt = bf.match(des[i], des[i + 1])
                if len(mt) >= 12:
                    mt = sorted(mt, key=lambda x: x.distance)[:600]
                    src = np.float32([kps[i][x.queryIdx].pt for x in mt])
                    dst = np.float32([kps[i + 1][x.trainIdx].pt for x in mt])
                    Hm, mask = cv2.findHomography(dst, src, cv2.RANSAC, 3.0)
                    if Hm is not None and mask is not None:
                        good = mask.ravel().astype(bool)
                        dd = src - dst
                        if good.any():
                            dx = float(np.median(dd[good, 0]))
            shifts.append(shifts[-1] + (dx if dx is not None else frames[i].shape[1] * 0.22))
        total = shifts[-1]
        if abs(total) < frames[0].shape[1] * 0.5:
            return None
        w, hh = frames[0].shape[1], frames[0].shape[0]
        cw = int(abs(total) + w + 8)
        canvas = np.zeros((hh, cw, 3), np.uint8)
        for im, s in zip(frames, shifts):
            x = int(round(-s if total < 0 else s))
            x = max(0, min(cw - w, x))
            roi = canvas[:, x:x + w]
            mk = roi.sum(axis=2) == 0
            roi[mk] = im[mk]
        return canvas

    if prefer == 'scans':
        try:
            st = cv2.Stitcher_create(cv2.Stitcher_SCANS)
            status, pano = st.stitch(frames)
            if status == 0 and pano is not None and pano.shape[1] > frames[0].shape[1] * 1.3:
                return pano, 'scans'
            log('      · Stitcher_SCANS 未真正拼接（status=%d 宽=%s）'
                % (status, pano.shape[1] if pano is not None else '-'))
        except Exception as e:                  # noqa: BLE001
            log('      · Stitcher_SCANS 异常: %s' % e)

    canvas = _orb()
    if canvas is not None:
        return canvas, 'orb-shift'

    if prefer != 'scans':
        try:
            st = cv2.Stitcher_create(cv2.Stitcher_SCANS)
            status, pano = st.stitch(frames)
            if status == 0 and pano is not None and pano.shape[1] > frames[0].shape[1] * 1.3:
                return pano, 'scans'
        except Exception:                       # noqa: BLE001
            pass
    return None, 'fail'


def build_panorama(n=40, step=420, width=900):
    """拍一圈 + 拼全景。返回 (pano, method)。"""
    frames = capture_rotation_frames(n=n, step=step, width=width)
    log('      · 全景采样 %d 帧' % len(frames))
    pano, method = stitch_panorama(frames)
    if pano is not None:
        # 覆盖度：一屏约 60~70° FOV ⇒ 360° 至少要 5.5~6 屏宽
        cover = pano.shape[1] / float(width)
        log('      · 全景拼接成功（%s，%dx%d，约 %.1f 屏宽 ≈ %.0f°）'
            % (method, pano.shape[1], pano.shape[0], cover, cover * 62.0))
        if cover < 5.0:
            log('      · ⚠ 全景覆盖不足 360°（%.1f 屏 < 5 屏）⇒ 闭环会失效，'
                '需加大帧数/步长（实测 40 帧 × 420 鼠标 ≈ 360°）' % cover)
    else:
        log('      · 全景拼接失败（%s）' % method)
    return pano, method


def _find_template_in(canvas, tpl_name, min_score=0.42, widths=(60, 90, 130, 180, 250, 340)):
    """在一张长图（全景）里找模板，返回 (score, cx) 或 None。"""
    import cv2
    tpl0 = cv2.imread(os.path.join(TPL_DIR, tpl_name))
    if tpl0 is None or canvas is None:
        return None
    g = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    best = None
    for tw in widths:
        sc = tw / float(tpl0.shape[1])
        t = cv2.resize(cv2.cvtColor(tpl0, cv2.COLOR_BGR2GRAY), None, fx=sc, fy=sc,
                       interpolation=cv2.INTER_AREA)
        if t.shape[0] >= g.shape[0] or t.shape[1] >= g.shape[1]:
            continue
        res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
        _mn, mx, _ml, loc = cv2.minMaxLoc(res)
        if best is None or mx > best[0]:
            best = (float(mx), int(loc[0] + t.shape[1] // 2))
    if best is None or best[0] < min_score:
        return None
    return best


def _locate_in_pano(pano):
    """把当前实时画面定位到全景里，返回全景 x（找不到返回 None）。
       做法：取实时画面中间一条竖带当模板，在全景里滑窗匹配。"""
    import cv2
    if pano is None:
        return None
    img = screenshot()
    H, W = img.shape[:2]
    # 实时画面的中间条带（避开 HUD 与角色）
    band = img[int(H * 0.18):int(H * 0.50), int(W * 0.42):int(W * 0.58)]
    if band.size == 0:
        return None
    ph = pano.shape[0]
    bh = int(ph * 0.55)
    band = cv2.resize(cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), (int(band.shape[1] * bh / band.shape[0]), bh),
                      interpolation=cv2.INTER_AREA)
    gp = cv2.cvtColor(pano, cv2.COLOR_BGR2GRAY)
    if band.shape[0] >= gp.shape[0] or band.shape[1] >= gp.shape[1]:
        return None
    res = cv2.matchTemplate(gp, band, cv2.TM_CCOEFF_NORMED)
    _mn, mx, _ml, loc = cv2.minMaxLoc(res)
    if mx < 0.35:
        return None
    return (int(loc[0] + band.shape[1] // 2), float(mx))


def sweep_with_heading(n=40, step=500, width=900):
    """转一圈：边转边用 ORB 测每帧位移，返回 (canvas, cur_x, mpp)。

    * `cur_x`   = 拍完之后**当前朝向**在全景条里的 x（航位推算，不靠事后匹配）
    * `mpp`     = 鼠标 / 全景px（本次实测，**每圈重算**，避免写死的标定漂移）

    ★ 为什么用航位推算而不是"每帧在全景里盲搜"（2026-11-08 晚实测）：
      盲搜 `_locate_in_pano`（单条竖带走模板匹配）分数只有 0.35~0.5、位置在
      2835/338/2853 之间乱跳 ⇒ 闭环根本收敛不了 ✗。
      而 ORB 帧间位移每帧都有 150+ 内点、很稳 ✓ 累加起来就能知道自己在哪。
      实测：40 帧 × 500 鼠标 ⇒ 4714 px = 5.2 屏 ≈ 325°，**一屏 ≈ 3818 鼠标**。
    """
    import cv2
    import numpy as np
    crops, grays, shifts = [], [], [0.0]
    prev = None
    for _ in range(n):
        img = screenshot()
        if in_battle():
            break
        H, W = img.shape[:2]
        c = img[int(H * PANO_CROP[2]):int(H * PANO_CROP[3]),
                int(W * PANO_CROP[0]):int(W * PANO_CROP[1])]
        c = cv2.resize(c, (width, int(c.shape[0] * width / c.shape[1])), interpolation=cv2.INTER_AREA)
        g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
        crops.append(c)
        grays.append(g)
        if prev is not None:
            dx = _orb_shift(prev, g)
            shifts.append(shifts[-1] + (dx if dx is not None else 0.22 * width))
        prev = g
        _send_input_move(step, 0)
        time.sleep(0.4)
    if len(crops) < 4:
        return None, 0.0, 5.0
    total = shifts[-1]
    if abs(total) < width * 0.5:
        return None, 0.0, 5.0
    w, hh = width, crops[0].shape[0]
    cw = int(abs(total) + w + 8)
    canvas = np.zeros((hh, cw, 3), np.uint8)
    for im, s in zip(crops, shifts):
        x = int(round(-s if total < 0 else s))
        x = max(0, min(cw - w, x))
        roi = canvas[:, x:x + w]
        mk = roi.sum(axis=2) == 0
        roi[mk] = im[mk]
    mpp = (len(crops) * step) / abs(total)          # 鼠标/px
    cur_x = abs(total) if total > 0 else 0.0        # 拍完回到起点附近
    log('      · 全景 %.0f px = %.1f 屏 ≈ %.0f°，一屏≈%.0f 鼠标'
        % (abs(total), abs(total) / width, abs(total) / width * 62.0, mpp * width))
    return canvas, cur_x, mpp


def _orb_shift(gray_a, gray_b):
    """两图水平中位位移（ORB + 单应 + RANSAC）。"""
    import cv2
    import numpy as np
    orb = cv2.ORB_create(3000)
    ka, da = orb.detectAndCompute(gray_a, None)
    kb, db = orb.detectAndCompute(gray_b, None)
    if da is None or db is None or len(ka) < 10 or len(kb) < 10:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    mt = bf.match(da, db)
    if len(mt) < 12:
        return None
    mt = sorted(mt, key=lambda x: x.distance)[:600]
    src = np.float32([ka[x.queryIdx].pt for x in mt])
    dst = np.float32([kb[x.trainIdx].pt for x in mt])
    Hm, mask = cv2.findHomography(dst, src, cv2.RANSAC, 3.0)
    if Hm is None or mask is None:
        return None
    good = mask.ravel().astype(bool)
    if not good.any():
        return None
    return float(np.median((src - dst)[good, 0]))


def find_pink_in_canvas(canvas):
    """在长图里找"品红门板"（门最强特征：品红 + 白眼睛）。返回候选 list。"""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(canvas, cv2.COLOR_BGR2HSV)
    pink = cv2.inRange(hsv, (140, 50, 55), (178, 255, 255))
    pink = cv2.morphologyEx(pink, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(pink, 8)
    out = []
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        if area < 500 or hh < 35:
            continue
        ar = w / float(max(1, hh))
        if not (0.5 <= ar <= 1.7):
            continue
        roi = canvas[y:y + hh, x:x + w]
        rh = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        wf = float(cv2.inRange(rh, (0, 0, 180), (180, 80, 255)).mean()) / 255.0
        yf = float(cv2.inRange(rh, (15, 90, 150), (40, 255, 255)).mean()) / 255.0
        # ★★ 2026-11-09 实测：全景里曾选中「白0.51 黄0.00」的**纯白物体**当门，
        #   转过去后卡住不动 ✗。真门在截图里是「白0.24 黄0.014」。
        #   判据：有黄框（≥0.01）或 白色适中（0.10~0.35）；纯白 >0.35 一律不要。
        if not (yf >= 0.01 or (0.10 <= wf <= 0.35)):
            continue
        out.append({'x': int(x + w // 2), 'w': int(w), 'h': int(hh), 'white': wf, 'yellow': yf,
                    'score': yf * 20 + wf * 8 + min(area, 8000) / 8000.0})
    out.sort(key=lambda c: -c['score'])
    return out


def goto_by_panorama(n=40, step=500, max_iter=12):
    """全景兜底（航位推算版）：拍一圈 → 在条里找门 → 按"转了多少就累加多少"转到门 → 直走+F。"""
    t0 = time.time()
    canvas, cur_x, mpp = sweep_with_heading(n=n, step=step)
    if canvas is None:
        log('      · 全景采样/拼接失败')
        return False
    cands = find_pink_in_canvas(canvas)
    if not cands:
        log('      · 全景里没有粉色候选')
        return False
    door = cands[0]
    log('      · 全景里选门 x=%d（白%.2f 黄%.2f 分%.2f），当前朝向 x=%.0f'
        % (door['x'], door['white'], door['yellow'], door['score'], cur_x))
    for it in range(max_iter):
        if time.time() - t0 > 240:
            break
        err = door['x'] - cur_x
        if abs(err) <= 25:
            log('      · 航位推算已对准（误差 %.0f px）' % err)
            break
        mouse = int(max(-800, min(800, err * mpp)))
        if mouse == 0:
            mouse = 1 if err > 0 else -1
        _send_input_move(mouse, 0)
        time.sleep(0.45)
        cur_x += mouse / mpp
        log('      · 转 %+d（剩 %.0f px）' % (mouse, door['x'] - cur_x))
    for seg in range(10):
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            wait_battle(timeout=420)
            return True
        pyautogui.keyDown('w')
        time.sleep(1.0)
        pyautogui.keyUp('w')
        time.sleep(0.2)
        for _ in range(2):
            press('f', 0.8)
            if on_station_picker() or area_passed():
                log('      · 全景兜底过门成功 ✓')
                return True
            if is_event_ui():
                do_event_dialog()
                break
    return on_station_picker() or area_passed()


# 门模板集合：每个区域的门样式不同，**一套模板不够**。
# 2026-11-08 实测：door9（财富区裁的）在铸造区只有 0.32 不达标；
# 而 door_casting（铸造区四方向图里裁的，190×268）自匹配 1.000 ✓。
DOOR_TEMPLATES = ('door9.png', 'door_casting.png')


def find_door_by_tpl(min_score=0.42, near=520):
    """用**真门模板组**多尺度匹配找门，带时间一致性。

    返回 {'score','cx','cy','w','h','src'} 或 None。
    """
    import cv2
    import numpy as np
    img = screenshot()
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (1920, 1080), interpolation=cv2.INTER_AREA)
    hits = []
    for tname in DOOR_TEMPLATES:
        tpl0 = cv2.imread(os.path.join(TPL_DIR, tname))
        if tpl0 is None:
            continue
        tg0 = cv2.cvtColor(tpl0, cv2.COLOR_BGR2GRAY)
        for tw in (60, 80, 105, 135, 175, 230, 300, 380):
            sc = tw / float(tg0.shape[1])
            t = cv2.resize(tg0, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
            if t.shape[0] >= g.shape[0] or t.shape[1] >= g.shape[1]:
                continue
            res = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
            thr = max(0.30, min_score - 0.12)
            ys, xs = np.where(res >= thr)
            for i in range(0, len(xs), 3):
                x, y = int(xs[i]), int(ys[i])
                hits.append({'score': float(res[y, x]),
                             'cx': (x + t.shape[1] // 2) * 2,
                             'cy': (y + t.shape[0] // 2) * 2,
                             'w': t.shape[1] * 2,
                             'src': tname})
    if not hits:
        return None
    hits = [h for h in hits if h['score'] >= min_score]
    if not hits:
        return None
    last = _TPL_LAST['cx']
    if last is not None:
        near_hits = [h for h in hits if abs(h['cx'] - last) <= near]
        if near_hits:
            _TPL_LAST['miss'] = 0
            best = max(near_hits, key=lambda h: h['score'])
        else:
            _TPL_LAST['miss'] += 1
            if _TPL_LAST['miss'] < 3:
                return None
            best = max(hits, key=lambda h: h['score'])
    else:
        best = max(hits, key=lambda h: h['score'])
    _TPL_LAST['cx'] = best['cx']
    best['h'] = int(best['w'] * 1.4)
    return best



def find_door_pink(min_area_small=600):
    """用**颜色 + 黄框**找随意门。返回 dict 或 None。

    ★★ 2026-11-08 晚实测定案（原地识别，不移动）：
      这一区的品红候选里，**只有门有黄框**，红横幅没有：

      | 候选 | 偏  | 宽高比 | **黄框占比** | 白点 | 真身 |
      |------|-----|--------|--------------|------|------|
      | ①    | +350| 0.36   | **0.000**    | 0.072| 右墙红横幅 ✗ |
      | ③    | +1404| 2.15  | 0.000        | 0.014| 横幅 ✗ |
      | ②    | −20 | 1.14   | **0.085**    | 0.117| **真的是门** ✓ |

      ⇒ 判据：品红连通块 + **黄框占比 ≥ 0.02**（门 8.5% vs 横幅 0%）；
        再用白点(眼睛)、宽高比、离中心距离加权排序。
      模板匹配在本区会指错（door_casting 0.41 @偏-452、door9 0.32 @偏+556），
      所以**颜色+黄框优先于模板**。
    """
    import cv2
    import numpy as np
    img = screenshot()
    small = cv2.resize(img, (960, 540), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (140, 60, 70), (178, 255, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, _lab, st, _cen = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, n):
        x, y, w, hh, area = st[i]
        if area < min_area_small or hh < 40:
            continue
        # ★ 2026-11-08 晚：**排除超大块**。首领区左下角那滩粉红血迹
        #   面积 157763 / 宽 2064px，曾被当成门追了 60 秒 ✗
        if w * 4 > 900 or area > 40000:
            continue
        ar = w / float(max(1, hh))
        if not (0.35 <= ar <= 2.2):
            continue
        roi = small[y:y + hh, x:x + w]
        rh = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        yf = float(cv2.inRange(rh, (15, 90, 150), (40, 255, 255)).mean()) / 255.0
        wf = float(cv2.inRange(rh, (0, 0, 185), (180, 75, 255)).mean()) / 255.0
        # 门的两条判据（实测二选一即可）：
        #   ① 黄框 ≥0.02（财富/铸造区的门 8.5%）
        #   ② 白色 ≥0.10（首领区那扇门白色 24%，血迹只有 3.3%、装饰 2.2%）
        if not (yf >= 0.02 or wf >= 0.10):
            continue
        off = (x + w / 2.0) * 4 - 1920
        score = yf * 20.0 + wf * 8.0 + min(area, 3000) / 3000.0 - abs(off) / 1200.0
        if best is None or score > best['score']:
            best = {'score': round(score, 2), 'cx': int((x + w / 2.0) * 4),
                    'cy': int((y + hh / 2.0) * 4), 'w': int(w * 4), 'h': int(hh * 4),
                    'yellow': round(yf, 3), 'white': round(wf, 3), 'src': 'pink+yellow'}
    return best



def pick_door(min_score=0.60, tol_near=420):
    """挑一扇门：**先用 YOLO**（专训类目），模板匹配兜底。

    ★ 2026-11-08：为什么改成 YOLO 优先 ——
      本地模型就是训这个的（`0060-mnyz-xcrk 模拟宇宙下层入口` / `0066-...-xcrkwjh 未激活`），
      而模板 `door5.png` 是从**室外街道**机位裁的，室内场景（体育馆/走廊）认不出 ✗。
      实测这一区 `approach_door` 转了 15 秒、模板一次都没命中，而门其实是存在的。
    """
    # ★★ 时间一致性（2026-11-08 加）：YOLO 和颜色两条都可能在多帧之间**跳目标**
    #   —— 实测日志「门 0.86 偏+1385 → 门 0.61 偏+220 → 门 0.45 偏+1544 → …」，
    #   于是 approach_door 一直在原地打转、永远走不到门口 ✗。
    #   规则：新检测离上一帧的已确认位置 > 520px 就**先不接受**（当作这帧没看到，
    #   让调用方扫视），连续 4 帧都发生在别处才允许换目标。
    _near = 520

    def _accept(d):
        last = _DOOR_LAST.get('cx')
        if last is not None and abs(d['cx'] - last) > _near:
            _DOOR_LAST['miss'] += 1
            if _DOOR_LAST['miss'] < 4:
                return None
        _DOOR_LAST['cx'] = d['cx']
        _DOOR_LAST['miss'] = 0
        return d

    # ★★ 2026-11-08 晚定案：**颜色+黄框优先于模板**。
    # 实测铸造区：door9 0.32@偏+556、door_casting 0.41@偏-452（都指错），
    # 而"品红+黄框≥0.02"稳定命中真门（偏-20，连续 3 帧不变）✓
    _pk = find_door_pink()
    if _pk is not None:
        _d = _accept(_pk)
        if _d is not None:
            return _d

    _td = find_door_by_tpl(min_score=0.42)
    if _td is not None:
        _d = _accept(_td)
        if _d is not None:
            return _d

    _yd = yolo_pick(CATE_DOOR, min_conf=0.35)
    if _yd is not None:
        _d = _accept({'score': _yd['conf'], 'cx': _yd['cx'], 'cy': _yd['cy'],
                      'w': _yd['w'], 'h': _yd['h'], 'src': 'yolo'})
        if _d is not None:
            return _d
    _yo = yolo_pick(CATE_DOOR_OFF, min_conf=0.35)
    if _yo is not None:
        # 门在"睡觉"：位置可用，但过去也进不去 —— 交给调用方判断（这里当作"看到门"）
        return {'score': _yo['conf'], 'cx': _yo['cx'], 'cy': _yo['cy'],
                'w': _yo['w'], 'h': _yo['h'], 'src': 'yolo_off'}
    cands = detect_door_cands(min_score=min_score)
    if not cands:
        _DOOR_LAST['miss'] += 1
        if _DOOR_LAST['miss'] >= 3:
            _DOOR_LAST['cx'] = None        # 连续丢三帧才允许换目标
        return None
    _DOOR_LAST['miss'] = 0
    last = _DOOR_LAST['cx']
    if last is not None:
        near = [c for c in cands if abs(c['cx'] - last) <= tol_near]
        if near:
            best = max(near, key=lambda c: c['score'])
            _DOOR_LAST['cx'] = best['cx']
            return best
        # 附近没有 → 可能是大幅转身，接受最高分但拉大容差
        best = max(cands, key=lambda c: c['score'] * (1.0 if abs(c['cx'] - last) > 900 else 0.5))
        _DOOR_LAST['cx'] = best['cx']
        return best
    best = cands[0]
    _DOOR_LAST['cx'] = best['cx']
    return best


def walk_to_target_holding(find_fn, name='目标', max_seconds=20.0, close_w=900, center_tol=70):
    """**按住 W 边走边追**（连续反馈），而不是走走停停。返回 True = 过门/出现站点卡。

    ★ 2026-11-08 实测对比（同一区、同一扇门）：
      · stop-and-go（走 0.7 秒→等→重新推理→再走）：**15 秒只走了 2 步**，
        日志「门 0.17 宽6% 偏+18 → 门 0.15 宽7% 偏+30 → 对门 15 秒没成功」✗
        —— 每轮都要跑一次 ONNX 推理 + 等走路动画，时间全耗在等待上。
      · 按住 W + 每 0.25 秒修一次方向：20 秒能修正 40+ 次，
        这才是当初**过 12/13 那扇门时唯一真正生效**的方式 ✓。
    所以过门/追怪一律优先用这个函数。
    """
    import pyautogui
    pyautogui.FAILSAFE = False
    t0 = time.time()
    last_off = 0
    first_w = 0
    i = 0
    missed = 0
    holding = True
    detours = 0
    pyautogui.keyDown('w')
    try:
        while time.time() - t0 < max_seconds:
            if on_station_picker() or area_passed():
                log('      · [%.1fs] %s：出现站点卡/过门 ✓' % (time.time() - t0, name))
                return True
            if in_battle():
                return True
            i += 1
            d = find_fn()
            if d is None:
                # ★ 2026-11-08：目标在 10 帧里只有 ~3 帧可见（颜色检测本来就是这样）。
                #   按住 W 时"看不见就继续走"会一路走偏 ✗ ⇒ 改成：
                #   看不见就**松开 W 停下来原地转**找目标，找到再继续走 ✓
                missed += 1
                if missed == 2:
                    pyautogui.keyUp('w')
                if missed >= 2 and missed % 2 == 0:
                    _send_input_move(500, 0)     # 原地转约 25°
                    time.sleep(0.25)
                time.sleep(0.15)
                continue
            missed = 0
            if not holding:
                pyautogui.keyDown('w')
                holding = True
            if first_w == 0:
                first_w = d['w']
            elif d['w'] <= first_w * 1.08 and i > 12:
                # ★ 2026-11-08：**看见门却走不近** = 被围栏/花坛挡住（这一区是围栏广场，
                #   实测门宽一直停在 8% 不动）。做法：**横向绕行**——
                #   转身 ~90° 前进 1.2 秒（沿障碍走），再转回来继续朝门走。
                detours += 1
                if detours > 4:
                    log('      · [%.1fs] %s 绕了 %d 次还是走不近 ⇒ 放弃'
                        % (time.time() - t0, name, detours))
                    return False
                log('      · [%.1fs] %s 走不近（宽 %d）⇒ 第 %d 次横向绕行'
                    % (time.time() - t0, name, d['w'], detours))
                pyautogui.keyUp('w')
                side = 2400 if detours % 2 else -2400        # 约 ±70°
                _send_input_move(side, 0)
                time.sleep(0.3)
                pyautogui.keyDown('w')
                time.sleep(1.2)
                pyautogui.keyUp('w')
                _send_input_move(-side, 0)                    # 转回原方向
                time.sleep(0.3)
                pyautogui.keyDown('w')
                first_w = 0
                i = 0
                continue
            off = d['cx'] - 1920
            last_off = off
            if abs(off) > center_tol:
                mx = max(-900.0, min(900.0, off / 1.2 * 0.8))
                _send_input_move(int(mx), 0)
                # ★ 2026-11-08：**我们自己转的头，不算"换目标"**。
                #   时间一致性门槛（|新cx-旧cx|>520 就拒绝）原本会把"转向后合法的新位置"
                #   当成另一个物体丢掉 —— 实测日志：门 0.22 宽22% 偏+658 → 转向+438 → 没看到门 ✗。
                #   所以主动转向后把基准清空，下一帧无条件接受。
                _DOOR_LAST['cx'] = None
                _DOOR_LAST['miss'] = 0
            # ★ 宽度不增长 = 走过去也没用（被展台/围栏挡住）⇒ 提前放弃，别空转 70 秒
            if i % 10 == 0:
                log('      · [%.1fs] %s 宽 %d（起点 %d）' % (time.time() - t0, name, d['w'], first_w))
            if d['w'] >= close_w:
                log('      · [%.1fs] %s 已经很近（宽 %d）→ 停步按 F' % (time.time() - t0, name, d['w']))
                break
            time.sleep(0.25)
    finally:
        pyautogui.keyUp('w')
    time.sleep(0.3)
    for _ in range(2):
        press('f', 1.6)
        if on_station_picker() or area_passed() or is_event_ui() or in_battle():
            return True
    log('      · [%.1fs] %s 没开（最后偏%+d）' % (time.time() - t0, name, last_off))
    return False


def click_go_to_target(find_fn, name='目标', max_seconds=25.0, close_w=800, tol=260):
    """**点击移动**寻路：点目标 → 游戏自己走过去 → 再点 → 直到贴近，然后按 F。

    ★★ 2026-11-08 重大发现：星铁支持**点击地面/物体自动走过去**（实测点一下门，
      画面连续 3 秒自己在动、我一个键都没按）。这比自己按 W + 转向**可靠得多**：
        · 方向由游戏算，不用猜转向增益 ✗
        · 绕障由游戏处理（围栏/花坛挡路的问题从根上消失）✓
    所以过门/追怪一律优先用它，hold-W 只作兜底。
    """
    t0 = time.time()
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            log('      · [%.1fs] %s：出现站点卡/过门 ✓' % (time.time() - t0, name))
            return True
        if in_battle():
            return True
        if is_event_ui():
            do_event_dialog()
            return True
        d = find_fn()
        if d is None:
            _send_input_move(500, 0)          # 看不见就转一点找
            time.sleep(0.3)
            continue
        if d['w'] >= close_w:
            log('      · [%.1fs] %s 已经很近（宽 %d）→ 按 F' % (time.time() - t0, name, d['w']))
            break
        # ★ 关键细节（2026-11-08 实测）：**必须点目标下方的"地面"**，不能点目标本体！
        #   点门/墙 → 游戏当作点了不可走的地方，角色原地不动 ✗
        #   点门底下的地面 → 角色真的走过去 ✓（实测画面差异 12~13 vs 静止基线 3.35）
        gy = min(2100, d['cy'] + d['h'] // 2 + 120)
        gx = max(200, min(3600, d['cx']))
        log('      · [%.1fs] %s 宽%d 偏%+d → 点它下方的地面 (%d,%d) 走过去'
            % (time.time() - t0, name, d['w'], d['cx'] - 1920, gx, gy))
        click_phys(gx, gy, settle=0.25)
        time.sleep(2.4)
    for _ in range(3):
        press('f', 1.4)
        if on_station_picker() or area_passed() or is_event_ui() or in_battle():
            return True
    return False


def coverage_clear(max_seconds=90.0, rounds=2):
    """**覆盖式推进**：不认门，用「点各处地面走过去 + 每到一个点狂按 F」把区域趟一遍。

    为什么需要它（2026-11-08）：
      DU 的门用颜色/模板/YOLO 都**召回率太低**（实测 3/10 帧，YOLO 在该场景几乎全空），
      而"过门"其实只需要**站到门附近按 F**。区域都不大 ⇒ 扇形走 10 个地面点、
      每点按 2 次 F，基本总能踩到门口 ✓ —— 完全没有检测依赖，不会因为认错而空转 ✗
    前提：点击移动可用（已实测：点地面 → 画面差异 12~13，静止基线 3.35）。

    ⚠️ 性能要点：**每点只跑一次全屏 OCR**。
       PP-OCRv5 在 3840×2160 上约 1~2 秒，而 on_station_picker/is_event_ui/in_battle
       各自都会 OCR ⇒ 一按 F 就 3 次 OCR，实测每点 20 秒 ✗。改成取一次 items 复用后
       每点降到 ~4 秒 ✓。
    """
    POINTS = [(1920, 1560), (2500, 1500), (1340, 1500), (2900, 1350),
              (900, 1350), (2250, 1250), (1550, 1250), (1920, 1200),
              (2700, 1150), (1100, 1150)]
    t0 = time.time()
    hit = 0

    def _check():
        """一次 OCR，返回 (done, event) —— done=该收工了。"""
        items = ocr_items()
        if on_station_picker(items) or area_passed(items):
            return True, False
        return False, is_event_ui(items)

    for rnd in range(rounds):
        for idx, (x, y) in enumerate(POINTS):
            if time.time() - t0 > max_seconds:
                log('      · 覆盖推进 %.0f 秒到点，收工' % max_seconds)
                return on_station_picker() or area_passed()
            done, ev = _check()
            if done:
                return True
            if ev:
                do_event_dialog()
                continue
            log('      · 覆盖第 %d 轮第 %d 点 → 点地面 (%d,%d) 走过去' % (rnd + 1, idx + 1, x, y))
            click_phys(x, y, settle=0.2)
            time.sleep(1.3)
            for _ in range(2):
                press('f', 0.55)
                hit += 1
                done, ev = _check()
                if done:
                    log('      · ★ 第 %d 次按 F 打开了门/站点' % hit)
                    return True
                if ev:
                    do_event_dialog()
                    break
                if in_battle():
                    log('      · 覆盖推进中进战斗 → 等打完')
                    wait_battle(timeout=420)
                    txt = '|'.join(_texts(ocr_items()))
                    if '选择祝福' in txt:
                        pick_bless_inline()
                    break
            # ★ 2026-11-08：**必须转够角度**！只转 200（≈10°）时，10 个点加起来才 100° 视野，
            #   等于只在原地附近打转、根本没扫一圈 ✗（实测 10 点走完门都没开）。
            #   转 1200（约 60°）⇒ 10 个点覆盖 600°，绕满一圈还多 ✓
            _send_input_move(1200, 0)
    return on_station_picker() or area_passed()


def approach_door(max_seconds=60, center_tol=120):
    """走向门并按 F 过门。**严格按铁律**：先对正到画面正中心，再直走。

    ★ 2026-11-08 实测有效流程（第 2 区 (2/13)→(3/13) 就是这么过的）：
      1. `leave_and_rejoin` 回区域入口（门在正前方）；
      2. `door9.png` 模板找门（分数 0.65~0.70）；
      3. `turn_to_center_of` 阻尼对正（-258 → -148 → -86 → -50 收敛 ✓）；
      4. 直走，门宽 210→270→350→460，然后出现「选择下一站」✓；
      5. **绝不边走边转**（边走边转 = 插肩而过）。
    """
    t0 = time.time()
    last_w = None
    stall = 0
    _last_off = None
    _jumps = 0
    tol = max(100, int(center_tol * 0.8))   # 模板抖动约 ±60，容差必须更宽
    if not turn_to_center_of(lambda: pick_door(min_score=0.42), name='门',
                             tol=tol, max_iter=12, sweep=140):
        log('      · 门没对上 → 兜底①：全景闭环')
        if goto_by_panorama():
            return True
        # ★ 2026-11-09 作者指定：全景也找不到 ⇒ **随机走+重扫**，三次都不行才重置位置。
        log('      · 全景没找到目标 → 兜底②：随机走+重扫（最多 3 次）')
        if random_walk_rescan(tries=3):
            return True
        log('      · 兜底②未找到 → 兜底③：覆盖推进')
        return coverage_clear(max_seconds=min(70.0, max_seconds))

    for seg in range(12):
        if time.time() - t0 > max_seconds:
            break
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            wait_battle(timeout=420)
            return True
        # ★ 2026-11-08：步长从 0.9 缩到 0.55 —— 闭环要"走一小步就重新识别"，
        #   0.9 秒太长，一次迈过头就丢目标（实测多次）。
        pyautogui.keyDown('w')
        time.sleep(0.55)
        pyautogui.keyUp('w')
        time.sleep(0.2)
        for _ in range(2):
            press('f', 0.7)
            if on_station_picker() or area_passed():
                log('      · 按 F 出现选择下一站 ✓')
                return True
            if is_event_ui():
                do_event_dialog()
                break
            if in_battle():
                wait_battle(timeout=420)
                return True
        d = pick_door(min_score=0.35)
        if d is not None:
            off = d['cx'] - 1920
            log('      · 段%d 门偏 %+d 宽 %d' % (seg + 1, off, d['w']))
            # ★ 2026-11-08：**门宽不增长 = 被装置/墙挡住** ⇒ 侧移一步绕过。
            #   实测：门就在造物调试台后面，正面走会撞上装置/墙角进不去；
            #   侧移 0.6 秒再走就能绕过去（作者原话："先往造物调试台旁边走"）。
            if last_w is not None and d['w'] <= last_w * 1.05:
                stall += 1
                if stall >= 2:
                    side = 'a' if (stall % 4 < 2) else 'd'
                    log('      · 门宽没长（%d→%d）⇒ 侧移（%s 0.6s）绕障' % (last_w, d['w'], side))
                    pyautogui.keyDown(side)
                    time.sleep(0.6)
                    pyautogui.keyUp(side)
                    time.sleep(0.3)
                    stall = 0
            else:
                stall = 0
            last_w = d['w']
            # ★★ 2026-11-09：**门偏剧烈跳变 = 检测器在乱抓**（战斗区满地气球/装饰），
            #   实测 +822→+360→+1653→+1788→… 来回跳，白烧 15~30 秒/轮 ✗
            #   连续 3 次跳变 >900px 就判定"这不是稳定门"，尽快把控制权交回漫游/全景。
            if _last_off is not None and abs(off - _last_off) > 900:
                _jumps += 1
                if _jumps >= 3:
                    log('      · 门偏连续跳变 %d 次（%+d→%+d）⇒ 判定检测不稳，放弃对齐' % (_jumps, _last_off, off))
                    return False
            else:
                _jumps = 0
            _last_off = off
            if abs(off) > tol:
                mx = max(-300.0, min(300.0, off / TURN_K * TURN_DAMP))
                _send_input_move(int(mx), 0)
                time.sleep(0.3)
        else:
            log('      · 段%d 门不可见（可能已贴近）' % (seg + 1))
    return on_station_picker() or area_passed()


def random_walk_rescan(tries=3):
    """全景扫描找不到目标时的自救（**作者 2026-11-09 指定**）：

        随机方向走随机距离 → 再扫一遍 → 三次都扫不到 ⇒ 重置位置（暂离重进）。

    理由（作者原话）：这样比在原地反复扫/反复转更省时间。
    返回 True = 已找到目标或已重置位置（可以继续下一趟）。
    """
    import random
    for i in range(tries):
        mx = random.randint(-3200, 3200)
        _send_input_move(mx, 0)
        time.sleep(0.45)
        secs = random.uniform(0.7, 2.0)
        pyautogui.keyDown('w')
        time.sleep(secs)
        pyautogui.keyUp('w')
        time.sleep(0.5)
        log('      · 随机走（转%+d，走%.1fs）第 %d/%d 次 → 重新扫描' % (mx, secs, i + 1, tries))
        # 重新扫描：门（颜色/模板/YOLO）或敌人标识都算"找到目标"
        if find_door_pink() is not None:
            log('        ✓ 扫到粉门'); return True
        if find_door_by_tpl(min_score=0.42) is not None:
            log('        ✓ 扫到门模板'); return True
        if enemy_indicator() is not None:
            log('        ✓ 扫到敌人标识 → 开打'); engage_battle()
            return True
        if yolo_pick(CATE_ENEMY, min_conf=0.45, min_w=60) is not None:
            log('        ✓ YOLO 扫到怪'); return True
        # 再来一次全景闭环（它内部会横扫 360°）
        try:
            if goto_by_panorama():
                log('        ✓ 全景闭环成功')
                return True
        except Exception as e:
            log('        ✗ 全景异常: %s' % e)
    log('      · 三次都扫不到目标 → 重置位置（暂离重进）')
    h = find_game()
    if h:
        try:
            leave_and_rejoin(h)
        except Exception as e:
            log('        ✗ 重置异常: %s' % e)
    return True


def advance_and_clear(max_seconds=60):
    """小步前进 + 边走边按 F（门口不弹提示，必须盲按）。返回 True = 已弹「选择下一站」。

    ★ 预算按**秒**（2026-11-07 晚作者要求：找不到就别拖）。
    """
    import numpy as np
    import cv2

    def band_of(img):
        return cv2.resize(img[900:1700, :, :], (640, 160), interpolation=cv2.INTER_AREA)

    # ★ 先试「对准门再前进」：门偏在一侧时，边走边按 F 会擦肩而过
    # ★ 2026-11-08：找门预算从 30s 降到 15s —— 战斗区**门要先清完怪才出现**，
    #   花 30 秒找一扇还不存在的门纯属浪费（实测每趟白等 30s）。
    if approach_door(max_seconds=max(10, max_seconds // 4)):
        return True
    # ⚠️ 不再无条件放秘技（作者 2026-11-07 指出：没怪时不该放）；改由 go_to_target 认到怪再放
    t0 = time.time()
    prev = band_of(screenshot())
    blocked = 0
    step = 0
    _last_prompt_sig = None
    _prompt_repeat = 0
    while time.time() - t0 < max_seconds:
        step += 1
        if on_station_picker():
            return True
        hits = find_prompts()
        if hits:
            word, x, y = hits[0]
            # ★ 2026-11-08 晚：**同一提示反复按 F 没反应 = 被锁住/条件不满足**。
            #   实测首领区：门就在眼前，但「调查」提示按 F 完全无效，
            #   原因是 HUD 上的区域机制「概念献祭典仪 2/6」没做完 ⇒ 门是锁的。
            #   旧代码会在这里无限循环、一轮白烧 60 秒 ✗
            sig = '%s|%d|%d' % (word, x, y)
            if sig == _last_prompt_sig:
                _prompt_repeat += 1
            else:
                _last_prompt_sig, _prompt_repeat = sig, 1
            if _prompt_repeat >= 3:
                log('      · 「%s」提示连续 %d 次按 F 无效 ⇒ 判定被锁'
                    '（如区域机制未完成）→ 停止硬按，交给其它策略' % (word, _prompt_repeat))
                return False
            log('      · [%.0fs] 看到「%s」提示 @ (%d,%d) → F（第%d次）'
                % (time.time() - t0, word, x, y, _prompt_repeat))
            if interact_here():
                return True
            prev = band_of(screenshot())
            continue
        # ★ 先问本地 YOLO（隔一步跑一次，省时间）：看见事件框/门/怪/奖励就直接过去
        if step % 2 == 1 and smart_clear_step():
            return True
        # 兜底：宝箱模板（每 4 步一次，很贵）
        if step % 4 == 0 and detect_tpl('chest.png', min_score=0.62) is not None:
            log('      · [%.0fs] 模板看到宝箱 → 过去开' % (time.time() - t0))
            if approach_object('chest.png', '宝箱'):
                return True
            continue
        walk(1.0)
        press('f', 1.3)
        if area_passed():
            log('      · [%.0fs] 已经过门（弹出面具/站点界面）' % (time.time() - t0))
            return True
        # ★ 走不动就转视角（否则会顶着墙白走到超时）
        cur = band_of(screenshot())
        moved = float(np.mean(cv2.absdiff(prev, cur)))
        prev = cur
        if moved < 4.0:
            blocked += 1
            log('      · [%.0fs] 走不动（第 %d 次）' % (time.time() - t0, blocked))
            if blocked < 2:
                # ★ 先别急着转：每次重进后第一步常在场地边缘，误判"卡住"就转身会走歪
                walk(0.8)
                prev = band_of(screenshot())
                continue
            blocked = 0
            log('      · 连撞两次 → 转约 32 度换个方向')
            turn_mouse(900)
            continue
        blocked = 0
        # ★★ 2026-11-08：直走这趟也要**螺旋扫视**（每步转约 42°）。
        #   实测这一区的怪/门都在**侧面**，而旧代码只在撞墙时才转 ⇒ 整趟扫不到，
        #   日志表现为"YOLO 看到普通怪×1"却从不去打、最后走到超时 ✗。
        #   螺旋后 8~9 步正好一圈，怪和门都会进视野 ✓。
        turn_mouse(800)
        if on_station_picker():
            return True
        if is_event_ui():
            log('      · 盲按 F 打开了事件界面')
            do_event_dialog()
            continue
        if in_battle():
            log('      · 进战斗了 → 等自动打完')
            wait_battle()
            if '选择祝福' in '|'.join(_texts(ocr_items())):
                pick_bless_inline()
            continue
    log('      · 直走 %.0f 秒没结果' % max_seconds)
    return on_station_picker()


def roam_search(max_seconds=90):
    """贴墙漫游找事件/门（时间预算，默认 90 秒）。返回 True = 已弹「选择下一站」。"""
    import numpy as np
    import cv2

    def band_of(img):
        return cv2.resize(img[900:1700, :, :], (640, 160), interpolation=cv2.INTER_AREA)

    t0 = time.time()
    prev = band_of(screenshot())
    blocked = 0
    done = 0
    step = 0
    while time.time() - t0 < max_seconds:
        step += 1
        hits = find_prompts()
        if hits:
            word, x, y = hits[0]
            log('      · [%.0fs] 漫游看到「%s」@ (%d,%d) → F'
                % (time.time() - t0, word, x, y))
            if interact_here():
                return True
            done += 1
            prev = band_of(screenshot())
            continue

        if smart_clear_step():
            return True
        if detect_tpl('chest.png', min_score=0.62) is not None:
            log('      · [%.0fs] 漫游看到宝箱 → 过去开' % (time.time() - t0))
            if approach_object('chest.png', '宝箱'):
                return True
            prev = band_of(screenshot())
            continue
        walk(0.5)
        press('f', 1.0)                    # 门口不弹提示，边走边盲按
        if on_station_picker() or area_passed():
            return True
        if is_event_ui():
            do_event_dialog()
            prev = band_of(screenshot())
            continue
        if in_battle():
            log('      · 进战斗了 → 等自动打完')
            wait_battle()
            if '选择祝福' in '|'.join(_texts(ocr_items())):
                pick_bless_inline()
            prev = band_of(screenshot())
            continue

        cur = band_of(screenshot())
        moved = float(np.mean(cv2.absdiff(prev, cur)))
        prev = cur
        # ★★ 2026-11-08：改成**螺旋扫视**。
        #   实测（sr_sweep_indoor.png）：这一区的怪只在**某个朝向**才进画面
        #   （手动转一圈：朝2 看到 普通怪 0.78/0.74/0.62，朝4 看到 0.42），
        #   而旧代码只在「每 8 步」或「撞墙」时才转向 ⇒ 常常整趟都扫不到 ✗。
        #   现在每步固定转约 48°（turn_mouse 900），8 步正好一圈，保证怪/门进视野 ✓。
        if moved >= 4.0:
            turn_mouse(900)
        if moved < 4.0:                    # 画面几乎没变 = 被挡住
            blocked += 1
            # ★ 2026-11-08 加强：原来只转 1700（≈50°）继续走 ✗ ——
            #   实测角色顶在墙角（花盆+墙）时，转 50° 还是在墙上，一直原地打转。
            #   改成：大角度转身 + 走一段；**连续两次走不动就先 S 后退**再转。
            log('      · [%.0fs] 走不动（第 %d 次）→ %s'
                % (time.time() - t0, blocked, '后退+转身' if blocked % 2 == 0 else '大角度转身'))
            if blocked % 2 == 0:
                press('s', 0.9)            # 先后退脱开墙角
                walk(0.4)
            turn_mouse(2600 if blocked % 2 else -2600)   # ≈ ±75°
            walk(1.2)
            prev = band_of(screenshot())
            continue
            pass
    log('      · 漫游 %.0f 秒结束（途中完成 %d 个互动）' % (max_seconds, done))
    return on_station_picker()


def is_final_area(items=None):
    """是不是最终区（左上角出现 13/13）。"""
    items = items if items is not None else ocr_items()
    txt = ''.join(_texts(items))
    return ('13/13' in txt)


def settle_exit():
    """最终区结算出口：ESC →「结束并结算」→「确认」。返回 True = 已点完。

    13/13 打完后场上没有门，只有一个白色菱形；菱形无文字可抓，
    而暂停面板的「结束并结算」是固定文字，所以走暂停面板更稳 ✓
    """
    if ocr_find('暂离', tries=1, interval=0.6) is None:
        log('      · 打开暂停面板（ESC）')
        press('esc', 2.2)
    if ocr_find('结束并结算', tries=3, interval=1.0) is None:
        log('      · 面板里没有「结束并结算」→ 再按一次 ESC')
        press('esc', 2.0)
    if not click_text('结束并结算', tries=4, interval=1.2):
        log('      ⚠ 找不到「结束并结算」')
        return False
    time.sleep(2.5)
    if not click_text('确认', tries=5, interval=1.2):
        log('      ⚠ 结算确认没点成')
        return False
    time.sleep(6.0)
    log('      ✓ 已提交结算')
    return True


def clear_area(h=None, tries=3, walk_seconds=60, roam_seconds=90):
    """清场 + 过门。返回 True = 已出现「选择下一站」。

    最坏耗时 ≈ walk_seconds 两次 + roam_seconds 一次 ≈ 3 分钟，之后重置位置。
    """
    t_all = time.time()
    for t in range(1, tries + 1):
        log('      · 清场第 %d/%d 趟（直走 %ds）' % (t, tries, walk_seconds))
        # ⚠️ 2026-11-08 晚：顺序改回「门优先」。
        #   door9.png 真门模板命中后（0.65~0.70）能稳定对正并过门，
        #   而覆盖推进每轮白烧 80 秒且没实测成功过 ⇒ 只当兜底。
        if advance_and_clear(max_seconds=walk_seconds):
            return True
        if coverage_clear(max_seconds=min(80.0, walk_seconds + 20)):
            log('      · 覆盖推进成功 → 选站点卡')
            pick_station()
            return True
        if t == tries - 1:
            log('      · 加一趟纯漫游（%ds）' % roam_seconds)
            if roam_search(max_seconds=roam_seconds):
                return True
        if h is None or t >= tries:
            break
        log('      · 已找 %.0f 秒 → 暂离重进，重置位置' % (time.time() - t_all))
        if not leave_and_rejoin(h):
            log('      ⚠ 暂离/重进失败')
            break
    log('      · 清场总共 %.0f 秒仍没开出门' % (time.time() - t_all))
    if is_final_area():
        log('      · 这是最终区（13/13）→ 用「结束并结算」收尾')
        settle_exit()
    return on_station_picker()


def find_game():
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and GAME_TITLE in win32gui.GetWindowText(h):
            found.append(h)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def ensure_foreground(h, tries=3):
    """抢前台。★ 必须用 AttachThreadInput ✓（2026-11-07 实测 ✓）

    只用 SetForegroundWindow 会被 Windows 的**前台锁**拒绝 ✗
    （前台是浏览器时连试 3 次全失败 ✗，而且当时的点击全打给了浏览器 ✗）。
    AttachThreadInput 挂到前台线程后就能合法抢 ✓，实测返回 1 ✓。

    ⚠️ 另外：**跑本脚本的 python.exe 必须是 High 完整性** ✓
    （Medium 时点击会被 UIPI 丢 ✗ —— 见笔记 §12.2）。
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32')
    kernel32 = ctypes.WinDLL('kernel32')
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    if win32gui.GetForegroundWindow() == h:
        return True
    for _ in range(tries):
        try:
            fg = user32.GetForegroundWindow()
            t_fg = user32.GetWindowThreadProcessId(fg, None)
            t_me = kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(t_me, t_fg, True)
            try:
                user32.ShowWindow(h, win32con.SW_SHOW)
                user32.BringWindowToTop(h)
                user32.SetForegroundWindow(h)
                user32.SetActiveWindow(h)
                user32.SetFocus(h)
            finally:
                user32.AttachThreadInput(t_me, t_fg, False)
            time.sleep(0.7)
            if win32gui.GetForegroundWindow() == h:
                return True
        except Exception as e:
            log('      抢前台异常: %s' % e)
            time.sleep(0.5)
    return win32gui.GetForegroundWindow() == h


def click_phys(x, y, hover=0.5, settle=0.5):
    """平滑移动到物理坐标再点 ✓（一次性 SetCursorPos 会被吞 ✗）。"""
    sw, sh = pyautogui.size()
    x = max(1, min(int(x), sw - 2))
    y = max(1, min(int(y), sh - 2))
    pyautogui.moveTo(max(1, x - 120), max(1, y - 100), duration=0.3)
    pyautogui.moveTo(x, y, duration=hover)
    time.sleep(settle)
    pyautogui.click()


def walk(seconds):
    pyautogui.keyDown('w')
    time.sleep(seconds)
    pyautogui.keyUp('w')
    time.sleep(0.35)


def press(key, wait=1.0):
    pyautogui.press(key)
    time.sleep(wait)


def leave_and_rejoin(h):
    """暫离 → 重进。重进后角色在区域入口、门在正前方 ✓（实测多次 ✓）。"""
    log('      · 暂离')
    # 打开暂停面板要试到为止：ESC 可能被事件弹窗吃掉，也可能把已开的面板关掉。
    # 规则：最多试 3 次，每次先看「暂离」在不在 —— 在就不按（按了会关掉）。
    opened = False
    for attempt in range(4):
        if ocr_find('暂离', tries=1, interval=0.6) is not None:
            log('      · 暂停面板已开（第 %d 次检查）' % (attempt + 1))
            opened = True
            break
        # ★ 2026-11-08：ESC 是**开关**。旧代码盲按 3 次，结果在"暂停面板 / 手机菜单"
        #   之间反复横跳，最后停在手机菜单上，整轮卡死 ✗。
        #   现在先认当前画面：手机菜单 → 关掉它（再按 ESC 才会打开暂停面板）。
        _txt = '|'.join(_texts(ocr_items()))
        if any(k in _txt for k in ('梦境护照', '均衡等级', '开拓等级')):
            log('      · 当前在手机菜单 → 先关掉（ESC）')
            press('esc', 1.8)
            continue
        log('      · 面板没开 → 按 ESC（第 %d 次）' % (attempt + 1))
        press('esc', 2.2)
    if not opened:
        log('      x 试了 3 次仍打不开暂停面板')
    if not click_text('暂离', tries=5, interval=1.0):
        log('      ✗ 没找到「暂离」')
        return False
    time.sleep(8.0)
    if not ensure_foreground(h):
        log('      ✗ 离开后抢不到前台')
        return False
    log('      · 重进')
    press('f', 6.0)
    if not click_text('开始游戏', tries=8, interval=1.5):
        return False
    time.sleep(6.0)
    if not click_text('继续进度', tries=6, interval=1.5):
        log('      ✗ 没找到「继续进度」')
        return False
    time.sleep(14.0)
    return True


def in_div_uni_area(items=None):
    """是否真的在差分宇宙的区域里。

    ★ 2026-11-08 再修：以前用**整屏 OCR** 的文字判断，但 HUD 这行常被拆成
      「（2/13）第」+「位面-财富」甚至丢了"位面"二字 ⇒ 明明在副本里却判 False ✗
      （实测日志：画面明确是「（2/13）第…」，函数却返回 False）。
      现在和 area_kind() 一样：**裁左上角 HUD 条 + 放大 2 倍**再 OCR。
    判据：HUD 里同时有 `n/m` 计数 或 出现任一区域类型字样（战斗/财富/事件/休整…），
    再加上开局专用界面（面具/奇迹/站点卡/愿力满盈）。
    """
    import re as _re
    import numpy as np
    import cv2
    if items is None:
        img = screenshot()
        crop = img[15:145, 110:1150]
        crop = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2),
                          interpolation=cv2.INTER_CUBIC)
        items2 = ocr_items(crop)
        full_txt = '|'.join(_texts(ocr_items()))
    else:
        items2 = items
        full_txt = '|'.join(_texts(items))
    hud = ''.join(t.strip() for t, s, _, _ in items2 if s >= 0.4)
    # ★★ 2026-11-09 实测修正：**菜单/结算页也会让旧判据返回 True**。
    #   现象：停在「模式选择 / 周期演算详情」页时 in_div_uni_area()=True
    #   → auto_entry 认为"已经在局内"→ 跳过进场 → 脚本对着菜单找门、左转右转 ✗
    #   判据：整屏出现菜单特征词、且**没有**「位面-」HUD ⇒ 一定不在区域里。
    _MENU_MARKS = ('模式选择', '开始游戏', '分析报告', '已过期', '本期剩余时间',
                   '积分奖励', '规则说明', '拟合等级', '乐园漫记', '查看详情', '结束并结算')
    if any(k in full_txt for k in _MENU_MARKS) and '位面-' not in full_txt             and '位面' not in hud:
        return False
    if _re.search(r'\d+\s*/\s*\d+', hud):
        return True
    for k in AREA_KINDS:
        if k in hud:
            return True
    for kw in ('选择一张面具', '选择惊世奇迹', '选择你的奇迹', '选择下一站', '选择一张站点卡',
               '选择站点卡', '选择方程', '差分宇宙·周期演算', '愿力满盈'):
        if kw in full_txt:
            return True
    return False


def enter_divergent(h=None):
    """进入差分宇宙·周期演算并开始/继续一局（**状态机 + OCR，不写死坐标**）。

    ★ 2026-11-09 全流程实测（作者要求"让脚本来操作"），确认的路径：
        大世界 →（站在差分宇宙终端旁）按 **F** → 大厅（开始游戏）
        → 模式选择（周期演算）→ 详情页
        → 若「已过期」：结束并结算 → 确认 → 分析报告 → 返回主界面 → 再走一遍
        → 继续进度 / 开始挑战 → 选面具 → 进入 (1/13) 区域

    ★ 三个必须处理的弹窗（都是实测撞到的）：
        ① 「当前存档非本周演算进度…确定继续进度吗？」→ 点 **取消**（放弃过期存档）
        ② 「…确定结算奖励并重置进度吗？」→ 点 **确认**
        ③ 「分析报告」→ 点 **返回主界面**
    """
    if h is None:
        h = find_game()
    if not h or not ensure_foreground(h):
        log('      ✗ 抢不到前台')
        return False
    if on_station_picker() or in_div_uni_area():
        log('      · 已经在副本里，跳过进场')
        return True

    def click_txt(name, tries=2, wait=1.3):
        p = ocr_find(name, tries=tries, interval=0.9)
        if not p:
            return False
        log('        · 点「%s」@ (%d,%d)' % (name, p[0], p[1]))
        click_phys(p[0], p[1], settle=wait)
        time.sleep(0.9)
        return True

    f_tried = 0
    for step in range(50):
        time.sleep(1.0)
        if on_station_picker() or in_div_uni_area():
            log('      ✓ 已进入差分宇宙（area=%s）' % (area_kind() or '?'))
            return True
        img = screenshot()
        items = ocr_items(img)
        joined = '|'.join(t.strip() for t, s, _, _ in items if s >= 0.6)

        # ---------- 弹窗（优先级最高） ----------
        if '非本周演算进度' in joined:
            log('      · 存档非本周进度 → 取消（放弃过期存档）')
            click_txt('取消') or click_txt('确认')
            continue
        if '确定结算奖励并重置' in joined:
            log('      · 结算确认 → 确认')
            click_txt('确认') or click_txt('确定')
            continue
        if '分析报告' in joined:
            click_txt('返回主界面') or click_txt('返回')
            continue
        if '点击空白处关闭' in joined:
            click_phys(*P['dialog_next'], settle=0.8)
            continue
        if '选择一张面具' in joined or '选择面具' in joined:
            log('      · 选面具')
            pick_first_card()
            continue

        # ---------- 大厅 / 模式选择 / 详情页 ----------
        if '模式选择' in joined:
            click_txt('周期演算')
            continue
        if '开始游戏' in joined:
            click_txt('开始游戏')
            continue
        if '已过期' in joined and '结束并结算' in joined:
            log('      · 存档已过期 → 结束并结算（重置后开新局）')
            click_txt('结束并结算')
            continue
        if '继续进度' in joined:
            click_txt('继续进度')
            continue
        if '开始挑战' in joined or '开始演算' in joined:
            click_txt('开始挑战') or click_txt('开始演算')
            continue
        # ★ 2026-11-09 取证：**详情页的进入按钮叫「启动「差分宇宙」」**（底部右侧），
        #   而「周期演算：喜剧秀场」只是标题标签 —— 点它只会在"模式选择↔详情页"之间
        #   来回跳（实测无限 ping-pong）✗
        if '启动' in joined:
            click_txt('启动')
            continue
        if '周期演算' in joined and '小时' in joined:
            # 详情页但没看到「启动」→ 先回上一层（模式选择）
            log('      · 详情页没看到「启动」→ ESC 回上一级')
            press('esc', 1.2)
            continue

        # ---------- 大世界：终端按 F（最多 3 次），再走指南兜底 ----------
        if is_world():
            if f_tried < 3:
                f_tried += 1
                log('      · 大世界：按 F 试开差分宇宙终端（第%d次）' % f_tried)
                press('f', 1.6)
                time.sleep(1.2)
                continue
            if click_txt('指南', tries=2):
                continue
            press('esc', 1.2)
            continue

        # 其它未知界面：ESC 收拾一下
        press('esc', 1.0)

    log('      ✗ 进场超时（50 步）')
    return False


def do_area_action(kind_hint=''):
    """区域内的"尽量做一遍"动作。

    没有 OCR ✗，所以不判断区域类型 —— 改成**把所有可能的交互都试一遍**：
      秘技 → 走两步 → F → ▼×4 → 确定
    多试无害 ✓（商店/空白区没东西时这些操作不产生副作用 ✓）。
    """
    log('      · 秘技 + 前进')
    press('e', 0.8)
    walk(0.7)
    walk(0.6)

    log('      · F（交互）')
    press('f', 1.5)

    log('      · ▼ × 4（事件/对话）')
    for _ in range(4):
        click_phys(*P['dialog_next'], settle=0.25)

    log('      · 确定（若有）')
    click_phys(*P['btn_confirm'], settle=0.4)
    time.sleep(1.5)

    log('      · ESC（关掉可能打开的菜单）')
    press('esc', 1.5)


def walk_to_door_and_enter(h):
    """可靠过门：直走 5 小步 → F。"""
    log('      · 直走推门')
    # 实测：重进后门在正前方，但 5 小步(3.5s)不够 → 加到 10 小步(7s) ✓
    for i in range(10):
        walk(0.7)
    press('f', 5.0)


def pick_station():
    """「选择站点卡」：按**实际耗时**挑最省事的区域，再点「确定」。

    ★ 2026-11-08 重写。上一版只认标题『选择一张站点卡』✗，实测这一屏的标题是
      **『选择站点卡』**（少一个"一张"），于是整段选卡逻辑被跳过、只剩「确定」，
      然后一直提示"找不到确定"卡住 ✗。

    页面结构（实测 sr_station_look.png，物理坐标）：
      一张网格，格子里是区域类型文字：战斗/事件/异常/财富/商店/冒险/奖励/铸造/精英
      「区域说明」在右上，「确定」在 **(3254, 1872)**。
    策略（沿用作者 2026-11-07 定的耗时顺序：零操作的优先）：
      休整 > 空白 > 商店 > 财富 > 铸造 > 奖励 > 事件 > 冒险 > 战斗 > 精英 > 异常
    点卡时点**文字上方 80px**（文字在卡下半部，直接点文字容易点到说明区）。
    """
    log('      · 选站点卡')
    items = ocr_items()
    txts = [(t.strip(), int(cx), int(cy)) for t, s, cx, cy in items if s >= 0.55 and t.strip()]
    has_title = any('站点卡' in t for t, _, _ in txts)
    if not has_title:
        log('      · 屏幕上没有『站点卡』标题')
    PREF = ('休整', '空白', '商店', '财富', '铸造', '奖励', '事件', '冒险', '战斗', '精英', '异常', '首领')
    chosen = None
    for want in PREF:
        for t, cx, cy in txts:
            if t == want or t.startswith(want):
                chosen = (want, cx, cy)
                break
        if chosen is not None:
            break
    if chosen is not None:
        want, cx, cy = chosen
        log('      · 挑「%s」（按耗时优先）→ 点 (%d,%d)' % (want, cx, max(0, cy - 80)))
        click_phys(cx, max(0, cy - 80), settle=1.2)
    else:
        # ★ 2026-11-08：**首领局只有一张卡**，且卡片位置不固定，
        #   原来点死坐标 (880,700) 会落空（实测点完仍停在选卡页）。
        #   改成：先找「等级」标签（每张卡都有），点它上方 40px。
        lv = None
        for t_, s_, cx_, cy_ in items:
            if s_ >= 0.55 and t_.strip() == '等级':
                lv = (int(cx_), int(cy_)); break
        if lv is not None:
            log('      · 用「等级」标签定位卡片 → 点 (%d,%d)' % (lv[0], lv[1] - 40))
            click_phys(lv[0], max(60, lv[1] - 40), settle=1.2)
        else:
            log('      · 没认出区域类型也没找到「等级」→ 点屏幕中上半部')
            click_phys(1920, 1200, settle=1.2)
    time.sleep(1.5)
    if not click_text('确定', tries=4, interval=1.0) and \
            not click_text('确认', tries=2, interval=0.8):
        log('      · 没有「确定」文字 → 点实测位置 (3254,1872)')
        click_phys(3254, 1872, settle=1.2)
    time.sleep(8.0)


# ---- 局内状态集合：出现在这些界面说明**已经在副本里**（不该再走进场流程）----
IN_RUN_STATES = ('pick_mask', 'pick_miracle', 'pick_station', 'pause_panel',
                 'event_dialog', 'battle', 'in_area')

# 差分宇宙大厅（还没进局）会出现的文字
LOBBY_HINTS = ('周期演算', '前往参与', '开始游戏', '继续进度', '查看详情', '拟合等级')


def is_in_run(verbose=True):
    """是否**已经在差分宇宙局内**（任意节点）。
    判据（任一成立）：
      ① HUD 有 `n/m` 计数或区域类型字样（`in_div_uni_area()`）；
      ② 当前界面属于局内状态（面具/奇迹/站点卡/暂停面板/事件/战斗）。
    """
    st = detect_state(verbose=False)[0]
    # ★★ 2026-11-09 实测修正：**大厅/详情页必须否决**。
    #   现象：周期演算详情页有「通关可获得积分」→ 命中 event_dialog 的 '获得'
    #   → is_in_run 返回 True → auto_entry 跳过进场 → 脚本对着菜单找门 ✗
    #   代码里原本就有 LOBBY_HINTS 却没被用上，这里补上。
    try:
        joined = '|'.join(_texts(ocr_items()))
    except Exception:
        joined = ''
    if any(k in joined for k in LOBBY_HINTS) and '位面-' not in joined:
        if verbose:
            log('      · 大厅/详情页特征命中 → 不在局内')
        return False, st
    if st in IN_RUN_STATES and st != 'in_area':
        return True, st
    if in_div_uni_area():
        return True, st or 'in_area'
    return False, st


def enter_from_lobby():
    """已在差分宇宙大厅（未进局）：直接点「周期演算 → 继续进度 / 开始游戏」。"""
    for name in ('周期演算', '继续进度', '开始游戏', '确认'):
        got = ocr_find(name, tries=3, interval=1.2)
        if got is not None:
            log('      · 点「%s」@ %s' % (name, got))
            click_phys(got[0], got[1], settle=1.5)
            time.sleep(3.0)
        if in_div_uni_area() or on_station_picker():
            break
    return in_div_uni_area() or on_station_picker()


def auto_entry(h, force_entry=False):
    """**从任意节点接着跑**：已在局内就什么都不做；在大厅就只点进局；在大世界才走完整进场。

    作者 2026-11-08 要求："已经在差分宇宙里了，就不用回到主界面再重进了"。
    """
    if force_entry:
        log('① 强制进场（--force-entry）')
        return enter_divergent(h)

    inside, st = is_in_run()
    if inside:
        log('① 检测到**已在差分宇宙局内**（界面=%s）→ 直接从当前节点继续，不回主界面 ✓' % st)
        return True

    txt = '|'.join(_texts(ocr_items()))
    if any(k in txt for k in LOBBY_HINTS):
        log('① 检测到在**差分宇宙大厅**（未进局）→ 直接「周期演算 → 继续进度」')
        if enter_from_lobby():
            return True
        log('      · 大厅内进局失败 → 退回完整进场流程')
        return enter_divergent(h)

    log('① 在**大世界** → 走完整进场流程')
    return enter_divergent(h)


def run(max_areas=14, dry=False, skip_entry=False, force_entry=False):
    h = find_game()
    if not h:
        log('✗ 没找到游戏窗口（%s）' % GAME_TITLE)
        return 2
    log('✓ 找到游戏窗口')
    if dry:
        log('（--dry：只打印计划，不操作）')
        return 0
    if not ensure_foreground(h):
        log('✗ 抢不到前台')
        return 3

    if skip_entry:
        log('① 跳过进场（--skip-entry：假定已经在副本里）')
    else:
        # ★ 2026-11-08：**自动判断从哪开始**（作者要求"任意节点接着跑"）
        if not auto_entry(h):
            log('✗ 进入差分宇宙失败')
            return 4
    if not ensure_foreground(h):
        return 3

    # ★ 卡死检测状态（2026-11-08 加）
    #   起因：「选择一张面具」那一屏**完全不吃输入**（pyautogui/SendInput/PostMessage/
    #   SendMessage/键盘全无效，连 M 都打不开地图），旧代码会永远在同一屏空转，
    #   既不会失败也不会结束。现在同一界面连续 12 轮毫无变化就判卡死退出。
    _last_sig = None
    _same = 0

    for i in range(1, max_areas + 1):
        log('')
        log('  === 第 %d 轮 ===' % i)

        # ---- 先看当前在哪个界面，再决定干什么 ✓（状态机）----
        state, texts = detect_state()

        # ---- 卡死检测 ----
        _sig = (state, '|'.join(texts)[:200])
        if _sig == _last_sig:
            _same += 1
        else:
            _last_sig, _same = _sig, 1
        # ★ 2026-11-09 作者要求：**卡住时用全景闭环自救**，别直接判死退出。
        if _same == 8:
            log('  ⚠ 界面「%s」连续 8 轮没变化 → 先试全景闭环找路（作者指定的自救手段）' % state)
            try:
                if goto_by_panorama():
                    log('      ✓ 全景闭环返回成功')
                    _same = 0
                    _last_sig = None
                    continue
            except Exception as e:
                log('      ✗ 全景闭环异常: %s' % e)
        if _same >= 12:
            log('  ⚠ 界面「%s」连续 %d 轮没有任何变化 → 判定卡死，退出（返回 5）' % (state, _same))
            log('     已知成因：某些界面**完全不吃合成输入**（键盘/鼠标/SendInput/消息全无效），')
            log('     需要人工点一下才能继续；代码层面没有可靠的办法绕过 ✗')
            return 5

        if state == 'device_curio':
            log('      · 造物调试台（选择加权奇物）→ 点右上 X 关闭')
            click_phys(3744, 129, settle=1.2)
            time.sleep(1.0)
            continue
        if state == 'popup_blank':
            log('      · 这是"点击空白处关闭"的弹窗 → 点右上空白')
            click_phys(3700, 300, settle=1.2)
            time.sleep(2.0)
            continue
        if state == 'bless_choose':
            log('      · 这是「选择祝福」界面')
            # 三张卡横排在 y≈1016（名字），卡面在下面 y≈1300
            #   铁律：优先带**书标**的卡（图鉴没开过）✓；没有就选第 1 张
            badge = find_template(os.path.join(TPL_DIR, 'badge_book.png'), threshold=0.65)
            if badge is not None:
                col_x = badge[0]
                log('      · 书标在 (%d,%d) → 选那一列' % (col_x, badge[1]))
            else:
                col_x = 966
                log('      · 没书标 → 选第 1 张')
            click_phys(col_x, 1300, settle=1.3)     # 点卡面（不是名字，名字点不中 ✗）
            time.sleep(1.5)
            if not click_text('确', tries=5, interval=1.2):   # 「确认」/「确定」都匹配
                click_phys(3374, 1912, settle=1.2)
            time.sleep(8.0)
            continue
        if state == 'pick_equation':
            log('      · 这是「选择方程」界面')
            # 铁律 ✓：优先选**带书标**的卡（= 图鉴没开过）✓
            #   实测 sr_in_area.png：三张卡里第 3 张「天命之主」右上角有书标 ✓
            badge = find_template(os.path.join(TPL_DIR, 'badge_book.png'), threshold=0.65)
            if badge is not None:
                log('      · 书标在 (%d,%d) → 选它那一列' % (badge[0], badge[1]))
                col_x = badge[0]
            else:
                log('      · 没找到书标，选第 3 张')
                col_x = 2860
            click_phys(col_x, 1000, settle=1.2)      # 点卡面中上部
            time.sleep(1.5)
            if not click_text('确认', tries=5, interval=1.2):
                click_phys(3050, 1970, settle=1.2)   # 兜底：右下角确认
            time.sleep(10.0)
            continue
        if state == 'pick_mask':
            log('      · 这是「选择一张面具」界面')
            # ★★ 2026-11-08 实测唯一可行的一组坐标（**别再靠目测估**）：
            #    来自 OneDragon 自己的 AHA_MASK 处理器（逻辑坐标 1920x1080 下）
            #      选卡 (314, 914)   → 物理 (628, 1828)
            #      确认 (1576, 982)  → 物理 (3152, 1964)
            #   血泪：① 模板匹配 score=1.000 命中后点 (300,1600) **选不中** ✗
            #        ② 我从截图目测估的卡面位置差了 1000+ 像素 ✗（真卡面在画面最下面）
            #   所以这一屏就固定用上面这组 ✓
            # ★★ 2026-11-09 实测（喜剧秀场这一版）：面具是**竖向列表**
            #   「镜头面具(966,825) / 斗士面具(966,1199) / 财猫面具(966,1570)」，
            #   **屏幕上根本没有「确定」按钮** —— 点卡片本身就算选中。
            #   旧代码死点 (628,1828) 会落空，然后等 8 秒、无限循环 ✗
            _clicked = False
            for _name in ('镜头面具', '斗士面具', '财猫面具', '欢愉假面', '面具'):
                _p = ocr_find(_name, tries=1, interval=0.4)
                if _p:
                    log('      · 点面具「%s」@ (%d,%d)' % (_name, _p[0], _p[1]))
                    click_phys(_p[0], _p[1], settle=1.5)
                    _clicked = True
                    break
            if not _clicked:
                log('      · 没认出面具名 → 兜底点左下卡面 (628,1828)')
                click_phys(628, 1828, settle=1.5)
            time.sleep(1.8)
            if not click_text('确定', tries=2, interval=0.8) and \
                    not click_text('确认', tries=2, interval=0.8):
                log('      · 无确定按钮（这一版就是点卡即选）→ 继续等进区域')
            time.sleep(6.0)
            continue
        if state == 'pick_miracle':
            log('      · 这是「选择惊世奇迹」界面')
            # 实测（sr_miracle.png）：三张卡名字在 y≈1108、卡体在 y≈1250；
            #   按钮是「确认」不是「确定」。
            click_phys(942, 1250, settle=1.3)      # 第 1 张卡体
            time.sleep(2.5)
            if not click_text('确认', tries=4, interval=1.2):
                click_phys(2296, 1941, settle=1.2)  # 兜底：确认按钮实测位置
            time.sleep(9.0)
            continue
        if state == 'pick_station':
            pick_station()
            continue
        if state == 'phone_menu':
            log('      · 这是手机菜单（不是暂停面板）→ 按 ESC 关掉')
            press('esc', 2.0)
            continue
        if state == 'pause_panel':
            log('      · 在暂停面板（副本里只有 暂离 / 结束并结算）→ 走暂离重进')
            if leave_and_rejoin(h) and clear_area(h, tries=1):
                pick_station()
            continue
        if state == 'settle':
            log('      · 已到结算界面')
            click_text('返回主界面', tries=5, interval=1.2)
            break
        if state == 'mode_select':
            log('      · 在模式选择 → 周期演算 → 启动')
            click_text('周期演算', tries=5, interval=1.5)
            time.sleep(4.0)
            click_text('启动', tries=6, interval=1.5)
            time.sleep(14.0)
            continue

        # ★ 事件全屏界面：先把剧情走完（否则后面所有点击都打在这个界面上）
        if is_event_ui():
            log('      · 当前停在事件界面 → 先把剧情走完')
            do_event_dialog()
            continue

        # ★ 清场 + 过门：小步走、边走边找 `[F]` 提示；把区域里的互动全清掉门才会醒
        if clear_area(h):
            log('      · 门开了 → 选站点卡')
            pick_station()
        else:
            log('  ⚠ 这一轮没清出「选择下一站」，跳过（下一轮会暂离重进再来）')

    log('')
    log('=== 跑完 %d 个区域 ===' % max_areas)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='差分宇宙 · 自动跑（第一版）')
    ap.add_argument('--areas', type=int, default=14, help='最多跑几个区域')
    ap.add_argument('--dry', action='store_true', help='只打印计划')
    ap.add_argument('--skip-entry', action='store_true',
                    help='跳过进场判断（假定已经在副本里，不做任何检测）')
    ap.add_argument('--force-entry', action='store_true',
                    help='强制走完整进场流程（默认是**自动判断**：已在局内就直接接着跑）')
    args = ap.parse_args(argv)
    return run(max_areas=args.areas, dry=args.dry, skip_entry=args.skip_entry,
               force_entry=args.force_entry)


if __name__ == '__main__':
    sys.exit(main())
