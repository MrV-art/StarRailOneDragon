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
import re
import sys
import threading
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


STATUS_FILE = r"D:\_TOOL_DSH\workbench\div_uni.status.json"


def set_status(text):
    """★ 2026-11-15 作者要求「像 BetterGI 那样显示现在正在做什么」：
    把当前动作写进状态文件，由 div_uni_status.py 的浮层窗口实时显示。"""
    try:
        import json as _json
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            _json.dump({'ts': time.time(), 'msg': str(text)[-160:]}, f, ensure_ascii=False)
    except Exception:
        pass


def log(msg):
    print(msg, flush=True)
    try:
        set_status(msg)
    except Exception:
        pass


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
        try:
            beat('ocr_find')
        except Exception:
            pass
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

_SHOT_LOCK = threading.Lock()


def screenshot():
    """物理分辨率截图（3840x2160，BGR）。

    ★ 2026-11-10 多线程：pyautogui 的截图**不是线程安全的**，
      检测线程和主线程都会截图，所以这里加锁串行（单次 ~75ms，可接受）。
    """
    import numpy as np
    with _SHOT_LOCK:
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


def _scan_side_of_door(img, door, side, inner_px=150, half=1500):
    """在门的一侧找「竖边对」构成的直立面板（事件画框）。

    2026-11-15 实测（素材 mat_01_full.png，真值 左1411,779 / 右2221,778）：
        左 → (1426,779) 177x275 误差 15px ✓
        右 → (2220,779) 205x269 误差  1px ✓
    """
    import cv2
    import numpy as np
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    S = hsv[:, :, 1]
    H, W = gray.shape[:2]
    dcx, dcy, dw, dh = int(door['cx']), int(door['cy']), int(door['w']), int(door.get('h') or door['w'] * 2)
    # ★ 内侧边距用**固定像素**（门宽会因光晕而飘：素材 234 / 实时 285 ✗ 比例边距会推偏 ROI）
    if side == 'L':
        x1, x2 = dcx - half, dcx - inner_px
    else:
        x1, x2 = dcx + inner_px, dcx + half
    y1, y2 = dcy - int(dh * 0.80), dcy + int(dh * 0.55)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(W, x2), min(H, y2)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return []
    e = np.abs(cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3)).sum(axis=0)
    col = cv2.GaussianBlur(e.reshape(1, -1), (21, 1), 0).ravel()
    th = col.max() * 0.40
    peaks = [i for i in range(2, len(col) - 2)
             if col[i] > th and col[i] == col[max(0, i - 25):i + 25].max()]
    res = []
    for i in range(len(peaks)):
        for j in range(i + 1, len(peaks)):
            ww = peaks[j] - peaks[i]
            if not (140 <= ww <= 420):
                continue
            sub = roi[:, peaks[i]:peaks[j]]
            rr = np.abs(cv2.Sobel(sub, cv2.CV_32F, 0, 1, ksize=3)).sum(axis=1)
            rr = cv2.GaussianBlur(rr.reshape(-1, 1), (1, 21), 0).ravel()
            if rr.size < 60:
                continue
            top = int(np.argmax(rr[:len(rr) // 2]))
            bot = len(rr) // 2 + int(np.argmax(rr[len(rr) // 2:]))
            hh = bot - top
            if not (0.8 * ww <= hh <= 1.9 * ww):
                continue
            cx = x1 + (peaks[i] + peaks[j]) // 2
            cy = y1 + (top + bot) // 2
            ss = S[max(0, cy - hh // 3):cy + hh // 3, max(0, cx - ww // 3):cx + ww // 3]
            res.append({'cx': cx, 'cy': cy, 'w': ww, 'h': hh,
                        'S': float(ss.mean()) if ss.size else 0.0,
                        'side': side, 'src': 'beside_door'})
    out = []
    for r in sorted(res, key=lambda d: -d['S']):
        if all(abs(r['cx'] - k['cx']) > 80 or abs(r['cy'] - k['cy']) > 80 for k in out):
            out.append(r)
    return out[:2]


def find_events_beside_door(img=None, door=None, verbose=False, refs=None):
    """★★★ 2026-11-15 方案：**以门为参照**找左右两侧的事件画框。

    为什么不按"画的内容/形状"硬认（都实测失败）：
        * Canny+双层轮廓 → 0 命中（画内部边缘太碎，框线不闭合）
        * 高饱和矩形块   → 0 命中（画内部颜色不均匀，还会跟背景树连成一片）
        * 边框结构模板   → 自匹配 0.516，另一扇只有 0.151（两边框花纹不同）
    为什么这门侧扫描可行：
        * 差分宇宙里事件**总是成对摆在门左右**、同一高度、尺寸相近；
        * 门本身可靠（粉色矩形，偏差 83px）；
        * 面板有**两条强竖直边**，且内部是"画"（饱和度明显高于背景）。
    返回两侧各最多 1 个（按内部饱和度排序，S<60 的丢弃）。
    """
    if img is None:
        img = screenshot()
    if door is None:
        door = find_door_pink_rect(img)
    if door is None:
        if verbose:
            log('    · 门侧找事件：没有门 ⇒ 跳过')
        return []
    out = []
    refs = list(refs or [])
    for side in ('L', 'R'):
        cands = _scan_side_of_door(img, door, side)
        for c in cands:
            if c['S'] < 60.0:
                continue
            # ★ 若另一侧已有确认事件，候选必须与它**同高、同尺寸量级**（否则就是树干/招牌）
            # ★ 实测：140px / 宽比1.8 太松 ⇒ 树干(374x373, 宽比1.63) 也被放过 ✗
            #   真值互证对：左 230x288(cy780) 右 217x285(cy785) ⇒ Δcy=5 宽比0.94
            #   ⇒ 收紧到 Δcy≤60、宽比 0.70~1.40、高比 0.70~1.40
            if refs and not any(abs(c['cy'] - r['cy']) <= 60
                                and 0.70 <= c['w'] / float(max(1, r['w'])) <= 1.40
                                and 0.70 <= c['h'] / float(max(1, r['h'])) <= 1.40
                                for r in refs):
                if verbose:
                    log('    · 门侧候选被判为误报（与参照不同高/不同大）: %s (%d,%d) %dx%d'
                        % (side, c['cx'], c['cy'], c['w'], c['h']))
                continue
            out.append(c)
            refs.append(c)
            break
    if verbose:
        log('    · 门侧找事件: %d 个 %r' % (len(out), [(c['side'], c['cx'], c['cy'], round(c['S'])) for c in out]))
    return out


def find_events(img=None, verbose=False):
    """★★★ 2026-11-15 统一入口：**YOLO 锚点 + 门侧扫描互证**，返回事件画框列表。

    实测数据（素材 mat_01_full.png，奖励区左右各一）：
        YOLO `模拟宇宙事件`：左 conf 0.95 / 右 conf 0.16（右边画得暗+被树挡）
        门侧扫描：左 (1426,779) 误差 15px / 右 (2220,779) 误差 1px
    ⇒ 任一路单独都不够，合起来才稳：
        A. YOLO conf ≥ 0.30  → 直接确认（锚点）
        B. YOLO 低置信但"与锚点同高同尺寸、在另一侧" → 互证采纳
        C. 仍不足 2 个 → 门侧扫描补齐，且候选必须与已确认事件**同高同尺寸**
    """
    if img is None:
        img = screenshot()
    raw = [it for it in yolo_detect(0.08, img) if it[0] in CATE_EVENT]
    out = []
    for cate, label, conf, cx, cy, w, h in raw:
        if conf >= 0.30:
            out.append({'cx': cx, 'cy': cy, 'w': w, 'h': h, 'conf': conf,
                        'src': 'yolo', 'side': 'L' if cx < 1920 else 'R'})
    anchors = list(out)
    for cate, label, conf, cx, cy, w, h in raw:
        if conf >= 0.30:
            continue
        for a in anchors:
            if abs(cy - a['cy']) <= 160 and 0.5 <= w / float(max(1, a['w'])) <= 2.0 \
                    and abs(cx - a['cx']) > 200:
                out.append({'cx': cx, 'cy': cy, 'w': w, 'h': h, 'conf': conf,
                            'src': 'yolo_low_corroborated',
                            'side': 'L' if cx < 1920 else 'R'})
                break
    if len(out) < 2:
        try:
            door = find_door_pink_rect(img)
        except Exception:
            door = None
        if door is not None:
            for c in find_events_beside_door(img, door, verbose=verbose, refs=out):
                if all(abs(c['cx'] - o['cx']) > 120 for o in out):
                    out.append(c)
    out.sort(key=lambda d: d['cx'])
    if verbose:
        log('    · 事件识别: %d 个 %r' % (len(out), [(o['side'], o['cx'], o['cy'], o['src']) for o in out]))
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
            use_technique()
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
    use_technique()
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
    use_technique()
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
            # ⚠ 不再补按秘技（作者规则：只放一次）
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
    # ★★ 2026-11-15 死循环 bug：原来这里认「继续进度」✗，可**差分宇宙大厅也有「继续进度」**
    #   ⇒ 大厅被判成暂停面板 → 脚本"暂离→重进"无限循环、角色一动不动 ✗
    #   真·暂停面板的特征是 **暂离 + 结束并结算**（大厅没有这两个词）。
    ('pause_panel',    ['暂离']),
    # ★ 2026-11-08：造物调试台（电饭煲上的包子）—— 按 F 会开这个面板，
    #   里面有「选择加权奇物」，**必须点右上 X 关掉**，否则流程卡住。
    # ★ 2026-11-15 实测 4.7：面板标题是「造物调试台」，选项是
    #   **方程覆写 / 祝福覆写 / 祝福强化 / 剩余热量**（旧关键词一个都不出现 ✗）
    #   ⇒ 旧规则认不出 → 被判成 in_area → 对着面板 UI 疯狂"转视角找门"空转 20 分钟 ✗
    #   ⚠ 不能写「造物调试台」：它**同时是世界的浮空标签**，站在设备旁就会误判成面板 ✗
    ('device_curio',   ['选择加权奇物', '装配或调整加权奇物',
                        '方程覆写', '祝福覆写', '祝福強化', '祝福强化', '剩余热量']),
    # ★ 2026-11-15：「还有未使用的热量，是否确认离开？」—— 关面板时的二次确认
    ('leave_confirm',  ['未使用的热量', '是否确认离开']),
    # ★ 2026-11-15 作者：「现在是在角色复活的机器的界面，退出来就行了，不用管」
    #   这类按 F 打开的杂项面板**一律直接关**（效率优先），别让它们盖住区域界面
    ('misc_panel',     ['角色重置', '选择无法战斗的角色', '暂无目标']),
    # ★★ 2026-11-15：「选择奇物」界面——卡片说明里有「获得」，会被 event_dialog 抢判 ✗
    #   ⇒ 必须在 event_dialog 之前认出来
    ('pick_curio',     ['选择奇物', '请选择奇物', '查看已有奇物']),
    # ★ 2026-11-08：ESC 连按会把暂停面板关掉、停在**手机菜单**上（实测卡在这里出不来）
    ('phone_menu',     ['梦境护照', '均衡等级', '开拓等级']),
    # ★★ 2026-11-15 误判修正：原来认「结束并结算」✗ —— 可**差分宇宙大厅（模式选择）**
    #   右下角也有这个按钮 ⇒ 大厅被判成结算页 → 流程走错 ✗
    #   真·结算页才有「探索成功 / 分析报告 / 返回主界面」。
    ('settle',         ['探索成功', '分析报告', '返回主界面']),
    ('bless_choose',   ['选择祝福', '选择一项祝福']),
    ('event_dialog',   ['获得', '确定']),
    # ★ 2026-11-09：**奇物大转盘**（开门/事件后弹出的抽奖机）——
    #   实测脚本不认识它，会一直停在这个界面 ✗
    ('roulette',       ['奇物大转盘', '开始抽奖', '剩余次数']),
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
        if s < 0.45:                     # ★ 0.55 → 0.45（提示字偶发偏低分）
            continue
        if not (x1 <= cx <= x2 and y1 <= cy <= y2):
            continue
        # ★★ 2026-11-15 修：原来要求**完全等于**（tt in PROMPT_WORDS）
        #   ⇒ OCR 多/少一个字就匹配不上（实测画面明明有「F 造物调试台」，这里返回 [] ✗）
        #   改成**包含匹配**。
        if any(k in tt for k in PROMPT_WORDS):
            hits.append((tt, int(cx), int(cy)))
    if not hits:
        # 诊断：把提示框区域内的 OCR 结果打出来（便于下次定位）
        try:
            in_box = [(t.strip(), int(cx), int(cy))
                      for t, s, cx, cy in ocr_items()
                      if s >= 0.35 and (x1 <= cx <= x2 and y1 <= cy <= y2)]
            if in_box:
                log('      · 提示框内有文字但没匹配: %r' % (in_box[:6],))
        except Exception:
            pass
    return hits


def is_world(items=None):
    """在区域里（不是全屏界面）：左上角有位面计数，或右上「状态效果」。"""
    items = items if items is not None else ocr_items()
    txt = '|'.join(_texts(items))
    return ('位面' in txt) or ('状态效果' in txt)


def roulette_topleft():
    """左上角 OCR 判「奇物大转盘」（作者 2026-11-09 建议：左上角识别最稳）。

    实测该界面左上角直接写「奇物大转盘」（置信度 1.0），
    而区域里左上角是「(n/13) 位面-XX」。
    """
    try:
        import cv2
        img = screenshot()
        crop = cv2.resize(img[0:300, 0:950], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        txt = ''.join(t.strip() for t, s, _, _ in ocr_items(crop) if s >= 0.35)
        return '转盘' in txt
    except Exception:
        return False


def event_ui_topleft():
    """**OCR 左上角**判断是否在事件界面（作者 2026-11-09 建议）。

    依据画面布局：
      * 事件界面：左上角显示「差分宇宙 / 事件」（小图标 + 两行字）
      * 区域里  ：左上角显示「（n/13）第一位面-铸造」

    只裁左上 900x280 再放大 2 倍做 OCR —— 比整屏 OCR 快一个数量级（~50ms vs 1~2s），
    而且不会因为对话框正文里出现"事件"二字而误判 ✓
    """
    try:
        import cv2
        img = screenshot()
        crop = img[0:300, 0:950]
        crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        txt = ''.join(t.strip() for t, s, _, _ in ocr_items(crop) if s >= 0.35)
        return '事件' in txt
    except Exception:
        return False


def is_event_ui(items=None):
    """事件全屏界面。

    ★ 2026-11-09 作者建议：**先 OCR 左上角**（那里直接写「差分宇宙 / 事件」），
      比整屏匹配又快又准；整屏匹配保留作兜底。
    """
    try:
        if items is None and event_ui_topleft():
            return True
    except Exception:
        pass
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


# 2026-11-15: 去掉过于宽泛的 技能 关键词（选祝福界面也会出现，导致误判 2 秒打完）
BATTLE_WORDS = ('弱点', '回合', '自动战斗', '战斗开始', '终结技', '战技点', '行动条')


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
        # ★ 2026-11-15 作者：「放进中断，免得在跑其他函数（时插不进去）」
        #   战斗最长等 420 秒 ⇒ 必须能被打断
        check_pause('等战斗')
        try:
            beat('wait_battle')
        except Exception:
            pass
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


def sweep_with_heading(n=40, step=550, width=900, settle=0.15):
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
    for _i in range(n):
        img = screenshot()
        # ★★ 2026-11-09：**in_battle() 每帧调一次 ≈ 2 秒**（内部 OCR/YOLO），
        #   40 帧就是 80 秒 —— 采样从 12 秒变成 102 秒的真正原因 ✗
        #   改成每 8 帧查一次（战斗中画面会剧变，8 帧内必定发现）。
        if _i % 20 == 0 and in_battle():
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
        # 作者 2026-11-09：原来每帧 sleep(0.4)，40 帧白等 16 秒；
        # 游戏转视角是瞬时的，0.15 秒足够刷新（实测拼接质量不变）
        time.sleep(settle)
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
        # ★★ 2026-11-09 数据定案：**只认黄框，不要"纯白也算"**。
        #   实测真门 黄框 0.014~0.16；而店铺门面/警戒带 黄框 0.00、白色 0.3~0.8
        #   ⇒ 之前"白色 0.10~0.35 也算门"把店招选成门，转过去全是店 ✗
        if yf < 0.012:
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
    for seg in range(14):
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            wait_battle(timeout=420)
            return True
        # ★★ 2026-11-09：**出生点前有栏杆**，直走会被挡住 ⇒ 每 3 段侧移一次绕开。
        #   （实测对准门后走 10 秒仍进不去，就是被栏杆卡住）
        if seg % 3 == 2:
            _side = 'a' if (seg // 3) % 2 == 0 else 'd'
            log('      · 第%d段：侧移 %s 绕栏杆/障碍' % (seg + 1, _side.upper()))
            pyautogui.keyDown(_side)
            time.sleep(0.55)
            pyautogui.keyUp(_side)
            time.sleep(0.2)
        pyautogui.keyDown('w')
        time.sleep(1.0)
        pyautogui.keyUp('w')
        time.sleep(0.2)
        for _ in range(2):
            press_f_checked()
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
# ★★★ 2026-11-15 作者要求：「**对着门的截图的像素一个个比较**」
#   历史实拍的门截图全部纳入模板组（Learn/tools/templates/door*.png），
#   颜色法（find_door_pink）已证伪（§8.1）——它会把**蓝色能量栅栏**当成门 ✗
DOOR_TEMPLATES = ('door9.png', 'door_casting.png', 'door_wealth.png',
                  'door.png', 'door2.png', 'door3.png', 'door4.png',
                  'door5.png', 'door7.png', 'door8.png')

# ★★★ 2026-11-09 作者点出的瓶颈：**门一转过去模板就丢**（正对 0.82、偏 30° 掉到 0.5 以下），
#   而闭环恰恰要求"转的过程中还能看见门"。
#   对策：用 cv2 透视变换把正对模板生成 **±15°/±30° 变体**（远边变窄变矮，就是侧看的样子）。
#   ⚠ 这些**只在二级匹配**里用（一级没命中才补），否则 7 个模板 × 6 尺度太慢（1.9 秒/次）✗
DOOR_TEMPLATES_ANGLED = (
    'door_wealth_m15r.png', 'door_wealth_m15l.png',
    'door_wealth_m30r.png', 'door_wealth_m30l.png',
)
# 2026-11-09 新增 door_wealth.png：从「财富」区实拍裁的（粉门+两只白眼+黄框窗），自匹配 1.000。
# 这区的品红广告牌/海报和门颜色几乎一样，**只能靠模板区分** ✓


# ════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者选定方案①：**特征点匹配（ORB + RANSAC 单应）**
#
#   为什么：模板匹配 cv2.matchTemplate 是**尺度敏感**的（同一模板换个距离就废 ✗），
#   而 ORB 关键点+描述子是**尺度/旋转不变**的，还能用 RANSAC 剔掉错误匹配、
#   直接解出目标在画面里的位置和大小 ⇒ 远近都能用 ✓
#
#   参考图：作者提供的近距离门截图（上半部，避开角色） + 早前参考图裁出的整门
# ════════════════════════════════════════════════════════════════════════

# ★ 只留两张最像本作门的参考图（每多一张就多一倍匹配时间）
DOOR_REF_IMAGES = ('door_front_top.png', 'door_ref_crop.png')
_SIFT_CACHE = {}
_FEAT_CACHE = {'ts': 0.0, 'res': None}

# ★ 实测标定（2026-11-15，单元测试）：
#     含门图 内点 28 / 26；非门图（栅栏/外墙/霓虹）内点 8 / 9
#   ⇒ 阈值取 16 可完全分开 ✓
DOOR_MIN_INLIERS = 12


def _sift_refs():
    """懒加载参考图的 SIFT 描述子（只算一次）。"""
    import cv2
    import os as _os
    if _SIFT_CACHE:
        return _SIFT_CACHE
    try:
        sift = cv2.SIFT_create(nfeatures=1500)
    except Exception:
        return {}
    for name in DOOR_REF_IMAGES:
        path = _os.path.join(TPL_DIR, name)
        if not _os.path.exists(path):
            continue
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        kp, des = sift.detectAndCompute(img, None)
        if des is None or len(kp) < 20:
            continue
        _SIFT_CACHE[name] = {'kp': kp, 'des': des, 'w': img.shape[1], 'h': img.shape[0]}
    return _SIFT_CACHE


def find_door_by_features(img=None, min_inliers=None):
    """★★★ 方案①：**SIFT 特征点匹配 + RANSAC 单应**（作者选定）。

    为什么比模板匹配好（实测）：
      * 模板匹配 cv2.matchTemplate 尺度敏感（远模板近处只剩 0.39 分 ✗）；
      * SIFT 关键点尺度/旋转不变，实测内点：**门 26~28、非门 8~9** ⇒ 阈值 16 干净分开 ✓
    返回 {'cx','cy','w','h','score'(内点),'src':'sift:<ref>'} 或 None。
    """
    import cv2
    import numpy as _np
    refs = _sift_refs()
    if not refs:
        return None
    global _FEAT_CACHE
    if img is None:
        now_t = time.time()
        if _FEAT_CACHE.get('res') is not None and now_t - _FEAT_CACHE.get('ts', 0) < 0.4:
            return _FEAT_CACHE['res']
        img = screenshot()
        _FEAT_CACHE['ts'] = now_t
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # ★★ 2026-11-15 实测校准：0.35 虽然快，但**远处小门（宽216→76px）SIFT 认不出**
    #   （v5 一上来就"丢门"就是这个原因 ✗）⇒ 改回 0.5：远处门 27 内点、近处 151 内点 ✓
    sc = 0.5 if gray.shape[1] > 2200 else 1.0
    if sc != 1.0:
        gray = cv2.resize(gray, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
    try:
        sift = cv2.SIFT_create(nfeatures=1200)
        kp2, des2 = sift.detectAndCompute(gray, None)
    except Exception:
        return None
    if des2 is None or len(kp2) < 30:
        return None
    bf = cv2.BFMatcher()
    thr = DOOR_MIN_INLIERS if min_inliers is None else min_inliers
    best = None
    for name, ref in refs.items():
        try:
            knn = bf.knnMatch(ref['des'], des2, k=2)
        except Exception:
            continue
        good = []
        for pair in knn:
            if len(pair) < 2:
                continue
            a, b = pair[0], pair[1]
            if a.distance < 0.80 * b.distance:
                good.append(a)
        if len(good) < max(8, thr):
            continue
        src_pts = _np.float32([ref['kp'][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = _np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        try:
            H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 6.0)
        except Exception:
            continue
        if H is None or mask is None:
            continue
        inl = int(mask.sum())
        if inl < thr:
            continue
        corners = _np.float32([[0, 0], [ref['w'], 0], [ref['w'], ref['h']], [0, ref['h']]]).reshape(-1, 1, 2)
        try:
            proj = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
        except Exception:
            continue
        cx = float(proj[:, 0].mean()) / sc
        cy = float(proj[:, 1].mean()) / sc
        bw = float(_np.linalg.norm(proj[1] - proj[0])) / sc
        bh = float(_np.linalg.norm(proj[3] - proj[0])) / sc
        if not (60 <= bw <= 1600) or not (60 <= bh <= 2200):
            continue
        if not (0.45 <= (bw / max(1.0, bh)) <= 2.2):      # 门是竖长方形
            continue
        cand = {'cx': int(cx), 'cy': int(cy), 'w': int(bw), 'h': int(bh),
                'score': inl, 'src': 'sift:%s' % name}
        # ★ 2026-11-15：多扇门时**优先最大的那扇**（=离得最近，才是我要走的出口）
        #   原来按内点数挑，会在"近处大门"和"远处小门"之间来回跳，导致方向摇摆 ✗
        if best is None or cand['w'] > best['w']:
            best = cand
    if img is not None and False:
        pass
    else:
        _FEAT_CACHE['res'] = best
    return best


def find_door_by_eyes(img=None):
    """★★★ 2026-11-15：**按「白圈套黑瞳」识别随意门**（作者提供近距离截图做模板的思路）。

    随意门的眼睛在游戏里是**纯黑瞳孔 + 纯白眼圈**（实测截图），这是霓虹招牌/
    红蓝栅栏/外墙**都没有**的结构 ⇒ 比颜色法、模板法都稳，且**与距离无关**。

    流程：粉色竖向矩形（粗筛）→ 矩形内找「深色圆斑(瞳孔) + 周围亮白环」
    → 找到 ≥2 只 ⇒ 就是门。瞳孔坐标可直接当作瞄准点。
    """
    import cv2
    import numpy as _np
    if img is None:
        img = screenshot()
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    pink = cv2.inRange(hsv, (135, 25, 100), (180, 238, 255))
    pink = cv2.morphologyEx(pink, cv2.MORPH_CLOSE, _np.ones((9, 9), _np.uint8))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    cnts, _ = cv2.findContours(pink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if ww < 110 or ww > 1500:
            continue
        ratio = hh / float(max(1, ww))
        if ratio < 1.05 or ratio > 2.9:
            continue
        if cv2.contourArea(c) < ww * hh * 0.28:
            continue
        roi = gray[y:y + hh, x:x + ww]
        if roi.size == 0:
            continue
        # ① 深色圆斑 = 瞳孔（纯黑）
        _, dark = cv2.threshold(roi, 60, 255, cv2.THRESH_BINARY_INV)
        dc, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        pupils = []
        for c2 in dc:
            (ex, ey), er = cv2.minEnclosingCircle(c2)
            if er < ww * 0.02 or er > ww * 0.14:
                continue
            a2 = cv2.contourArea(c2)
            if a2 / max(1.0, 3.14159 * er * er) < 0.55:      # 近似圆
                continue
            if ey > hh * 0.72:                                 # 眼睛在上半部
                continue
            # ② 周围要有亮白环（眼白）
            r_in = int(er * 1.15); r_out = int(er * 2.1)
            y0 = max(0, int(ey) - r_out); y1 = min(hh, int(ey) + r_out)
            x0 = max(0, int(ex) - r_out); x1 = min(ww, int(ex) + r_out)
            patch = roi[y0:y1, x0:x1]
            if patch.size == 0:
                continue
            ring = patch > 190
            if float(ring.mean()) < 0.28:                      # 白环占比
                continue
            pupils.append((int(ex), int(ey), int(er)))
        # ③ 两只瞳孔，左右分开、高度接近
        if len(pupils) >= 2:
            pupils.sort(key=lambda t: t[0])
            ok_pair = None
            for i in range(len(pupils) - 1):
                a, bb = pupils[i], pupils[i + 1]
                gap = abs(bb[0] - a[0])
                dy = abs(bb[1] - a[1])
                if ww * 0.22 <= gap <= ww * 0.80 and dy <= hh * 0.18:
                    ok_pair = (a, bb)
                    break
            if ok_pair is None:
                continue
            cand = {'cx': x + ww // 2, 'cy': y + hh // 2, 'w': ww, 'h': hh,
                    'score': 0.88, 'src': 'pupils', 'eyes': [(p[0] + x, p[1] + y) for p in ok_pair]}
            if best is None or cand['w'] > best['w']:
                best = cand
    return best


# ★★★ 2026-11-15 致命 bug 修复：这个常量原来**只有引用、没有定义**
#   ⇒ door_prompt_present() 每次 NameError → 被 except 吞 → 永远返回 False
#   ⇒ 「随意门」提示一直出现，脚本从来不按 F ✗（作者亲眼看到）
DOOR_PROMPT_WORDS = ('随意门', '任意门', '随异门', '随意□')

_FULL_OCR_CACHE = {'ts': 0.0, 'items': None}


def _ocr_full_cached(img=None, max_age=0.8):
    """全屏 OCR 缓存（0.8 秒内复用）——保证不漏判，又不把每步拖到 3 秒。

    ★ 传了 img 就直接识别它（离线单元测试要用这个分支）
    """
    if img is not None:
        return ocr_items(img)
    now = time.time()
    if _FULL_OCR_CACHE['items'] is not None and now - _FULL_OCR_CACHE['ts'] < max_age:
        return _FULL_OCR_CACHE['items']
    items = ocr_items()
    _FULL_OCR_CACHE['ts'] = now
    _FULL_OCR_CACHE['items'] = items
    return items


def door_prompt_present(img=None):
    """★★★ 2026-11-15 作者截图点破的**最可靠判据**：

    门在面前时，屏幕上会出现交互提示 `[F] 随意门`（作者截图实拍）。
    这是 **OCR 文字**，不受距离/光照/角度影响，比模板匹配和颜色法都稳 ✓
    （文档 §15.1：交互提示框是固定屏幕位置，世界浮空标签不能当提示）

    返回 (True, 文字) 或 (False, '')。
    """
    # ★ 2026-11-15：F 提示框位置固定（实测提示文字在 (2504,1225) 附近），
    #   只识别这一小块 ⇒ 从 2~3 秒降到 ~0.2 秒（原来全屏 OCR 把流程拖死 ✗）
    try:
        if img is None:
            img = screenshot()
        h_img, w_img = img.shape[:2]
        # 物理坐标 3840x2160 下的提示区；按实际分辨率等比缩放
        sx = w_img / 3840.0
        sy = h_img / 2160.0
        x0, x1 = int(2000 * sx), int(3100 * sx)
        y0, y1 = int(1100 * sy), int(1360 * sy)
        roi = img[y0:y1, x0:x1]
        if roi.size == 0:
            return False, ''
        items = ocr_items(roi)
        for t, sc, cx, cy in items:
            if sc >= 0.40 and any(k in t for k in DOOR_PROMPT_WORDS):
                return True, t.strip()[:16]
        # ★★ ROI 会漏（实测截图上 OCR 读到「随意门」1.00 分，但 ROI 没覆盖到 ✗）
        #   ⇒ 回退全屏 OCR（0.8 秒缓存，不会拖慢）
        for t, sc, cx, cy in _ocr_full_cached(img):
            if sc >= 0.40 and any(k in t for k in DOOR_PROMPT_WORDS):
                return True, t.strip()[:16]
        # ★ 2026-11-15：**去掉全屏兜底**——实测全屏 OCR 3.16 秒，把 12 步全景拖成 26 秒 ✗
        #   ROI 没命中就返回 False（SIFT 会补上找门的职责）
    except Exception:
        return False, ''
    return False, ''


def find_door_by_tpl(min_score=0.55, near=520, fast=False):
    # ★ 2026-11-09：闭环伺服每步都要检测一次，而"一级+角度二级"实测 2110ms 太慢。
    #   fast=True 时**只扫上次门宽附近的 3 个尺度**（约 300ms），供伺服循环使用；
    #   整帧精确搜索仍用 fast=False。
    """用**门模板组**多尺度匹配找门，带时间一致性。

    ★ 2026-11-09 两处关键设计：
      1. **两级匹配**（性能）：先只跑原始模板（6 个尺度，~800ms）；
         低于 min_score 时，才补跑"姿态变体"（水平压缩 0.93/0.85，2 个尺度）。
         一上来就跑全部变体会慢 3 倍（实测 2436ms）⇒ 会吃掉对正预算 ✗
      2. **姿态容忍**（鲁棒）：模板正对 0.82、侧 20° 掉到 0.37~0.55；
         侧看主要表现是水平压缩（cos20°≈0.94）⇒ 压缩变体正好补这个缺口。

    返回 {'score','cx','cy','w','h','src'} 或 None。
    """
    import cv2
    import numpy as np
    img = screenshot()
    g = cv2.resize(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (1920, 1080), interpolation=cv2.INTER_AREA)
    hits = []

    def _scan(tg0, tname, scales, squash):
        for tw in scales:
            sc = tw / float(tg0.shape[1])
            t = cv2.resize(tg0, None, fx=sc * squash, fy=sc, interpolation=cv2.INTER_AREA)
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

    tg_list = []
    for tname in DOOR_TEMPLATES:
        tpl0 = cv2.imread(os.path.join(TPL_DIR, tname))
        if tpl0 is None:
            continue
        tg_list.append((cv2.cvtColor(tpl0, cv2.COLOR_BGR2GRAY), tname))

    # ---- 一级：原始模板 ----
    _lw = _TPL_LAST.get('w') or 0
    if fast and _lw >= 60:
        # 除了"上次门宽"附近，再带上两个远端尺度，避免门忽然变大/变小就搜不到
        _scales = tuple(int(max(60, min(460, _lw * f))) for f in (0.6, 0.85, 1.0, 1.25, 1.7))
    else:
        _scales = (90, 130, 175, 230, 300, 380)
    for tg, tname in tg_list:
        _scan(tg, tname, _scales, 1.0)
    best1 = max([h['score'] for h in hits], default=0.0)

    # ---- 二级：只有没达标才补姿态变体（慢，但只在必要时） ----
    if best1 < min_score and not fast:
        for tg, tname in tg_list:
            for sq in (0.93, 0.85):
                _scan(tg, tname, (130, 230), sq)
        # ★ 再补"多角度模板"（±15°/±30° 透视变体）—— 解决"一转过去就丢门"
        for aname in DOOR_TEMPLATES_ANGLED:
            ta = cv2.imread(os.path.join(TPL_DIR, aname))
            if ta is None:
                continue
            tag = cv2.cvtColor(ta, cv2.COLOR_BGR2GRAY)
            _scan(tag, aname, (110, 150, 200, 260), 1.0)

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
    _TPL_LAST['w'] = best['w']      # ★ 记住门宽，供 fast 模式推尺度
    best['h'] = int(best['w'] * 1.4)
    return best


def find_door_pink(min_area_small=220):
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
        # ★ 2026-11-09 放宽下限：**街道远处的小门**在 960x540 下可能只有 ~25x30 px，
        #   原来 area<600 / hh<40 会直接扔掉 ⇒ 整条街都"看不到门" ✗
        if area < min_area_small or hh < 16:
            continue
        # ★ 2026-11-08 晚：排除超大块（血迹）。首领区那滩血迹 面积157763/宽2064px。
        # ★★ 2026-11-09 放宽：**贴脸的门也很大**（作者指出「门就在前方、粉色不会变」），
        #   原来 w>900 就扔掉 ⇒ 站到门前反而检测不到 ✗
        #   改成：只有"又大又没门特征"才扔；有黄框/白点的大块照样收。
        _huge = (w * 4 > 1800 or area > 150000)
        if _huge:
            _roi0 = small[y:y + hh, x:x + w]
            _rh0 = cv2.cvtColor(_roi0, cv2.COLOR_BGR2HSV)
            _yf0 = float(cv2.inRange(_rh0, (15, 90, 150), (40, 255, 255)).mean()) / 255.0
            _wf0 = float(cv2.inRange(_rh0, (0, 0, 185), (180, 75, 255)).mean()) / 255.0
            if _yf0 < 0.015 and _wf0 < 0.08:
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
        if not (yf >= 0.015 or wf >= 0.08):
            continue
        off = (x + w / 2.0) * 4 - 1920
        score = yf * 20.0 + wf * 8.0 + min(area, 3000) / 3000.0 - abs(off) / 1200.0
        if best is None or score > best['score']:
            best = {'score': round(score, 2), 'cx': int((x + w / 2.0) * 4),
                    'cy': int((y + hh / 2.0) * 4), 'w': int(w * 4), 'h': int(hh * 4),
                    'yellow': round(yf, 3), 'white': round(wf, 3), 'src': 'pink+yellow'}
    return best



def pick_door(min_score=0.60, tol_near=420, fast=False):
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

    # ★★ 2026-11-09 再次调整：**模板优先**。
    #   原因：财富区的**品红广告牌/海报**在颜色上与门几乎一样（都品红+黄框），
    #   颜色判据必然误检；而 door_wealth.png 自匹配 1.000、在场景里 0.6+，
    #   模板能把门和海报干净分开 ✓
    # ★ 2026-11-09：透传 fast（原来写死 0.55 且不传 fast ⇒ 伺服的快速通道拿不到门 ✗）
    _td = find_door_by_tpl(min_score=max(0.55, min_score), fast=fast)
    if _td is not None:
        _d = _accept(_td)
        if _d is not None:
            return _d

    # ★★ 2026-11-09 定案：**不再用颜色判据找门**。
    #   财富区的品红广告牌/海报与门颜色几乎一致（都品红+黄框），
    #   实测每次都把广告牌当门（偏+1760 宽320 之类），追过去全是建筑 ✗
    #   三次成功过门（财富/铸造/财富）全部是**模板命中**（0.65~0.82），
    #   所以这里只信模板 + YOLO；模板没命中 ⇒ 门不在视野里，交给全景/VLM。

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

    ★# ⚠️ 2026-11-15 按文档 §17.25 更正：**点击地面 = 攻击**（带位移），不是寻路 ✗
#   旧注释「星铁支持点击地面自动走过去」已被作者否定（作者：点击是攻击，只不过攻击会有位移）
#   需要绕障时只能用键盘 W/A/S/D 小步走位 ✓
#
# （旧结论留档）2026-11-08：曾以为能点击地面自动走过去（实测点一下门，
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
                press_f_checked()
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
    _blind = 0        # 连续"门不可见"次数（用于交替左右侧移）
    tol = max(100, int(center_tol * 0.8))   # 模板抖动约 ±60，容差必须更宽

    # ★★ 2026-11-09：**战斗类区域先清怪，别急着找门**（门是锁的，清完才开）。
    #   且没有粉门/模板门时根本不该花时间对齐假门。
    _kind = area_kind()
    if _kind in BATTLE_KINDS:
        # ★★ 2026-11-09：**战斗区一律先清怪**，不管有没有看到门 ——
        #   门是锁的，清完才开（作者：「不打怪你门开不了」）。
        #   之前加了 "看到门就跳过清怪" 的条件 ⇒ 检测到假门就直接去撞门，白跑 60 秒 ✗
        _has_door = False
        if not _has_door:
            # ★★ 作者 2026-11-09 的打法：**识别门 → 放秘技 → 朝门方向一直走**，
            #   战斗结束视角自动面对敌人（=门的方向），不走过头就不用重新找门。
            # ★★ 2026-11-09 作者指定：**识别到门睁眼就直接走**（不按 F、不转向）
            _d0 = find_door_pink()
            if _d0 is not None:
                # 睁眼判定仅作参考：视觉判据不可靠（实测闭眼门被判 open=True ✗），
                # 真正的判据是"走过去按 F 能不能出选站点卡"。
                try:
                    _open, _eyes = door_eyes_open(_d0)
                    log('      · 门状态(参考): open=%s eyes=%d —— 不作决策依据' % (_open, _eyes))
                except Exception:
                    pass
                log('      · 看到门 → 走过去按 F（用结果判定，不猜状态）')
                if walk_into_door(_d0, max_seconds=45):
                    return True
            # ★★★ 2026-11-09 作者定案（首领房）：
            #   「没识别到门，说明有首领挡着，**应该开秘技打它**」
            #   ⇒ 战斗区优先走 hunt_enemies（它内部：先看门 → 看不到就找敌人撞）
            log('      · 战斗类区域(%s) → 先看门，看不到就开秘技打挡路的敌人' % _kind)
            if hunt_enemies(max_seconds=min(150.0, max(90.0, max_seconds))):
                return True
            log('      · 清怪路线没成 → 退回「对门+秘技+直走」')
            if walk_door_with_technique(max_seconds=min(120.0, max(70.0, max_seconds))):
                return True
            log('      · 两种打法都没成 → 退回漫游')
    # ★ 作者要求：**丢了目标就用全景**；这里 sweep=0 关掉"小幅度扫描"（它救不回来）
    if not turn_to_center_of(lambda: pick_door(min_score=0.55), name='门',
                             tol=tol, max_iter=12, sweep=0):
        # ★★ 2026-11-09：粉色检测在本区**极不稳定**（假阳性满天飞），
        #   所以"丢失目标"后**先让 VLM 指方向**（它擅长"有没有门/左中右"），
        #   再退到全景。
        log('      · 门没对上（丢失目标）→ 先让 VLM 指路')
        if walk_by_vlm(max_seconds=80):
            log('      · ✓ VLM 指路成功')
            return True
        log('      · VLM 指路没成 → 全景兜底')
        log('      · 门没对上（丢失目标）→ 全景扫一次（计数 +1）')
        r = pano_once('丢失目标')
        if r is None:
            return False
        if r:
            # 同样要硬校验：全景"成功"也不等于过门
            if on_station_picker() or area_passed():
                return True
            log('      · 全景后仍未出现选站点卡 → 继续本轮')
        # ★ 2026-11-09 作者指定：全景也找不到 ⇒ **随机走+重扫**，三次都不行才重置位置。
        log('      · 全景没找到目标 → 兜底②：随机走+重扫（最多 3 次）')
        _before = hud_progress()        # ★ 局部变量（这里在 approach_door 里，不能用 clear_area 的 _hud0）
        random_walk_rescan(tries=3)
        # 硬校验：随机走"找到东西"不等于过门，必须选站点卡出现或进度号变了
        if on_station_picker() or area_passed() or (hud_progress() not in (None, _before)):
            log('      · ✓ 已推进（进度号 %s → %s）' % (_before, hud_progress()))
            return True
        log('      · 随机走后仍未推进（进度号仍是 %s）' % (hud_progress(),))
        return False
        log('      · 兜底②未找到 → 兜底③：覆盖推进')
        return coverage_clear(max_seconds=min(70.0, max_seconds))

    # ★ 段数 12 -> 30：0.55s/段，最多约 16 秒步行（对 10 米外的门够用）
    for seg in range(30):
        check_pause('过门')
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
        # ★★ 2026-11-09：**步行段必须按 F**（作者指出"到门口为什么不开门"）。
        #   DU 的随意门要 F 交互，光走过去不触发 ⇒ 铸造区卡死的直接原因 ✗
        if on_station_picker() or area_passed():
            return True
        press_f_checked()
        if on_station_picker() or area_passed():
            log('      · 按 F 后出现选站点卡')
            return True
        for _ in range(2):
            press_f_checked()
            if on_station_picker() or area_passed():
                log('      · 按 F 出现选择下一站 ✓')
                return True
            if is_event_ui():
                do_event_dialog()
                break
            if in_battle():
                wait_battle(timeout=420)
                return True
        # ★★ 2026-11-09：**对正取 3 次中位数**（抑制跳变）+ **按门宽自适应步长**。
        #   实测：门很近时(宽560) door 偏仍在 +682/+1648/-160 之间跳，
        #   而 ±250 鼠标 ≈ ±260px 转向太粗 ⇒ 越对越偏、最后判"丢失目标" ✗
        _ds = [x for x in (pick_door(min_score=0.50) for _ in range(2)) if x is not None]
        d = None
        if _ds:
            _ds.sort(key=lambda z: z['cx'])
            d = _ds[len(_ds) // 2]              # 中位数，压掉抖动
        if d is not None:
            off = d['cx'] - 1920
            log('      · 段%d 门偏 %+d 宽 %d（3 次中位）' % (seg + 1, off, d['w']))
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
                # 门越近（宽越大）转向越细：宽≥400 ⇒ 上限 ±90；否则 ±260
                _lim = 90.0 if d['w'] >= 400 else 260.0
                mx = max(-_lim, min(_lim, off / TURN_K * TURN_DAMP))
                _send_input_move(int(mx), 0)
                time.sleep(0.3)
        else:
            # ★★ 2026-11-09 作者提示：**门和奇物机在一条直线上，装置挡在中间**。
            #   所以"门突然不可见"通常不是走过头，而是被装置挡住了视野/去路。
            #   对策：**小幅侧移绕开，再继续朝门走，反复尝试**（左右交替）。
            _blind += 1
            if _blind <= 8:
                # ★ 不要每次交替（左右互相抵消）！先连续往左 4 次，再往右 4 次
                side = 'a' if _blind <= 4 else 'd'
                log('      · 段%d 门不可见 → 小侧移（%s 0.45s）绕开中间的装置（第%d次）'
                    % (seg + 1, side.upper(), _blind))
                pyautogui.keyDown(side)
                time.sleep(0.6)
                pyautogui.keyUp(side)
                time.sleep(0.2)
                # 侧移后立刻再朝门方向走一小步
                pyautogui.keyDown('w')
                time.sleep(0.55)
                pyautogui.keyUp('w')
                time.sleep(0.2)
                press_f_checked()
                if on_station_picker() or area_passed():
                    return True
            else:
                log('      · 段%d 门不可见（侧移 8 次仍不见）→ 结束本轮' % (seg + 1))
                break
        if d is not None:
            _blind = 0
    return on_station_picker() or area_passed()


BATTLE_KINDS = ('战斗', '精英', '首领')


def door_eyes_open(d, img=None):
    """判断这扇门是否**已睁眼（解锁）**——作者 2026-11-09 指定：
    「以后识别到门睁眼就直接走」。

    做法：取门框内的区域，数**亮白色圆点**（眼睛）。≥2 个就认为睁眼。
    返回 (bool, eyes_count)。
    """
    import cv2
    import numpy as np
    try:
        if img is None:
            img = screenshot()
        H, W = img.shape[:2]
        sx, sy = W / 960.0, H / 540.0
        x0 = int((d['cx'] - d['w'] / 2) / (W / 960.0))
        x1 = int((d['cx'] + d['w'] / 2) / (W / 960.0))
        y0 = int((d['cy'] - d['h'] / 2) / (H / 540.0))
        y1 = int((d['cy'] + d['h'] / 2) / (H / 540.0))
        small = cv2.resize(img, (960, 540), interpolation=cv2.INTER_AREA)
        roi = small[max(0, y0):min(540, y1), max(0, x0):min(960, x1)]
        if roi.size == 0:
            return False, 0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, (0, 0, 200), (180, 60, 255))
        white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _l, st, _c = cv2.connectedComponentsWithStats(white, 8)
        eyes = 0
        for i in range(1, n):
            _x, _y, w, h, area = st[i]
            if area < 8 or w < 2 or h < 2:
                continue
            if 0.4 <= w / float(max(1, h)) <= 2.5:
                eyes += 1
        return eyes >= 2, eyes
    except Exception:
        return False, 0


def vlm_door_eyes(d=None, pad=0.25):
    """用**本地视觉模型**判断门是睁眼（已解锁）还是闭眼（未解锁）。

    ★ 2026-11-09 实测（这是本项目的**最佳方案**）：
      纯几何判据（白色圆点/白色占比）在 6 张样本上完全无区分度 ✗；
      而 qwen3-vl:8b 一眼就能判，还会给理由：

        sr_door_closeup.jpg（粉圆+黑横线）→ **闭合**，理由
          「两个圆形眼睛中间有黑色横线，符合闭合眼睛的视觉表现」✓
        sr_approach_result.jpg（白色发光眼）→ **睁开** ✓
        sr_verify_door_box / sr_door_rule_box → 闭合 ✓（4/4 正确）

    返回 'open' / 'closed' / None（判不出或调用失败）。
    """
    import base64
    import json
    import urllib.request
    try:
        import cv2
        if d is None:
            d = find_door_pink()
        if d is None:
            return None
        img = screenshot()
        cx, cy, w, h = d['cx'], d['cy'], d['w'], d['h']
        px, py = int(w * pad), int(h * pad)
        x0 = max(0, cx - w // 2 - px); x1 = min(img.shape[1], cx + w // 2 + px)
        y0 = max(0, cy - h // 2 - py); y1 = min(img.shape[0], cy + h // 2 + py)
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        # 小门放大一点，让眼睛看得清（VLM 对细节仍敏感）
        if crop.shape[1] < 300:
            sc = 300.0 / crop.shape[1]
            crop = cv2.resize(crop, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
        ok, buf = cv2.imencode('.jpg', crop, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if not ok:
            return None
        b64 = base64.b64encode(buf.tobytes()).decode()
        prompt = ('/no_think 这是一扇粉色门上的两只眼睛。'
                  '只回答一个词：【睁开】或【闭合】。')
        body = json.dumps({'model': VLM_MODEL, 'prompt': prompt, 'images': [b64],
                           'stream': False,
                           'options': {'temperature': 0.1, 'num_predict': 30}}).encode('utf-8')
        req = urllib.request.Request(VLM_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8', 'replace'))
        ans = (data.get('response') or '').strip()
        if '睁' in ans:
            return 'open'
        if '闭' in ans:
            return 'closed'
        return None
    except Exception as e:
        log('      · VLM 判门状态失败（不影响主流程）: %s' % e)
        return None


_TURN_GAIN = {'g': None}          # 实测的「屏幕像素 / 鼠标像素」转向增益


def measure_turn_gain(force=False):
    """**现场实测转向增益**：发一个已知鼠标位移，看目标在屏幕上移动了多少像素。

    ★★ 2026-11-09 作者问：「既然识别到门，为什么还会从旁边插肩而过？」
       根因之一：**转向增益写死且不一致**（有的地方 off/2.4、有的 off/1.2）。
       增益差一倍 ⇒ 以为对准了其实偏着 ⇒ 直着走就从门边擦过去了 ✗
       对策：**每次实测**，用 real_gain 换算鼠标位移，闭环迭代到偏差 <60px。
    返回 gain（px/mouse ≈ 0.4~0.6 属正常）或 None。
    """
    if _TURN_GAIN['g'] is not None and not force:
        return _TURN_GAIN['g']
    d1 = None
    try:
        d1 = pick_door(min_score=0.55)
    except Exception:
        d1 = None
    if d1 is None:
        return None
    off1 = d1['cx'] - 1920
    probe = 500
    _send_input_move(probe, 0)
    time.sleep(0.32)
    d2 = None
    try:
        d2 = pick_door(min_score=0.55)
    except Exception:
        d2 = None
    if d2 is None:
        # 测不到就退回已知经验值
        _TURN_GAIN['g'] = 1.0 / TURN_K if 'TURN_K' in globals() else 0.42
        return _TURN_GAIN['g']
    off2 = d2['cx'] - 1920
    g = (off2 - off1) / float(probe)
    g = abs(g)                     # ★ 只用幅度；方向由下面的符号公式决定
    if g == 0 or g < 0.05:
        g = 0.42
    _TURN_GAIN['g'] = g
    log('      · 转向增益实测: %.3f px/鼠标（发%+d → 偏%+d→%+d）'
        % (g, probe, off1, off2))
    return g


def servo_center_door(tol=60, max_iter=10):
    """把门**精确**转到屏幕中心（用实测增益 + 迭代收敛）。返回 (ok, 最后偏差)。"""
    g = measure_turn_gain()
    if g is None:
        return False, None
    off = None
    osci = 0
    for i in range(max_iter):
        d = None
        try:
            d = pick_door(min_score=0.55)
        except Exception:
            d = None
        if d is None:
            return False, off
        off = d['cx'] - 1920
        if abs(off) <= tol:
            log('      · 已居中（偏%+d，第%d次）' % (off, i + 1))
            return True, off
        # ★★★ 2026-11-09 **符号修正（关键）**：
        #   鼠标右移(+m) → 画面物体**左移** ⇒ 物体偏移 off 与所需鼠标 m 的关系是
        #       off + (-g*m) = 0   ⇒   m = off / g
        #   之前写成 -off/g ⇒ **转反了**：门在左(-506)却往右转，越转越远，
        #   日志「偏-506 → -708 → -908」就是铁证 ✗（这也解释了"从门边擦肩而过"）
        mx = off / g * 0.9
        mx = int(max(-900, min(900, mx)))
        if mx == 0:
            mx = 1 if off < 0 else -1
        log('      · 居中第%d次：偏%+d → 转%+d（增益%.2f）' % (i + 1, off, mx, g))
        _send_input_move(mx, 0)
        time.sleep(0.32)

        # ★★ 2026-11-15 自适应增益 + 震荡检测（实测会 ±200~400 来回过冲 ✗）
        #   用"转之前 off / 转之后 off"反推真实增益，逐步修正 g；
        #   连续 3 次变号且幅度没缩小 ⇒ 判定震荡，直接放弃居中（交给调用方"先往前走"）。
        try:
            d2 = pick_door(min_score=0.55)
            if d2 is not None:
                off2 = d2['cx'] - 1920
                if mx:
                    g_obs = (off - off2) / float(mx)
                    if 0.05 < abs(g_obs) < 20:
                        g = 0.5 * g + 0.5 * g_obs
                if (off2 > 0) != (off > 0) and abs(off2) >= abs(off) * 0.8:
                    osci += 1
                else:
                    osci = 0
                if osci >= 3:
                    log('      · 居中震荡（3 次变号不收敛）→ 放弃居中，改为直接往前')
                    return False, off2
        except Exception:
            pass
    return False, off


def approach_door_servo(max_seconds=90, tol=40, step_sec=0.45, min_score=0.55):
    # ★★ 2026-11-09 作者对「闭环」的确切定义（照此实现）：
    #     「旋转视角后，**确认目标是否在正中心**，如果在就移动，如果不在就继续旋转。」
    #   ⇒ tol 收紧到 **40px**（1920 半宽的 2%，约 1° 视场）；
    #   ⇒ **只有真正居中才允许前进**，否则只转不走（以前偏 90px 也照样走 → 擦肩而过 ✗）。
    """**闭环伺服走向门**（★ 作者 2026-11-09 的质问点出的要害）：

        作者原话：「既然识别到了目标门，为什么还会走错路线呢，不能直直朝着门走过去呢？」

        根因：之前是**开环**的 —— 「转一次 → 按住 W 走 1.5~N 秒 → 再检测」。
        走的这段时间里**完全不看门**，转向增益一旦有偏差、或半路被东西挡一下，
        就会偏出去，然后越走越远（擦肩而过）✗

        现在改成**闭环**，每一小步都：看门 → 修朝向 → 走一小步，直到：
          * 门宽足够大（很近了）⇒ 走近 + 按 F（OCR 校验）；
          * 出现选站点卡 ⇒ 成功；
          * 门连续几步看不见 ⇒ 小步原地转找回；
          * **门宽连续几步不涨（被挡住）⇒ 侧移绕开**，这正是"绕过障碍物到目标"的能力。
    """
    import numpy as _np
    t0 = time.time()
    last_off = 0
    lost = 0
    last_w = 0
    no_grow = 0
    steps = 0
    flips = 0
    # ★★ 2026-11-15 实测：真实转向增益 ≈ 1.03 px/px，而 _TURN_GAIN 里记的是 0.42
    #   ⇒ 每次都转过 2.5 倍 → 左右震荡永远收敛不了 ✗
    #   这里**在线学**：每转一次就用 (off_前 - off_后) / mx 反推，指数平滑更新。
    g_eff = abs(_TURN_GAIN['g'] or 0.42)
    prev_off = None
    prev_mx = 0

    log('      · 闭环伺服走向门（先精确居中，再小步闭环前进）')
    # ★ 先用实测增益把门精确转到屏幕中心（作者要的"转到中心再走"）
    try:
        _ok_center, _offc = servo_center_door(tol=50, max_iter=12)
        if _ok_center:
            log('      · 居中完成（偏%+d）→ 开始小步前进' % (_offc or 0))
    except Exception as _e:
        log('      · 居中过程出错: %s' % _e)
    while time.time() - t0 < max_seconds:
        check_pause('走向门')
        steps += 1
        if on_station_picker() or area_passed():
            log('      · ★ 到站（第%d步）' % steps)
            return True
        if in_battle():
            log('      · 半路遇怪 → 打它')
            wait_battle(timeout=420)
            continue
        if is_event_ui():
            log('      · 半路遇事件 → 交互')
            do_event_dialog()
            continue

        d = None
        try:
            # ★★ 2026-11-09：**和扫描用同一个检测器**（pick_door），否则扫描靠 YOLO
            #   找到的门，伺服（纯模板）每帧都认不到 → 日志"这一帧没看到门"×N ✗
            # ★★★ 2026-11-09 **根因修复**：伺服必须**和扫描用完全相同的检测顺序**。
            #   扫描是：find_door_pink() → find_door_by_tpl()；
            #   伺服之前用 pick_door（模板→YOLO→detect_door_cands），
            #   于是"扫描靠 find_door_pink 找到、宽 880 的门"，pick_door 根本不做这个检测
            #   ⇒ 每帧都"没看到门" ✗（这个不一致白烧了好几个小时）
            # ★ 优先读检测线程的共享结论（快 5 倍）；没有才自己检测一次
            d = door_latest(1.5)
            if d is None:
                d = find_door_pink()
            if d is None:
                d = find_door_by_tpl(min_score=min_score)
        except Exception:
            d = None

        if d is None:
            lost += 1
            if lost <= 4:
                # 只是这一帧没认到 ⇒ 朝最后已知方向再走一小步
                log('      · 这一帧没看到门（第%d次）→ 朝最后方向走一小步' % lost)
                pyautogui.keyDown('w')
                time.sleep(step_sec)
                pyautogui.keyUp('w')
                time.sleep(0.12)
            elif lost <= 14:
                # 连续丢 ⇒ 原地小步转，把门找回来
                log('      · 连续 %d 帧没门 → 原地小转找回' % lost)
                _send_input_move(420 if (lost % 2) else -420, 0)
                time.sleep(0.3)
            else:
                log('      · 门彻底丢了（%d 帧）→ 放弃伺服' % lost)
                return False
            continue

        lost = 0
        off = d['cx'] - 1920
        w = d['w']
        last_off = off

        # ★★ 2026-11-09：**宽度上限**。实测门宽 1116 时那不是门而是整面墙，
        #   而且偏移在 -1070~+116 之间乱跳（每帧锁到不同的大色块）✗
        #   规则：宽度 > 760 ⇒ 要么已经在门口（先按 F 试），要么是墙（忽略）。
        if w > 620:
            log('      · 检测到超宽色块（宽%d，偏%+d）⇒ 可能是墙，先试一次 F' % (w, off))
            if press_f_checked():
                time.sleep(1.2)
                if on_station_picker() or area_passed():
                    return True
            # 按 F 没反应 ⇒ 当这帧没有门，走一小步换视角
            pyautogui.keyDown('w')
            time.sleep(0.4)
            pyautogui.keyUp('w')
            time.sleep(0.1)
            continue

        # ── 判断"被挡住"：已经对准了但门宽连续不涨 ──
        if abs(off) <= tol:
            if last_w and w <= last_w * 1.03:
                no_grow += 1
            else:
                no_grow = 0
            last_w = w
            if no_grow >= 4:
                side = 'a' if (no_grow // 4) % 2 == 1 else 'd'
                log('      · 门宽连续 %d 步不涨（%d）⇒ 被挡住 → 侧移 %s 绕开'
                    % (no_grow, w, side.upper()))
                pyautogui.keyDown(side)
                time.sleep(0.65)
                pyautogui.keyUp(side)
                time.sleep(0.12)
                no_grow = 0
                last_w = 0
                continue
        else:
            no_grow = 0
            last_w = 0

        # ── 不在正中心 ⇒ **只转，不走**（作者要求）──
        if abs(off) > tol:
            # ★★ 2026-11-15 震荡兜底（效率优先）：偏航**反复变号且不收敛**时，
            #   再转下去只会把 90 秒预算烧光（实测 ±200~400 来回 8 次 ✗）。
            #   做法：前 3 步正常转；之后**边走边微调**（DU 的门基本都在正前方，走过去更划算）。
            if (off > 0) != (last_off > 0) and abs(off) > tol:
                flips += 1
            else:
                flips = 0
            # 用上一轮的观测反推真实增益（off 减少多少 / 转了多少鼠标像素）
            if prev_off is not None and prev_mx:
                try:
                    g_obs = (prev_off - off) / float(prev_mx)
                    if 0.10 < abs(g_obs) < 8.0:
                        g_eff = 0.6 * g_eff + 0.4 * abs(g_obs)
                        log('      · 增益校准：本次实测 %.2f → 采用 %.2f' % (abs(g_obs), g_eff))
                except Exception:
                    pass
            _g = g_eff
            # ★ 同上：m = off / g（不是 -off/g）
            # ★ 2026-11-10：上限 300 → 520。实测偏 1662 时每步只转 300，
            #   要 6~8 步才收敛、把 75 秒预算吃光 ⇒ 加大单步幅度。
            # ★ 2026-11-15：震荡时把单步幅度按 1/2^k 衰减，避免继续过冲
            amp = 520.0 * (0.5 ** min(3, max(0, flips - 1)))
            mx = int(max(-amp, min(amp, off / _g * 0.7)))
            if mx == 0:
                mx = 1 if off < 0 else -1
            prev_off, prev_mx = off, mx
            if flips >= 3:
                log('      · 第%d步 偏%+d 震荡%d次 → 边走边微调（转%+d + 前进 1.2s）'
                    % (steps, off, flips, mx))
                _send_input_move(mx, 0)
                time.sleep(0.15)
                pyautogui.keyDown('w')
                time.sleep(1.2)
                pyautogui.keyUp('w')
                time.sleep(0.12)
                continue
            log('      · 第%d步 偏%+d（>±%d，不在正中心）→ 只转%+d' % (steps, off, tol, mx))
            _send_input_move(mx, 0)
            time.sleep(0.2)
            continue
        log('      · 第%d步 ✓ 已在正中心（偏%+d）→ 允许前进' % (steps, off))

        # ── 已对准 → 走一小步。很近就直接按 F ──
        if w >= 300:
            log('      · 门很近（宽%d）→ 走一小步 + 按 F' % w)
            pyautogui.keyDown('w')
            time.sleep(0.45)
            pyautogui.keyUp('w')
            time.sleep(0.12)
            if press_f_checked():
                time.sleep(1.2)
                if on_station_picker() or area_passed():
                    log('      · ★ 按 F 进门')
                    return True
            continue

        log('      · 第%d步 已对准（宽%d）→ 前进 %.2fs' % (steps, w, step_sec))
        pyautogui.keyDown('w')
        time.sleep(step_sec)
        pyautogui.keyUp('w')
        time.sleep(0.1)

    log('      · 伺服超时（%.0f 秒）' % max_seconds)
    return on_station_picker() or area_passed()


def walk_deadreckon_to_door(off, width, max_seconds=25):
    """**航位推算走向门**：转向 → **按住 W 连续走** → 用一次 D 绕开障碍 → 按 F。

    ★★★ 2026-11-09 作者亲自纠正（录像 + 口头），这里记录三条**事实**：
      1. **一次键要长按**：按住 W 连续走比"0.8 秒一顿"快得多；此前 0.8s+停 0.25s
         的碎步走法既慢又容易走偏 ✗
      2. **距离并不远**：出生点→门正常走法 **不到 5 秒**；
         录像里走了 90 秒是因为作者**故意慢慢演示** ⇒ 不要按 90 秒设计。
      3. **绕障只需一次 D**：直线上挡着装置/栏杆，往 D 侧打一下（约 0.5 秒）再继续 W 就过了。

    `off` = 命中时的像素偏移（+右 -左），`width` = 门宽（估计距离）。
    """
    # ① 按实测增益转向（Δoffset ≈ -1.2 × Δmouse，符号取反）
    mx = int(max(-700, min(700, off / 1.2)))
    if mx:
        log('      · 航位推算：转 %+d（目标偏%+d）' % (mx, off))
        _send_input_move(mx, 0)
        time.sleep(0.35)

    # ② 先按住 W 走 1.2 秒
    log('      · 按住 W 直走 1.2 秒')
    pyautogui.keyDown('w')
    time.sleep(1.2)
    pyautogui.keyUp('w')
    time.sleep(0.15)
    if on_station_picker() or area_passed():
        log('      · ★ 直接就到了')
        return True
    if press_f_checked():
        if on_station_picker() or area_passed():
            log('      · ★ 按 F 后进门')
            return True

    # ③ 侧打一下 D（绕开中间挡着的装置/栏杆，约 0.5 秒）
    log('      · 侧打 D 0.5 秒绕开中间障碍')
    pyautogui.keyDown('d')
    time.sleep(0.5)
    pyautogui.keyUp('d')
    time.sleep(0.1)

    # ④ 再按住 W 连续走（每次 1.5 秒，基本不停）
    t0 = time.time()
    seg = 0
    while time.time() - t0 < max_seconds:
        seg += 1
        log('      · 第%d段：按住 W 直走 1.5 秒' % seg)
        pyautogui.keyDown('w')
        time.sleep(1.5)
        pyautogui.keyUp('w')
        time.sleep(0.12)
        if on_station_picker() or area_passed():
            log('      · ★ 到位（出现选站点卡/过关）')
            return True
        if press_f_checked():
            if on_station_picker() or area_passed():
                log('      · ★ 按 F 后进门')
                return True
        if is_event_ui():
            log('      · 遇到事件界面 → 交给事件处理')
            do_event_dialog()
            return True
        # 走了 3 段还没到 ⇒ 再侧打一次（左右交替）
        if seg in (3, 6):
            _sd = 'd' if seg == 3 else 'a'
            log('      · 第%d段后仍没到 → 侧打 %s 0.45 秒' % (seg, _sd.upper()))
            pyautogui.keyDown(_sd)
            time.sleep(0.45)
            pyautogui.keyUp(_sd)
            time.sleep(0.1)
    return on_station_picker() or area_passed()


def walk_into_door(d, max_seconds=40):
    """门已睁眼 ⇒ **直接往里走**（不按 F、不转向、不点地面）。"""
    import random as _r
    t0 = time.time()
    log('      · 门已睁眼 → 直接走进去')
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            wait_battle(timeout=420)
            return True
        pyautogui.keyDown('w')
        time.sleep(0.9)
        pyautogui.keyUp('w')
        time.sleep(0.25)
        # ★ 按 F 是**唯一可靠判据**（实测：不判门状态、走过去按 F → 1/13 过到 2/13 ✓）
        if on_station_picker() or area_passed():
            return True
        press_f_checked()
        if on_station_picker() or area_passed():
            log('      · 按 F 后出现选站点卡 ✓')
            return True
        dd = find_door_pink()
        if dd is None:
            # 门消失通常意味着已经穿过去了；再等等看有没有选站点卡
            time.sleep(0.8)
            if on_station_picker() or area_passed():
                return True
            if dd is None and time.time() - t0 > 6:
                log('      · 门已在视野里消失，继续前进一小段')
        # 只做小幅修正，绝不转身找门
        elif abs(dd['cx'] - 1920) > 600:
            mx = int(max(-100.0, min(100.0, (dd['cx'] - 1920) / TURN_K * TURN_DAMP)))
            if mx:
                _send_input_move(mx, 0)
                time.sleep(0.25)
    return on_station_picker()


def walk_door_with_technique(max_seconds=110):
    """战斗区打法（**作者 2026-11-09 指定**）：

        1. 先识别门（粉色门板 + 白眼睛），把朝向对准门；
        2. 按 E 放秘技（秘技圈跟着角色）；
        3. **朝门的方向一直走**，沿路秘技圈会罩住敌人；
        4. 进战斗 → 打完 → **视角自动面对敌人，而敌人和门同方向** ⇒ 继续同方向走；
        5. 只要不走过头，就不需要重新找门（不左键点地面、不 360° 转）。

    返回 True = 已出现「选择下一站」。
    """
    import random
    # ① 先找门并对朝向
    d = find_door_pink()
    if d is None:
        try:
            d = find_door_by_tpl(min_score=0.55)
        except Exception:
            d = None
    if d is not None:
        off = d['cx'] - 1920
        if abs(off) > 120:
            mx = int(max(-300.0, min(300.0, off / TURN_K * TURN_DAMP)))
            if mx:
                log('      · 先对门：偏%+d → 转%+d' % (off, mx))
                _send_input_move(mx, 0)
                time.sleep(0.4)
    else:
        log('      · 没看到门（战斗区常见）→ 直接开秘技往前走')

    # ② 开秘技
    log('      · 放秘技（E）')
    use_technique()

    t0 = time.time()
    step = 0
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            log('      · ★ 进战斗 → 打完继续同方向走（视角会自动面对敌人=门的方向）')
            wait_battle(timeout=420)
            time.sleep(1.0)
            use_technique()
            # 战后**不转视角**，继续往前（按作者的说法方向是一致的）
            continue
        step += 1
        pyautogui.keyDown('w')
        time.sleep(random.uniform(0.85, 1.05))
        pyautogui.keyUp('w')
        time.sleep(0.25)
        # ★★ 2026-11-09（作者指出「到门口为什么不开门」）：**必须按 F**。
        #   DU 的随意门要 F 交互，光走过去不触发；之前这版只走不按 F ⇒ 到了门边也不开 ✗
        if on_station_picker():
            return True
        press_f_checked()
        if on_station_picker() or area_passed():
            log('      · ★ 按 F 后出现选站点卡')
            return True
        # ★ 作者硬规则：**秘技只放一次**，这里不再补按任何 E。
        #   没打到怪就交给上层报告（NEED_AUTHOR），不要偷偷多放秘技。
        # 只在"门还在视野里且明显偏"时才微调，且只小角度
        if step % 3 == 0:
            dd = find_door_pink()
            if dd is not None:
                o = dd['cx'] - 1920
                if abs(o) > 420:
                    mx = int(max(-160.0, min(160.0, o / TURN_K * TURN_DAMP)))
                    if mx:
                        log('      · 微调朝向 %+d（偏%+d 宽%d）' % (mx, o, dd['w']))
                        _send_input_move(mx, 0)
                        time.sleep(0.3)
    log('      · 秘技+直走 %.0fs 没开出站点卡' % max_seconds)
    return False


# ★ 2026-11-15：世界里的**敌人名牌** ——「等级88 破晓战队·赤焰」
#   实测（4/13 首领房）：首领就站在角色旁边、名牌一直显示，但 **YOLO 认不出来**，
#   于是脚本"没看到敌人"转了 11 次、白耗 3 分钟 ✗。名牌是 OCR 稳拿的，x 坐标直接当瞄准目标。
ENEMY_LABEL_RE = re.compile(r'^等级\s*\d+')


def enemy_card_present(img=None):
    """★ 2026-11-15 作者原话：「**正上方出现了怪物卡片信息，就直接原地开秘技吧**」

    屏幕上只要出现「等级NN 怪物名」（包括**正上方那条固定横幅**），
    就说明**区域里有怪在场** ⇒ 立刻原地放秘技，不用先找到它的身体。
    返回 (bool, 卡片文字)。
    """
    try:
        for t, sc, cx, cy in ocr_items(img):
            if sc >= 0.45 and ENEMY_LABEL_RE.match(t.strip()):
                return True, t.strip()[:24]
    except Exception:
        return False, ''
    return False, ''


def find_enemy_label(img=None):
    """用敌人名牌 OCR 定位敌人。返回 {label, conf, cx, cy} 或 None。"""
    try:
        for t, s_, cx, cy in ocr_items(img):
            # ★ 2026-11-15 坑：首领房间顶部有一条**固定横幅**「等级88 破晓战队·赤焰」
            #   （cy≈119、永远居中 1920）——它不是敌人名牌，拿它瞄准会一直"对准"却撞不到 ✗
            #   真正的浮空名牌在画面中部（cy 大致 200~1400），所以这里卡 cy 下限。
            if s_ >= 0.45 and cy > 200 and ENEMY_LABEL_RE.match(t.strip()):
                return {'label': t.strip()[:20], 'conf': s_, 'cx': cx, 'cy': cy}
    except Exception:
        return None
    return None


def hunt_enemies(max_seconds=100):
    """战斗/精英/首领区：**先看门，看不到门 ⇒ 首领挡着 ⇒ 开秘技打它**。

    ★★★ 2026-11-09 作者原话（这就是本题的正解）：
        「这个是首领房间，你如果**没识别到门，说明有首领挡着，应该开秘技打它**。」

    正确顺序：
      1. 先找门（模板/颜色）—— 能看到门就直接走过去；
      2. **看不到门 ⇒ 一定有敌人挡在中间**：
         a. 用 YOLO 找敌人（CATE_ENEMY）；找到就**对准 + 撞上去**（charge_enemy）；
         b. 没找到 ⇒ **原地小步转**（约 10°/次）扫一圈找敌人，
            ⚠ 绝不能像以前那样随机 ±900 大转 —— 那会越走越偏（实测走 120 秒都没碰到怪 ✗）
      3. 进战斗就交给 wait_battle（自动战斗 + 选祝福）；
      4. 打完再看门 → 出现选站点卡就返回 True。
    """
    t0 = time.time()
    log('      · 战斗区：先找门；找不到门 ⇒ 开秘技打挡路的敌人')

    # ---- ① 先看门 ----
    d = None
    try:
        d = find_door_by_tpl(min_score=0.55)
    except Exception:
        d = None
    if d is None:
        d = find_door_pink()
    if d is not None:
        off = d['cx'] - 1920
        if abs(off) > 140:
            mx = int(max(-300.0, min(300.0, off / TURN_K * TURN_DAMP)))
            if mx:
                log('      · 看到门：偏%+d → 先转%+d' % (off, mx))
                _send_input_move(mx, 0)
                time.sleep(0.35)
        log('      · 门可见 → 放秘技后朝门直走')
        use_technique()
        pyautogui.keyDown('w')
        time.sleep(1.6)
        pyautogui.keyUp('w')
        time.sleep(0.15)
        if on_station_picker() or area_passed():
            return True

    # ---- ② 看不到门 ⇒ 打挡路的敌人 ----
    if d is None:
        log('      · 没看到门 ⇒ 首领挡着 ⇒ **放秘技(E) + 找它打**')
        use_technique()

    scan = 0
    while time.time() - t0 < max_seconds:
        check_pause('战斗区')
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            log('      · ★ 进战斗')
            wait_battle(timeout=420)
            txt = '|'.join(_texts(ocr_items()))
            if '选择祝福' in txt or '选择一项祝福' in txt:
                pick_bless_inline()
            # 打完之后门通常就开了
            d2 = None
            try:
                d2 = find_door_by_tpl(min_score=0.55)
            except Exception:
                d2 = None
            if d2 is None:
                d2 = find_door_pink()
            if d2 is not None:
                off = d2['cx'] - 1920
                if abs(off) > 140:
                    mx = int(max(-300.0, min(300.0, off / TURN_K * TURN_DAMP)))
                    if mx:
                        _send_input_move(mx, 0)
                        time.sleep(0.3)
                log('      · 打完了，门出现了 → 直走')
                pyautogui.keyDown('w')
                time.sleep(1.8)
                pyautogui.keyUp('w')
                time.sleep(0.15)
            continue

        # ★ 2026-11-15：**先看名牌**（OCR 稳），再退回 YOLO
        lab = find_enemy_label()
        if lab is not None:
            off = lab['cx'] - 1920
            log('      · 名牌锁定敌人「%s」偏%+d → 对准 + 撞上去' % (lab['label'], off))
            if abs(off) > 80:
                mx = int(max(-520.0, min(520.0, off / TURN_K * TURN_DAMP)))
                if mx:
                    _send_input_move(mx, 0)
                    time.sleep(0.35)
            pyautogui.keyDown('w')
            time.sleep(1.5)
            pyautogui.keyUp('w')
            time.sleep(0.2)
            scan += 1
            if scan % 3 == 0:
                use_technique()
            continue

        # 找敌人
        enemy = None
        try:
            enemy = yolo_pick(CATE_ENEMY, min_conf=0.40, min_w=50)
        except Exception:
            enemy = None
        if enemy is not None:
            log('      · 找到敌人 %s %.2f 偏%+d → 对准撞上去'
                % (enemy.get('label', '?'), enemy['conf'], enemy['cx'] - 1920))
            if charge_enemy(enemy, max_seconds=14.0):
                continue
            scan += 1
            continue

        # ★★ 2026-11-15 作者：「**为什么不全景**，视野里啥都没有还慢慢转」
        #   ⇒ 连续两三次没看到，就直接**全景快扫一圈**（pano_once），不再 10° 一步磨
        scan += 1
        if scan % 3 == 1:
            log('      · 没看到敌人（第%d次）→ **全景快扫一圈**找它' % scan)
            try:
                pano_once('战斗区找怪')
            except Exception as _e:
                log('      · 全景出错: %s' % _e)
            continue
        log('      · 没看到敌人（第%d次）→ 小步转 +600' % scan)
        _send_input_move(600, 0)
        time.sleep(0.3)
        if scan % 3 == 0:
            # ★ 2026-11-15 效率优先：首领房很小，**按住 W 走 3.5 秒**去撞更有效
            #   （原来每 4 次才走 1 秒，实测 3 分钟都撞不到怪 ✗）
            pyautogui.keyDown('w')
            time.sleep(3.5)
            pyautogui.keyUp('w')
            time.sleep(0.2)
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
        if find_door_by_tpl(min_score=0.55) is not None:
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
        press_f_checked()
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
        press_f_checked()
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


PANO_LIMIT_PER_AREA = 3        # ★ 同一区域全景次数上限（作者 2026-11-09：超过 n 次就结束）
_PANO_COUNT = {}               # {(区域类型, HUD计数): 已用次数}
_PANO_LIMIT_HIT = [False]      # 打到上限 → 整局结束


def _area_key():
    try:
        import re as _re
        hud = ''.join(t.strip() for t, s, _, _ in ocr_items() if s >= 0.4)
        mm = _re.search(r'\d+\s*/\s*\d+', hud)
        return (area_kind(), mm.group(0) if mm else '?')
    except Exception:
        return (area_kind(), '?')


def pano_once(tag=''):
    """全景扫描（带区域计数）。

    * 初始扫描用一次、丢失目标用一次，**都算**；
    * 同一区域超过 PANO_LIMIT_PER_AREA 次 ⇒ 置 _PANO_LIMIT_HIT，整局结束。
    返回 True = 全景成功；False = 没成功（但还能继续）；None = 已到上限。
    """
    key = _area_key()
    used = _PANO_COUNT.get(key, 0)
    if used >= PANO_LIMIT_PER_AREA:
        _PANO_LIMIT_HIT[0] = True
        log('      !!! 本区域(%s)全景已用 %d 次（上限 %d）→ **结束本局**'
            % (key[0] or '?', used, PANO_LIMIT_PER_AREA))
        return None
    _PANO_COUNT[key] = used + 1
    log('      · 全景第 %d/%d 次（区域 %s，%s）'
        % (used + 1, PANO_LIMIT_PER_AREA, key[0] or '?', tag))
    try:
        return bool(goto_by_panorama())
    except Exception as e:
        log('      · 全景异常: %s' % e)
        return False


VLM_MODEL = 'qwen3-vl:8b'
VLM_URL = 'http://127.0.0.1:11434/api/generate'
VLM_WIDTH = 800            # 图像缩到 800 宽（实测精度够、速度最快）
VLM_TIMEOUT = 90


def vlm_ask(prompt, width=VLM_WIDTH, num_predict=300, timeout=VLM_TIMEOUT):
    """问本地视觉模型（qwen3-vl:8b）——**决策层**用，不进伺服循环。

    实测要点（2026-11-09）：
      * 提示词必须带 `/no_think`：否则先想几百 token，单帧 27 秒；
        加了之后 **2.3 秒** ✓
      * `think:false` 参数无效（这版 ollama 返回空）
      * 图像缩到 800 宽即可
      * **坐标别信它**：实测把中央的粉门说成右侧；
        它擅长"这是什么/能交互吗/该干什么"，不是像素定位

    返回回答文本；任何失败返回 None（不抛异常）。
    """
    import base64
    import json
    import urllib.request
    try:
        import cv2
        img = screenshot()
        h = int(img.shape[0] * width / float(img.shape[1]))
        small = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode('.jpg', small, [int(cv2.IMWRITE_JPEG_QUALITY), 86])
        if not ok:
            return None
        b64 = base64.b64encode(buf.tobytes()).decode()
        body = json.dumps({
            'model': VLM_MODEL,
            'prompt': '/no_think ' + prompt,
            'images': [b64],
            'stream': False,
            'options': {'temperature': 0.1, 'num_predict': num_predict},
        }).encode('utf-8')
        req = urllib.request.Request(VLM_URL, data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        # ★★ 2026-11-15 修：本机 ollama 返回体是 **GBK**，按 utf-8 解会解析失败
        #   ⇒ 全天「门状态: 判不出」就是它 ✗  先 utf-8，失败退回 gbk。
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('gbk', 'replace')
        data = json.loads(text)
        ans = (data.get('response') or '').strip()
        return ans or None
    except Exception as e:
        log('      · VLM 调用失败（不影响主流程）: %s' % e)
        return None


# ★ 抽奖机黑名单：某区域次数用完后，不再打开它（作者 2026-11-09 要求）
_ROULETTE_DONE = {}

# ★ 2026-11-09 作者第二张图：一个区域里可能有 **多个可交互物（B/D/F）**，
#   必须**逐个交互完**门才会开 ⇒ 需要 OCR/VLM 来「定位」它们在左/中/右，
#   然后一个个走过去交互。大体方向都在出生的正前方。
# ★ F 交互的**白名单**：门类 + 可交互物 + 常见通用动词。
#   ⚠ 2026-11-09 血泪：之前白名单只有门类词，结果「造物调试台」弹提示时被自己拒绝，
#     装置就在眼前却永远不按 F ✗（作者规则的本意是"别乱按到抽奖机"，不是"别交互"）
F_ALLOW_WORDS = (
    # 门 / 出口
    '随意门', '门', '开启', '进入', '启动', '继续', '前往',
    # 通用交互
    '调查', '交互', '对话', '交谈', '查看', '拾取', '领取', '确认', '开启',
    # 可交互物（同 INTERACT_LABELS）
    '造物调试台', '调试台', '维护市容', '造物', '奇物', '事件',
    '宝藏', '宝箱', '商店', '复活', '委托', '终端', '机关', '开关', '祭坛',
)

INTERACT_LABELS = (
    '造物调试台', '维护市容', '造物', '奇物', '事件', '宝藏', '宝箱',
    '商店', '复活', '委托', '调查', '终端', '机关', '开关', '祭坛', '调试台',
)

# ★★ 2026-11-15 作者要求：「按 F 前识别到复活装置就不要点，省得点进去再退出」
#   优先级**高于** F_ALLOW_WORDS（旧白名单里居然有 '复活' ⇒ 一定会点进去 ✗）
F_DENY_WORDS = (
    '复活装置', '复活台', '复活点', '复活', '角色重置', '重置角色',
    '抽奖', '奇物大转盘',
)


def press_f_checked(targets=None, settle=0.7):
    """**按 F 之前先 OCR 交互提示，确认是我们要的目标**（作者 2026-11-09 要求）。

    血泪：盲按 F 会把「造物调试台 / 抽奖机」打开（次数 0 时还会卡死在那儿）。
    规则：
      * 没有提示 ⇒ **不按**；
      * 有提示但文字不在 targets 里 ⇒ **不按**，记一行日志；
      * 命中 ⇒ 按 F。
    返回 True = 真的按了。
    """
    if targets is None:
        targets = F_ALLOW_WORDS
    try:
        pr = find_prompts()
    except Exception:
        pr = []
    if not pr:
        return False
    word = pr[0][0]
    # ★ 2026-11-15：黑名单优先 —— 复活装置/抽奖机一律不按（作者要求，省得点进去再退出）
    if any(k in word for k in F_DENY_WORDS):
        log('      · F 提示是「%s」→ **黑名单**（复活装置等），不按 F' % word)
        return False
    if not any(k in word for k in targets):
        log('      · F 提示是「%s」，不在白名单 → 不按 F（防误触抽奖机等）' % word)
        return False
    press('f', settle)
    return True


def roulette_area_key():
    try:
        return (area_kind(), hud_progress())
    except Exception:
        return ('?', None)


def find_label(keyword, min_score=0.45):
    """在屏幕上找**世界标签**（物体上方的文字，如「造物调试台」「维护市容」）。

    返回 (x, y, text) 或 None。标签的 x 就是物体的方向 ✓
    """
    try:
        for t, s, cx, cy in ocr_items():
            if s >= min_score and keyword in t:
                return int(cx), int(cy), t.strip()
    except Exception:
        pass
    return None


def walk_to_label(keyword, max_seconds=45, tol=150):
    """**走到屏幕上某个世界标签并交互**（按 F）。

    作者 2026-11-09 指出的通用办法：世界标签的位置跟随物体 ⇒ 标签的 x 就是方向。
    用法：铸造区的「造物调试台」、事件板、复活装置等。
    返回 True = 已经交互（按了 F 且有界面变化）；False = 没找到/没成功。
    """
    t0 = time.time()
    hit_any = False
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            return True
        lab = find_label(keyword)
        if lab is None:
            if hit_any:
                return False
            # 没看到标签：原地转一点点再找（不要大范围乱转）
            _send_input_move(500, 0)
            time.sleep(0.35)
            continue
        hit_any = True
        x, y, txt = lab
        off = x - 1920
        log('      · 看到标签「%s」偏%+d' % (txt, off))
        if abs(off) > tol:
            mx = int(max(-260.0, min(260.0, off / TURN_K * TURN_DAMP)))
            if mx == 0:
                mx = 1 if off > 0 else -1
            _send_input_move(mx, 0)
            time.sleep(0.35)
            continue
        # 已对正 → 走过去 + 按 F
        pyautogui.keyDown('w')
        time.sleep(0.8)
        pyautogui.keyUp('w')
        time.sleep(0.3)
        press_f_checked()
        time.sleep(0.8)
        # 交互成功通常会出现界面变化（弹窗/面板）
        st = detect_state(verbose=False)[0]
        if st in ('device_curio', 'event_dialog', 'popup_blank', 'bless_choose'):
            log('      · 交互后进入界面: %s' % st)
            return True
    return False


def vlm_locate_door(max_tries=6):
    """**让 VLM 指方向**：它说不出像素坐标，但能可靠说出"有没有门、在左/中/右"。

    返回 'left' / 'center' / 'right' / None（没有门或判断失败）。
    """
    ans = vlm_ask('这是《崩坏：星穹铁道》差分宇宙的区域画面。'
                  '画面里有没有一扇**粉色的门**（门框是黄/金色的）？'
                  '只回答：没有 / 左边 / 中间 / 右边。')
    if not ans:
        return None
    a = ans.replace(' ', '')
    if '没有' in a or '看不到' in a or '不存在' in a:
        return None
    # 先判左右，再判中间（避免"中间偏左"这类混合说法）
    if '左边' in a or '左侧' in a or '左' in a:
        return 'left'
    if '右边' in a or '右侧' in a or '右' in a:
        return 'right'
    if '中间' in a or '中央' in a or '正中' in a:
        return 'center'
    return None


def walk_by_vlm(max_seconds=100):
    """**用 VLM 指方向来走到门口**（作者建议的分层：VLM 做决策，cv2 做动作）。

    每次问一句（2~4 秒）：
      * 没有门 → 原地转 ~90° 再看
      * 左边/右边 → 转过去
      * 中间 → 往前走 + 按 F
    出现选站点卡即成功。
    """
    t0 = time.time()
    turn_cycle = 0
    while time.time() - t0 < max_seconds:
        if on_station_picker() or area_passed():
            return True
        if in_battle():
            wait_battle(timeout=420)
            return True
        where = vlm_locate_door()
        if where is None:
            turn_cycle += 1
            log('      · VLM: 这个方向没看到门 → 转 90° 再看（第%d次）' % turn_cycle)
            if turn_cycle > 5:
                log('      · VLM 转满一圈都没门 → 放弃')
                return False
            _send_input_move(2900, 0)
            time.sleep(0.6)
            continue
        turn_cycle = 0
        if where == 'left':
            log('      · VLM: 门在左边 → 左转')
            _send_input_move(-900, 0)
            time.sleep(0.4)
            continue
        if where == 'right':
            log('      · VLM: 门在右边 → 右转')
            _send_input_move(900, 0)
            time.sleep(0.4)
            continue
        log('      · VLM: 门在中间 → 往前走 + 按 F')
        pyautogui.keyDown('w')
        time.sleep(0.9)
        pyautogui.keyUp('w')
        time.sleep(0.25)
        press_f_checked()
        if on_station_picker() or area_passed():
            return True
    return False


def scan_in_place(max_steps=40, step_mouse=620):
    # ★★ 2026-11-09 关键修复：原来 14 步 × 600 = 8400 鼠标 ≈ **137°**，根本不是"一圈"！
    #   而**门常常就在出生点正后方**（重置后截图里门就在角色背后）
    #   ⇒ 只转 137° 永远看不到它 ✗
    #   现在 40 × 620 = 24800 鼠标 ≈ 400°（一屏≈3800 鼠标≈62°），真正覆盖整圈 ✓
    """**原地转一圈扫描**（作者 2026-11-09 要求）：

        「进入区域的第一次扫描如果没扫到东西就不要动，免得乱动浪费时间，
          毕竟初始位置已经是最方便识别的了。」

    做法：站在出生点（不动位置），每次只**转视角**一档，每档检测
    ① 粉门/门模板 ② 顶部敌人标识 ③ F 提示（随意门/调查/开启…）。
    返回 {'kind':'door'|'enemy'|'prompt', ...} 或 None（一圈都没有）。
    """
    log('      · 原地转一圈扫描（不动位置）')
    for i in range(max_steps):
        try:
            d = find_door_pink()
            if d is None:
                d = find_door_by_tpl(min_score=0.55)
            # ★★ 只认够大的门：远处 60~90px 的粉色小物件极可能是假门
            #   （实测：偏-1820 宽200 的"门"，走近后只有 72px，根本不是门 ✗）
            if d is not None and d['w'] >= 130:
                log('        ✓ 第%d档扫到门（偏%+d 宽%d）' % (i, d['cx'] - 1920, d['w']))
                return {'kind': 'door', 'data': d}
            ind = enemy_indicator()
            if ind is not None:
                log('        ✓ 第%d档扫到敌人标识: %s' % (i, ind))
                return {'kind': 'enemy', 'label': ind}
            pr = find_prompts()
            if pr:
                log('        ✓ 第%d档扫到提示「%s」' % (i, pr[0][0]))
                return {'kind': 'prompt', 'data': pr[0]}
        except Exception as e:
            log('        ! 扫描异常: %s' % e)
        _send_input_move(step_mouse, 0)
        time.sleep(0.2)
    log('      · 原地一圈没有扫到任何目标 → **改用全景扫描**（作者 2026-11-09：丢了目标就用全景，小幅度扫描没用）')
    return None


_NO_PROGRESS_HITS = [0]


def _unknown_panel_guard(state):
    """★ 2026-11-15 安全网：state 说在区域里却读不到 (n/13) 进度号
    ⇒ 多半有个**没被识别的全屏面板**盖着（这次就是造物调试台）。
    连续 2 次 → 按一下 ESC 脱出，别对着面板死转视角。"""
    try:
        if state not in ('in_area', None):
            _NO_PROGRESS_HITS[0] = 0
            return False
        if hud_progress() is not None:
            _NO_PROGRESS_HITS[0] = 0
            return False
        _NO_PROGRESS_HITS[0] += 1
        if _NO_PROGRESS_HITS[0] >= 2:
            log('      ⚠ state=%r 但读不到进度号（疑似未知面板）→ 按 ESC 脱出' % (state,))
            press('esc', 1.0)
            time.sleep(1.0)
            _NO_PROGRESS_HITS[0] = 0
            return True
    except Exception:
        pass
    return False


def hud_progress():
    """读左上角 HUD 的 **(n/13)** 进度号（作者 2026-11-09 建议：这个最稳健）。

    返回 (n, m) 或 None。判"有没有推进"就看这个数字变没变，
    比"有没有出现选站点卡"可靠得多（后者会被误判成 True）。
    """
    try:
        import cv2
        import re as _re
        img = screenshot()
        crop = cv2.resize(img[15:145, 110:1150], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        txt = ''.join(t.strip() for t, s, _, _ in ocr_items(crop) if s >= 0.4)
        mm = _re.search(r'(\d+)\s*/\s*(\d+)', txt)
        return (int(mm.group(1)), int(mm.group(2))) if mm else None
    except Exception:
        return None


def progress_changed(before):
    """HUD 进度号是否相对 before 变了（变了 = 真的推进了）。"""
    now = hud_progress()
    if before is None or now is None:
        return None
    return now != before


def walk_lane_to_door(max_seconds=240, step=1.2):
    """**一条直巷式推进**（★★ 作者 2026-11-09 画的俯瞰图，这就是本题的正确模型）：

            C  = 门（区域出口）
            |
            B  = 怪物 / 可交互物（造物调试台、事件板…）
            |
            A  = 我们的角色（出生点）

        作者原话：「其实你大方向往前走就可以了，中途要么打下怪物，
                  要么交互，要么交互完绕过障碍。」

    ⇒ 完全不需要 360° 扫描 / 全景 / VLM 指路。只需要：
       1. **按住 W 往前走**；
       2. 途中 **遇到怪 → 打它**（秘技 + 撞上去，打完继续）；
       3. 途中 **遇到可交互 → OCR 看提示 → 按 F 交互**，交互完继续；
       4. **撞墙/被挡 → 侧移（A/D）绕过去**，然后继续往前；
       5. 门近了（模板命中且够大）→ 对准 → 走过去按 F。
    返回 True = 出现选站点卡 / 区域已过。
    """
    t0 = time.time()
    stuck = 0
    stepn = 0

    def _hold(keys, secs):
        for k in keys.split():
            pyautogui.keyDown(k)
        time.sleep(secs)
        for k in keys.split():
            pyautogui.keyUp(k)
        time.sleep(0.12)

    def _frame_sig():
        import cv2 as _cv
        g = _cv.cvtColor(screenshot(), _cv.COLOR_BGR2GRAY)
        return _cv.resize(g, (64, 36), interpolation=_cv.INTER_AREA).astype('float32')

    log('      · 直巷推进开始（A→B→C，按住 W 一直向前）')
    _done_labels = {}          # 标签文字 -> 时间戳（交互过就 30 秒内不再理它）
    while time.time() - t0 < max_seconds:
        check_pause('直巷推进')
        stepn += 1

        # ── 到站了？ ──
        if on_station_picker() or area_passed():
            log('      · ★ 到站（第%d步，%.0f 秒）' % (stepn, time.time() - t0))
            return True

        # ── 战斗中：等打完 ──
        if in_battle():
            log('      · 遇怪 → 等战斗结束')
            wait_battle(timeout=420)
            _txt = '|'.join(_texts(ocr_items()))
            if '选择祝福' in _txt or '选择一项祝福' in _txt:
                pick_bless_inline()
            continue

        # ── 事件界面：交互完继续 ──
        _st = detect_state(verbose=False)[0]
        if _st == 'event_dialog':
            log('      · 遇事件 → 交互（A→B 的 B）')
            do_event_dialog()
            continue
        if _st == 'roulette':
            log('      · 遇抽奖机 → ESC 关掉继续走')
            press('esc', 1.2)
            time.sleep(0.8)
            continue
        if _st == 'popup_blank':
            click_phys(1920, 1860, settle=1.0, guard_world=True)
            time.sleep(0.8)
            continue
        if _st in ('bless_choose', 'pick_miracle', 'device_curio'):
            log('      · 面板(%s) → 点确定关掉' % _st)
            if not click_text('确', tries=3, interval=0.8):
                click_phys(3252, 1876, settle=1.0, guard_world=True)
            time.sleep(1.0)
            continue

        # ── 有交互提示 → OCR 校验后按 F ──
        try:
            _pr = find_prompts()
        except Exception:
            _pr = []
        if _pr:
            log('      · 看到提示「%s」→ 交互' % _pr[0][0])
            if press_f_checked():
                _done_labels[_pr[0][0].strip()] = time.time()   # ★ 记下来，30 秒内不再理
                time.sleep(1.3)
                continue

        # ── 中间的可交互物（B/D/F）：先定位，再走过去交互 ──
        _lab = None
        try:
            for _kw in INTERACT_LABELS:
                _l = find_label(_kw)
                if _l is not None:
                    _lab = _l
                    break
        except Exception:
            _lab = None
        if _lab is not None:
            _lx, _ly, _ltxt = _lab
            _key = _ltxt.strip()
            if _key in _done_labels and time.time() - _done_labels[_key] < 30:
                _lab = None
            if _lab is not None:
                _loff = _lx - 1920
                if abs(_loff) > 160:
                    _mx2 = int(max(-260.0, min(260.0, _loff / 1.2)))
                    if _mx2:
                        log('      · 定位到可交互「%s」偏%+d → 转%+d' % (_key, _loff, _mx2))
                        _send_input_move(_mx2, 0)
                        time.sleep(0.3)
                        continue
                log('      · 可交互「%s」已对准 → 走过去' % _key)
                _hold('w', 1.0)
                continue

        # ── 门够近 → 对准走过去 ──
        _d = None
        try:
            _d = find_door_by_tpl(min_score=0.60)
        except Exception:
            _d = None
        if _d is not None and _d['w'] >= 180:
            _off = _d['cx'] - 1920
            if abs(_off) > 150:
                _mx = int(max(-260.0, min(260.0, _off / 1.2)))
                if _mx:
                    log('      · 门近了（偏%+d）→ 转%+d' % (_off, _mx))
                    _send_input_move(_mx, 0)
                    time.sleep(0.3)
                    continue
            log('      · 门正前方（宽%d）→ 交给闭环伺服' % _d['w'])
            if approach_door_servo(max_seconds=45):
                return True
            continue

        # ── 正常：往前走，检测有没有被挡住 ──
        _a = _frame_sig()
        _hold('w', step)
        _b = _frame_sig()
        try:
            import numpy as _np
            _diff = float(_np.abs(_a - _b).mean())
        except Exception:
            _diff = 99.0
        if _diff < 2.5:
            stuck += 1
            _side = 'a' if stuck <= 3 else 'd'
            log('      · 走不动（画面差 %.1f，第%d次）→ 侧移 %s 绕过障碍'
                % (_diff, stuck, _side.upper()))
            _hold(_side, 0.7)
            if stuck >= 6:
                log('      · 侧移 6 次仍被挡 → 再往前顶一次')
                _hold('w a', 1.0)
                stuck = 0
        else:
            stuck = 0
    log('      · 直巷推进超时（%.0f 秒）' % max_seconds)
    return on_station_picker() or area_passed()


class _DoorDetector(threading.Thread):
    """**常驻门检测线程**（2026-11-10 作者要求"多线程"）。

    为什么：检测一次约 0.9 秒（模板匹配占 75%），而决策只需要最新的结论。
    现在检测在独立线程里以 3~5 Hz 刷新，主线程读共享结果，
    一步决策从 ~1.2 秒降到 ~0.25 秒。

    约定：
      * **只有本线程高频截图**（screenshot 有全局锁，其它线程偶尔取一帧也不会冲突）；
      * 门宽必须在 [130, 620]，>620 是墙（实测 868/1156/1364/1632 全是墙面）；
      * 暂停文件存在时整线程睡眠，CPU 归零。
    """

    def __init__(self, interval=0.18, min_w=130, max_w=2000):
        super().__init__(name='door-detector', daemon=True)
        self.interval = interval
        self.min_w = min_w
        self.max_w = max_w
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._door = None
        self._ts = 0.0
        self._seq = 0
        self._ok = 0
        self._err = 0

    def _valid(self, d):
        return d is not None and self.min_w <= int(d.get('w', 0)) <= self.max_w

    def run(self):
        while not self._stop_evt.is_set():
            try:
                if os.path.exists(PAUSE_FILE):
                    time.sleep(0.4)
                    continue
                # ★ 2026-11-15：后台线程改用 **SIFT**（0.06 秒），
                #   不再跑模板匹配（1~2 秒）——它会把截图锁占满、把主线程拖死 ✗
                d = None
                try:
                    d = find_door_by_features()
                except Exception:
                    d = None
                if not self._valid(d):
                    t = None
                    try:
                        t = find_door_by_tpl(min_score=0.55, fast=True)
                    except Exception:
                        t = None
                    if self._valid(t):
                        d = t
                    elif self._valid(d) is False and not self._valid(t):
                        d = None
                if self._valid(d):
                    with self._lock:
                        self._door = d
                        self._ts = time.time()
                        self._seq += 1
                        self._ok += 1
                else:
                    with self._lock:
                        self._err += 1
            except Exception:
                with self._lock:
                    self._err += 1
            time.sleep(self.interval)

    def latest(self, max_age=1.5):
        """取最近一次检测结果（过期就返回 None，让调用方自己重新找）。"""
        with self._lock:
            if self._door is not None and (time.time() - self._ts) <= max_age:
                return dict(self._door)
        return None

    def stats(self):
        with self._lock:
            return {'ok': self._ok, 'err': self._err, 'seq': self._seq,
                    'age': round(time.time() - self._ts, 2) if self._ts else None}

    def stop(self):
        self._stop_evt.set()


_DETECTOR = {'t': None}


def detector_start(interval=0.30):
    det = _detector()
    if det is not None and det.is_alive():
        return det
    det = _DoorDetector(interval=interval)
    _DETECTOR['t'] = det
    det.start()
    log('      · 门检测线程已启动（间隔 %.2f 秒，读共享结果决策）' % interval)
    return det


def detector_stop():
    det = _detector()
    if det is not None:
        det.stop()
        _DETECTOR['t'] = None


def _detector():
    det = _DETECTOR.get('t')
    return det if det is not None and det.is_alive() else None


def door_latest(max_age=1.5):
    """主线程读"最新门结论"；检测线程没跑时返回 None。"""
    det = _detector()
    return det.latest(max_age) if det is not None else None


def _door_behind(steps=12, per_step_px=600):
    """★ 2026-11-15 作者原话：「**门应该在 180 度方向**」。

    进区域时角色是**背对门**的（面朝房间深处），所以"看正前方找门"经常什么都看不到；
    正前方没门就**回头看**：小步转、每步检测，**一看到门就停**（不转满整圈，省时间）。
    """
    for i in range(steps):
        check_pause('回头看门')
        _send_input_move(per_step_px, 0)
        time.sleep(0.3)
        d = _door_ahead()
        if d is not None:
            log('      · 回头第%d步看到门：偏%+d 宽%d' % (i + 1, d['cx'] - 1920, d['w']))
            return d
    log('      · 回头扫了 %d 步仍没看到门' % steps)
    return None


def _door_ahead(min_w=130, max_w=2000):
    """**只看正前方**找门（不转视角）。★ 2026-11-15 精简版：**只留两个快判据**。

    1) 屏幕上出现 `[F] 随意门` 提示 ⇒ 门就在面前（OCR 只扫提示框那一小块，~0.2s）
    2) SIFT 特征点匹配（~0.27s，实测门 24~151 内点 / 非门 0 命中）

    去掉的慢环节（实测拖垮全景）：全屏 OCR 兜底 3.2s、瞳孔检测 ~4s、模板兜底 1~2s。
    """
    try:
        beat('door_ahead')
    except Exception:
        pass
    try:
        ok_p, txt_p = door_prompt_present()
        if ok_p:
            return {'cx': 1920, 'cy': 1080, 'w': 420, 'h': 620,
                    'score': 1.0, 'src': 'prompt:%s' % txt_p}
    except Exception:
        pass
    d = None
    try:
        d = find_door_by_features()
    except Exception:
        d = None
    if d is not None and min_w <= d.get('w', 0) <= max_w:
        return d
    # ★ 线程共享结果只在 0.35 秒内可信 —— 转视角后它就是"上一帧的门"，已不在视野里 ✗
    d = door_latest(0.35)
    if d is not None and d.get('src') != 'pink' and min_w <= d.get('w', 0) <= max_w:
        return d
    return None

def _reacquire_door():
    """门丢了之后再找：**允许转向**（作者：只有进入区域那一次不许转）。

    先小步转 ±90° 各扫一遍，还找不到再用全景（16 秒）。
    """
    for turn in (900, -1800, 900):
        _send_input_move(turn, 0)
        time.sleep(0.45)
        d = _door_ahead()
        if d is not None:
            log('      · 转向后重新看到门（偏%+d 宽%d）' % (d['cx'] - 1920, d['w']))
            return d
    log('      · 转向也没找到门 → 用全景找一次')
    try:
        if pano_once('门丢失'):
            _send_input_move(0, 0)
    except Exception:
        pass
    return _door_ahead()


def _interact_labels_once(max_each=25):
    """**把正前方能看到的可交互物逐个交互掉**（作者：事件类区域的做法）。

    做法：OCR 世界标签 → 对准 → 走过去 → 按 F（OCR 校验）→ 记下名字 30 秒内不再理。
    返回 True = 至少成功交互了一个。
    """
    did = False
    done = {}
    t0 = time.time()
    while time.time() - t0 < max_each * 3:
        if on_station_picker() or area_passed():
            return True
        if is_event_ui():
            do_event_dialog()
            did = True
            continue
        lab = None
        txt = ''
        try:
            for kw in INTERACT_LABELS:
                l = find_label(kw)
                if l is not None:
                    lab, txt = l, l[2].strip()
                    break
        except Exception:
            lab = None
        if lab is None:
            break
        if done.get(txt, 0) and time.time() - done[txt] < 30:
            break
        lx, ly, _ = lab
        off = lx - 1920
        if abs(off) > 160:
            g = abs(_TURN_GAIN.get('g') or 0.42)
            _send_input_move(int(max(-520, min(520, off / g * 0.9))), 0)
            time.sleep(0.3)
            continue
        log('      · 交互「%s」：走过去' % txt)
        pyautogui.keyDown('w')
        time.sleep(1.0)
        pyautogui.keyUp('w')
        time.sleep(0.2)
        if press_f_checked():
            done[txt] = time.time()
            did = True
            time.sleep(1.2)
        else:
            done[txt] = time.time()      # 没有 F 提示也先记下，避免死循环
            break
    return did


# ═══════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者要求「重写」：**极简空间模型**（替换原来那套屏幕坐标伺服）
#
# 作者原话：「你是如何理解区域的空间结构的，我感觉这样调试好吃力啊」
#            「为什么不全景，视野里啥都没有在这里慢慢转做什么」
#            「如果在一个区域时间太长也重置位置吧」
#            「门应该在 180 度方向」
#            「按 F 前识别到这个标签是复活装置就不要点」
#
# ---- 空间模型（就这四句话）----
#     1) 角色进区域时**面朝房间深处**，**门恒定在身后 180°**；
#     2) 房间深处可能有：怪（战斗区）、装置/事件板（要按 F）、空地；
#     3) 门"不一定能开"——被怪挡着就得先清掉怪，装置没交互完门也可能是闭眼；
#     4) 方位用**全景轴的鼠标像素**表示（实测 ≈14.5 px/°，一屏≈3418 px）。
#        ⇒ 不再做"每帧居中"的伺服（增益一漂就左右打摆子，越补越乱 ✗）
#
# ---- 动作只有 4 个 ----
#     steer(off)      转到某方位（一次性，按方位角算）
#     hold_w(sec)     按住 W 直走
#     pano_once()     全景找门/找目标（已有实现，一次就对得上）
#     reset()         超时或没进展 → 暂离重进（重置位置）
# ═══════════════════════════════════════════════════════════════════════════

PX_PER_DEG = 14.5          # 实测：5267 px ≈ 363° ⇒ 14.5 px/°
USE_CLEAR_V2 = True        # 想回退旧流程就改成 False


def steer(off_px, max_mouse=900):
    """把目标（屏幕偏移 off_px）转到视野中心：**一次性按方位角算**，不做闭环伺服。"""
    try:
        # ★ 2026-11-15 受控实测（固定步长法）：基准偏-150，发 +400 后偏-746
        #   ⇒ 增益 = (偏0-偏1)/命令 = +1.49（**正**）
        #   ⇒ 居中命令 **mx = +off/g**（off<0 就往左转，门才会回到中心）
        g = abs(_TURN_GAIN['g'] or 1.49)
        mx = int(max(-max_mouse, min(max_mouse, off_px / max(0.25, g))))
        if mx == 0:
            return
        _send_input_move(mx, 0)
        time.sleep(0.22)
    except Exception as e:
        log('      · steer 出错: %s' % e)


def hold_w(sec):
    """按住 W 直走 sec 秒（走完立刻松开，不残留按键）。"""
    try:
        pyautogui.keyDown('w')
        time.sleep(max(0.2, sec))
        pyautogui.keyUp('w')
        time.sleep(0.12)
    except Exception:
        try:
            pyautogui.keyUp('w')
        except Exception:
            pass


def _v2_progressed(hud0):
    """本区有没有进展：出现选站点卡/过区，或者进度号变了。"""
    try:
        if on_station_picker() or area_passed():
            return True
        return progress_changed(hud0)
    except Exception:
        return False


def _v2_clear_blockers(kind='', budget=75):
    """② 清挡路的东西：战斗区撞怪（名牌 → YOLO → 全景），其它区按 F 交互。"""
    t0 = time.time()
    combat = (kind in ('战斗', '精英', '首领', '') )
    log('    · 清挡路（区域=%r，%s，预算 %d 秒）' % (kind or '?', '战斗' if combat else '交互', budget))
    if combat:
        use_technique()                      # 作者规则：本区域放一次秘技
    while time.time() - t0 < budget:
        check_pause('清挡路')
        try:
            if on_station_picker() or area_passed():
                return True
            if in_battle():
                log('    · ★ 进战斗 → 打完再说')
                wait_battle(timeout=420)
                txt = '|'.join(_texts(ocr_items()))
                if '选择祝福' in txt or '选择一项祝福' in txt:
                    pick_bless_inline()
                return True
        except Exception:
            pass
        if combat:
            # 每次循环再看一眼卡片：还在而秘技没用过 → 立刻放
            try:
                _hc, _cd = enemy_card_present()
                if _hc:
                    use_technique()
            except Exception:
                pass
            tgt = None
            try:
                tgt = find_enemy_label()          # 浮空名牌（排除顶部横幅）
            except Exception:
                tgt = None
            if tgt is None:
                try:
                    tgt = yolo_pick(CATE_ENEMY, min_conf=0.40, min_w=50)
                except Exception:
                    tgt = None
            if tgt is not None:
                off = tgt['cx'] - 1920
                log('    · 目标「%s」偏%+d → 转向 + 直走撞上去' % (str(tgt.get('label'))[:14], off))
                steer(off)
                hold_w(1.8)
                continue
            log('    · 视野里没目标 → **全景扫一圈**（不慢慢转）')
            try:
                if pano_once('v2清挡路') is False:
                    hold_w(1.2)
            except Exception as e:
                log('    · 全景异常: %s' % e)
            continue
        # 非战斗：按 F 交互（press_f_checked 自带黑名单：复活装置/抽奖机不按）
        try:
            if press_f_checked(settle=1.0):
                time.sleep(1.0)
                st = detect_state(verbose=False)[0]
                if st in ('device_curio', 'leave_confirm', 'misc_panel', 'bless_choose',
                          'event_dialog', 'popup_blank', 'roulette'):
                    return True
                continue
        except Exception:
            pass
        # 没交互提示 → 走两步换个位置再看
        hold_w(1.0)
    return False


_V2_IDLE = {'last_sig': None, 'same': 0}


def _v2_screen_sig():
    """低成本画面指纹（32x18 灰度），用来判断"有没有在动"。"""
    try:
        import numpy as _np
        img = screenshot()
        g = img[:, :, :3].mean(axis=2)
        h, w = g.shape
        small = _np.zeros((18, 32), dtype=_np.float32)
        sh, sw = h // 18, w // 32
        for i in range(18):
            for j in range(32):
                small[i, j] = g[i * sh:(i + 1) * sh, j * sw:(j + 1) * sw].mean()
        return small
    except Exception:
        return None


def _v2_idle_check(tag=''):
    """★ 2026-11-15 作者要求：「怎么可能出现停在原地发愣」。

    每调用一次就比一次画面；**连续 3 次几乎不变**（亮度均差 < 2.5）
    ⇒ 判定"原地发呆/顶着障碍"，返回 True，让调用方**立刻重置位置**。
    """
    try:
        import numpy as _np
    except Exception:
        return False
    sig = _v2_screen_sig()
    if sig is None:
        return False
    last = _V2_IDLE['last_sig']
    if last is not None and last.shape == sig.shape:
        diff = float(_np.abs(sig - last).mean())
        if diff < 2.5:
            _V2_IDLE['same'] += 1
        else:
            _V2_IDLE['same'] = 0
    _V2_IDLE['last_sig'] = sig
    if _V2_IDLE['same'] >= 3:
        log('    · ⚠ 画面连续 %d 次几乎不变（均差<2.5）→ 判定原地发愣（%s）'
            % (_V2_IDLE['same'], tag or '?'))
        _V2_IDLE['same'] = 0
        _V2_IDLE['last_sig'] = None
        return True
    return False


_FORCE_RESET = [False]
_WD_STARTED = [False]
_BEAT = [time.time()]          # ★ 心跳：主循环每走一步就喂一次


def beat(tag=''):
    """**喂狗**。主循环（以及战斗等待等合法长循环）每走一步调一次。"""
    _BEAT[0] = time.time()


def _idle_watchdog_start(timeout=45):
    """★ 2026-11-15 作者要求：「怎么可能出现停在原地发愣」。

    独立线程每 3 秒比一次画面：**连续 24 秒几乎不变**就按 ESC 脱困；
    连续 3 次脱困都无效 ⇒ 置 _FORCE_RESET，让流程**重置位置**。
    战斗/事件界面不动它（那些界面本来就静止）。
    """
    if _WD_STARTED[0]:
        return
    _WD_STARTED[0] = True
    try:
        import numpy as _np
    except Exception:
        return

    def _loop():
        kicks = 0
        while True:
            try:
                time.sleep(2.0)
                if os.path.exists(r"D:\_TOOL_DSH\workbench\div_uni.pause"):
                    _BEAT[0] = time.time()      # 暂停期间不算卡死
                    kicks = 0
                    continue
                idle = time.time() - _BEAT[0]
                if idle > timeout:
                    kicks += 1
                    log('  ⚠ 看门狗：主循环 %d 秒没喂狗 → 判定卡在某个调用里（第%d次）'
                        % (int(idle), kicks))
                    try:
                        press('esc', 0.8)       # 先试着把界面拉回来
                    except Exception:
                        pass
                    _BEAT[0] = time.time()      # 再给它一个超时窗口
                    if kicks >= 3:
                        log('  ⚠ 看门狗：连续 3 次没喂狗 → **请求重置位置**')
                        _FORCE_RESET[0] = True
                        kicks = 0
                elif idle < 5:
                    kicks = 0
            except Exception:
                pass

    threading.Thread(target=_loop, name='idle-watchdog', daemon=True).start()
    log('      · 心跳看门狗已启动（主循环 %d 秒不喂狗 → ESC；连续 3 次 → 重置位置）' % timeout)


# ════════════════════════════════════════════════════════════════════════
# ★★★ v3（2026-11-15 作者逐步给出的规则，**极度简化、保证会结束**）
#
#   作者规则汇总：
#     · 门在 **180°** 方向；
#     · **没有出现按 F 的标签 = 没有交互**（所以没有提示就绝不按 F）；
#     · 复活装置这类标签一出现就不按 F；
#     · 找不到就继续找，**3 次找不到就重置位置**；
#     · 不许原地发呆。
#
#   每圈只做三件事，全程不超过 ~20 秒：
#       转 180° → 看门 → 有门就转向+走；没门就走两步 + 转 60°
#   3 圈都没有进展 ⇒ **重置位置**（暂离重进）。
#   不再用全景导航（会顶着墙走、还会长时间阻塞 ✗），也不再用屏幕伺服。
# ════════════════════════════════════════════════════════════════════════

DEG180_PX = int(180 * PX_PER_DEG)      # 180° 对应的鼠标像素
DEG60_PX = int(60 * PX_PER_DEG)


def _v3_turn(delta_px):
    """一次性转固定角度（分块发，避免单次过大被游戏吞掉）。"""
    left = int(delta_px)
    step = 600 if left > 0 else -600
    while abs(left) > 0:
        n = int(max(-600, min(600, left)))
        try:
            _send_input_move(n, 0)
        except Exception:
            pass
        left -= n
        time.sleep(0.16)


# ════════════════════════════════════════════════════════════════════════
# ★★★ v4（2026-11-15）：**严格照设计文档**（docs\星铁-差分宇宙-流程笔记.md）
#
#   文档主流程：
#     ① 先找门（门只认正面：正面有「两只眼睛+▼」；颜色只能找背面）
#        找到 + 睁眼 → 直接往门走，中途遇障碍就绕过
#     ② 找不到门 **或 门闭眼** ⇒ 说明还有东西要交互 → **看左上角区域类型**：
#          战斗 / 精英 / **首领** ⇒ **开秘技(E) 攻击怪物** → 进战斗打完
#          事件 / 其它            ⇒ 把区域里的可交互物按 F 交互掉
#     ③ 做完回去再找门（§7：区域没清完之前门是"睡着"的，按 F 没用）
#     ④ 反复不成就 ESC→暂离→重进→继续进度（回起点，起点正前方就是随意门）
#
#   同时满足的文档硬规则：
#     · §14.5 **门必须居中才前进**（|off| ≤ 60 才允许 W）
#     · §15.5 **区域类型闸门**：休整/空白/商店/财富/铸造 里没有怪
#              ⇒ **不追怪、不放秘技**
#     · §17.25 点击地面 = 攻击，**不能当寻路**（本函数只用键盘 WASD 走位）
#     · §3.12 复活装置/抽奖机 一律不按 F（F_DENY_WORDS 已拦）
# ════════════════════════════════════════════════════════════════════════

V4_TOL = 60                 # 门居中的容差（§14.5）
V4_AREA_BUDGET = 150        # 单区预算（秒）；超了就走重置


def _v4_strafe(side, sec=0.55):
    """§8.4：连续两次"画面几乎没位移" ⇒ **横移绕障（左右交替）**。
    绕障只能用键盘 W/A/S/D（§17.25：点击地面是攻击，不是寻路）。"""
    side = 'a' if side not in ('a', 'd') else side
    try:
        log('    · 横移绕障：%s %.2f 秒' % (side.upper(), sec))
        pyautogui.keyDown(side)
        time.sleep(sec)
        pyautogui.keyUp(side)
        time.sleep(0.10)
    except Exception:
        try:
            pyautogui.keyUp(side)
        except Exception:
            pass


def _v4_center_door(tries=4):
    """把门转到 |off| ≤ V4_TOL（§14.5 居中才前进）。返回 (ok, door)。"""
    d = None
    for i in range(tries):
        check_pause('v4居中')
        d = None
        try:
            d = pick_door(min_score=0.55)
        except Exception:
            d = None
        if d is None:
            try:
                d = _door_ahead()
            except Exception:
                d = None
        if d is None:
            return False, None
        off = d['cx'] - 1920
        if abs(off) <= V4_TOL:
            log('    · 门已居中（偏%+d，第%d次）→ 允许前进' % (off, i + 1))
            return True, d
        steer(off)
        time.sleep(0.12)
    return False, d


# ════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者第三次纠正：「**怎么不放全景了，一直在小范围转视角和移动**」
#     → 找门必须**整圈全景**（转一圈、每步检测），找到后**一次性转到方位角**，
#       而不是"微转几度 → 复检 → 再微转"那种原地磨的写法。
#     px/° 实测 ≈ 14.5（§3.10：全景 5267px ≈ 363°）
# ════════════════════════════════════════════════════════════════════════

def _v4_pano_scan_door(step_px=600, steps=12):
    """整圈全景找门。返回 (bearing_px, cum_px) 或 (None, cum_px)。

    bearing_px = 从起点算起、门所在的方位（鼠标像素，正=右转）；
    cum_px     = 扫完一圈后累计转过的像素（用来"转回去"）。
    """
    cum = 0
    best = None
    for i in range(steps):
        check_pause('全景找门')
        beat('全景找门')
        d = None
        try:
            d = _door_ahead()
        except Exception:
            d = None
        if d is not None:
            off = d['cx'] - 1920
            bearing = cum + off
            log('    · 全景第 %d 步看到门：本步偏%+d 宽%d → 累计方位 %+d px（≈%.0f°）'
                % (i + 1, off, d['w'], bearing, bearing / PX_PER_DEG))
            return bearing, cum + off          # cum 这里返回"当前朝向"
        _send_input_move(step_px, 0)
        cum += step_px
        time.sleep(0.26)
    log('    · 全景转了一整圈（%d 步 ≈ %d°）都没看到门' % (steps, int(cum / PX_PER_DEG)))
    return None, cum


def _v4_turn_to_bearing(bearing, cur):
    """一次性转到目标方位（鼠标像素差）。"""
    delta = int(bearing - cur)
    if abs(delta) < 25:
        return
    log('    · 转到门方位：当前 %+d → 目标 %+d ⇒ 转 %+d px（≈%.0f°）'
        % (cur, bearing, delta, delta / PX_PER_DEG))
    left = delta
    while abs(left) > 0:
        n = int(max(-800, min(800, left)))
        try:
            _send_input_move(n, 0)
        except Exception:
            pass
        left -= n
        time.sleep(0.16)


def _v4_center_by_steer(d, tol=V4_TOL, tries=5):
    """★ 2026-11-15 作者质问：「刚刚不是都看到门了吗，怎么又走远了」

    根因：原 `_v4_center_door` 用 pick_door 复检，**它这次没认出来** ⇒ 判成"没能居中"
    ⇒ 掉进"先清区域"分支 ⇒ 横移/走位 ⇒ **离门越来越远** ✗

    这里改成：**手上已经有一扇门（d）就只转不横移**：
      steer(off) → 用 _door_ahead/_door_behind（和发现它时同一个检测器）复检 → 收敛就前进。
    """
    cur = d
    for i in range(tries):
        check_pause('v4转向门')
        try:
            off = cur['cx'] - 1920
        except Exception:
            return False, None
        if abs(off) <= tol:
            log('    · 门已居中（偏%+d，第%d次）→ 允许前进' % (off, i + 1))
            return True, cur
        log('    · 转向门：偏%+d → 转一次（第%d次）' % (off, i + 1))
        steer(off)
        time.sleep(0.25)
        nxt = None
        try:
            nxt = _door_ahead()
        except Exception:
            nxt = None
        if nxt is None:
            try:
                nxt = _door_behind(steps=3, per_step_px=300)
            except Exception:
                nxt = None
        if nxt is None:
            log('    · 转完门不见了 → 停止横移，交给下一圈重新找')
            return False, None
        cur = nxt
    try:
        return abs(cur['cx'] - 1920) <= tol, cur
    except Exception:
        return False, None


def technique_covers_enemy(img=None, verbose=False):
    """★★★ 2026-11-15 作者：「**秘技圈如果圈到怪物，是会有红色的小圈的**」
    ⇒ 圈住了就**原地别动**（战斗会自己触发）；没圈住才往前走。

    ⚠️ 第一版用"红色圆斑"判定，实测误报 11 个（把怪身上的红色也算进去了 ✗）
    ⇒ 改为**红环（轮廓里有孔洞）**判据，并且要求**离角色近**（技术圈只罩附近）：
        * RETR_CCOMP 取有子轮廓的连通域 = 环
        * 半径 10~45 px、圆度 ≥0.5
        * 环心距画面中心 ≤1000 px（角色在画面中心附近）
    返回 (bool, rings)。
    """
    import cv2
    import numpy as _np
    try:
        if img is None:
            img = screenshot()
        H, W = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        m1 = cv2.inRange(hsv, (0, 80, 110), (12, 255, 255))
        m2 = cv2.inRange(hsv, (168, 80, 110), (180, 255, 255))
        mask = cv2.bitwise_or(m1, m2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _np.ones((3, 3), _np.uint8))
        cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        rings = []
        if hier is not None:
            for i, c in enumerate(cnts):
                # 只取"有孔洞"的外轮廓（hier[0][i][2] = 第一个子轮廓下标）
                if hier[0][i][2] == -1:
                    continue
                a = cv2.contourArea(c)
                if a < 150:
                    continue
                (cx, cy), r = cv2.minEnclosingCircle(c)
                if r < 10 or r > 45:
                    continue
                circ = a / max(1.0, 3.14159 * r * r)
                # 环是"空心"的：面积远小于外接圆
                if circ > 0.75:
                    continue
                if circ < 0.15:
                    continue
                if abs(cx - W / 2) > 1000 or abs(cy - H / 2) > 800:
                    continue
                rings.append((int(cx), int(cy), int(r)))
        if verbose:
            log('    · 红环检测：%d 个 %r' % (len(rings), rings[:5]))
        return (1 <= len(rings) <= 6), rings
    except Exception as e:
        if verbose:
            log('    · 红环检测异常: %s' % e)
        return False, []


def _v4_clear_by_kind(kind, budget=80):
    """② 按区域类型处理（严格照 §15.5 闸门）。"""
    t0 = time.time()
    if kind in NO_ENEMY_KINDS:
        log('    · 区域=%r ∈ 无怪区（§15.5）→ **不追怪、不放秘技**，只找门/交互' % (kind,))
        # 无怪区：有 F 提示就交互，没有就往门的方向试走
        for _ in range(4):
            if time.time() - t0 > budget:
                break
            check_pause('v4无怪区')
            beat('v4无怪区')
            try:
                if press_f_checked(settle=1.0):
                    time.sleep(0.8)
                    return True
            except Exception:
                pass
            hold_w(1.0)
        return False

    if kind in ('战斗', '精英', '首领', '异常', '冒险', '奖励', ''):
        # ★★★ 2026-11-15 作者：「**不用识别怪物，你就朝前面走就行**，
        #   那么多怪物识别不过来」⇒ 战斗区策略极简化：
        #     ① 有怪卡就原地开秘技（pyautogui 按 E，实测有效）
        #     ② 然后**一直往前走**，撞到谁算谁；一进战斗就等它打完
        #     ③ 打完（或走满预算）返回，交给上层继续找门
        log('    · 区域=%r ⇒ **开秘技 + 一直往前走**（不做怪物识别）' % (kind or '未知→按战斗处理',))
        try:
            _has_card, _card = enemy_card_present()
        except Exception:
            _has_card, _card = False, ''
        if _has_card:
            log('    · 看到怪物卡片「%s」⇒ 原地开秘技(E)' % _card)
        else:
            log('    · 没看到怪卡也先开一次秘技（作者：秘技点是自动补充的）')
        use_technique()
        # ★ 浮空时间很短 ⇒ 立刻（0.25 秒）查红圈，不能等
        time.sleep(0.25)
        try:
            covered, _rings = technique_covers_enemy(verbose=True)
        except Exception:
            covered = False
        if covered:
            log('    · **秘技已罩住怪（看到红圈）⇒ 原地不动，等战斗触发**')
        else:
            log('    · 没看到红圈（没罩住）⇒ 往前走一点再判断')
        t_walk = time.time()
        while time.time() - t0 < budget:
            check_pause('v4打怪')
            beat('v4打怪')
            try:
                if on_station_picker() or area_passed():
                    return True
                if in_battle():
                    log('    · ★ 撞到怪 → 进战斗，等它打完')
                    _tb = time.time()
                    wait_battle(timeout=420)
                    _el = time.time() - _tb
                    log('    · 战斗判定用时 %.0f 秒' % _el)
                    if _el < 4.0 and stuck_tick('battle_fake_end', limit=2):
                        log('    · [熔断] 连续多次"战斗秒结束" ⇒ 判定为假战斗，放宽判定后继续')
                        stuck_reset('battle_fake_end')
                        return False
                    if _el < 4.0:
                        time.sleep(1.5)
                        if in_battle():
                            log('    · 复检：其实还在战斗，继续等')
                            wait_battle(timeout=420)
                    stuck_reset('battle_fake_end')
                    txt = '|'.join(_texts(ocr_items()))
                    if '选择祝福' in txt or '选择一项祝福' in txt:
                        pick_bless_inline()
                    return True
            except Exception:
                pass
            try:
                _cov, _rg = technique_covers_enemy()
            except Exception:
                _cov = False
            if _cov:
                log('    · 看到红圈（已罩住）⇒ 原地等战斗，不再前进')
                time.sleep(0.35)
                continue
            log('    · 仍没罩住 ⇒ 往前走 0.5 秒（已走 %.0f 秒）' % (time.time() - t_walk))
            try:
                pyautogui.keyDown('w')
                time.sleep(0.5)
                pyautogui.keyUp('w')
            except Exception:
                try:
                    pyautogui.keyUp('w')
                except Exception:
                    pass
            time.sleep(0.2)
        return False

    # 事件类：按 F 交互（没有提示就是不交互）
    log('    · 区域=%r ⇒ 交互事件（有 F 提示才按）' % (kind,))
    while time.time() - t0 < budget:
        check_pause('v4交互')       # ★ 同上
        beat('v4交互')
        try:
            if press_f_checked(settle=1.0):
                time.sleep(0.8)
                st = detect_state(verbose=False)[0]
                if st in ('event_dialog', 'pick_station', 'bless_choose', 'roulette',
                          'device_curio', 'leave_confirm', 'misc_panel', 'popup_blank'):
                    return True
                continue
        except Exception:
            pass
        hold_w(1.2)
        _v3_turn(DEG60_PX)
    return False


# ════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者原话：「**转视角让门在中心走过去不就行了吗，又没有障碍**」
#
#   就这三步，别的都不要：
#     ① 看到门 → 算它离屏幕中心多少 → **转那么一下**
#     ② 再看一眼，还偏就再转（在线学增益，避免转过头）
#     ③ |偏| ≤ 70 ⇒ **按住 W 走过去**（到门口有 F 提示就按 F）
#   没有全景、没有横移、没有"先清区域"绕路。
# ════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者讲清了空间关系（照人的走法实现）：
#
#   「**全景是为了找到门，转视角是为了对准走过去，横移是为了躲开障碍物**，
#     就像人走路一样：转一圈找到目标，然后盯着目标走过去，路上出现障碍就闪身绕过」
#
#   ⇒ 三个动作各有唯一职责，不许混用：
#       全景（_v4_pano_scan_door） 只用来**找**     —— 视野里没有门时
#       转视角（steer）            只用来**对准**   —— 门看得见但不在中心时
#       横移（_v4_strafe）         只用来**躲障碍** —— 对准了、在走、但**走不动**时
# ════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者：「转一圈找到目标，然后**盯着目标走过去**，路上出现障碍就闪身绕过」
#
#   实测结论（受控实验）：门检测**每帧都在跳**（同一扇门宽度 216/316/376 变化）
#   ⇒ "先精确对准、再走" 的两步法必然左右打摆子 ✗
#   ⇒ 改成**边走边微调**（像人走路那样，一直朝目标走、顺手修正方向）✓
#   只有"走不动"（连续两帧门宽没变大）才横移躲障碍 ✓
# ════════════════════════════════════════════════════════════════════════

def door_edge_hint(img=None):
    """★ 2026-11-15 补的判据：**门只露出一条边时的方向提示**。

    场景（作者截图证实）：门贴在画面最右侧/左侧，大部分在画面外
      ⇒ SIFT 算不出位置（实测 12 个方向里 11 次 None）、F 提示也不出现 ✗
    做法：找"粉色竖向色块"，若它**贴在左/右边框**且高度够大（>25% 屏高），
    就说明门在那个方向 ⇒ 返回 -1(左) / +1(右) / 0(没线索)。

    ⚠️ 只用来看**方向**，不用来认定"这是门"（认定仍靠 SIFT/提示）。
    """
    import cv2
    import numpy as _np
    if img is None:
        img = screenshot()
    h_img, w_img = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (135, 30, 110), (180, 235, 255))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _np.ones((15, 15), _np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = (0, 0)          # (面积, 方向)
    for c in cnts:
        x, y, ww, hh = cv2.boundingRect(c)
        if hh < h_img * 0.25:          # 不够高，不是门板
            continue
        if ww > w_img * 0.6:           # 太宽，是墙/招牌
            continue
        if x <= w_img * 0.06:          # 贴左边框
            area = ww * hh
            if area > best[0]:
                best = (area, -1)
        elif x + ww >= w_img * 0.94:   # 贴右边框
            area = ww * hh
            if area > best[0]:
                best = (area, 1)
    return best[1]


def vlm_door_seen():
    """问本地 VLM：**画面里有没有那扇粉色带眼睛的门**（只判有无，不问坐标）。

    依据文档实测结论：VLM 的**坐标不可信**（会把中央的粉门说成右侧 ✗），
    但"这是什么/在不在"判得很准 ✓ ⇒ 只用来做存在性判断。
    返回 True / False / None（None = VLM 不可用或答非所问）。
    """
    try:
        ans = vlm_ask('画面里有没有一扇粉红/品红色的门，门上有两只白色大眼睛和一个倒三角？'
                      '只回答一个字：有 / 无。不要解释。', num_predict=12)
    except Exception:
        return None
    if not ans:
        return None
    a = ans.strip()
    if a.startswith('有') or '有' in a[:6] and '没有' not in a[:6]:
        return True
    if '无' in a[:6] or '没有' in a[:6]:
        return False
    return None


# ════════════════════════════════════════════════════════════════════════
# ★★★ 2026-11-15 作者：「把**识别怪也作为一个线程**，这样如果在走路过程中
#      出现怪物卡牌就直接放秘技打怪」
#
#   设计：怪卡只出现在**屏幕顶部那一条**（实测「2-3 等级89 凶梦传染」在 y≈100~220），
#   所以线程只 OCR 那一小条（~0.3 秒），不做全屏 OCR（全屏 2~3 秒会拖死主线程）。
#   主线程/走位循环随时读 `enemy_latest()`：一有卡 ⇒ 立刻开秘技 + 转打怪。
# ════════════════════════════════════════════════════════════════════════

ENEMY_CARD_BOX = (800, 60, 2950, 250)      # 物理坐标 x1,y1,x2,y2（3840x2160）
_ENEMY_STATE = {'card': False, 'text': '', 'ts': 0.0, 'seq': 0}
_ENEMY_THREAD = [None]
_ENEMY_INTERRUPT = [False]   # 中断 pending 标志（像 IRQ 状态位）

# ★ 退出时兜底释放按键（含被强杀时的 atexit；SIGTERM 由 finally 处理）
try:
    import atexit as _atexit
    _atexit.register(lambda: release_all_keys('atexit'))
except Exception:
    pass


def enemy_card_fast(img=None):
    """只在顶部条里找「等级NN 怪名」。返回 (bool, 文字)。"""
    import cv2
    try:
        if img is None:
            img = screenshot()
        H, W = img.shape[:2]
        sx = W / 3840.0
        sy = H / 2160.0
        x1, y1, x2, y2 = ENEMY_CARD_BOX
        roi = img[int(y1 * sy):int(y2 * sy), int(x1 * sx):int(x2 * sx)]
        if roi.size == 0:
            return False, ''
        for t, sc, cx, cy in ocr_items(roi):
            if sc >= 0.40 and ENEMY_LABEL_RE.match(t.strip()):
                return True, t.strip()[:24]
    except Exception:
        return False, ''
    return False, ''


class _EnemyWatcher(threading.Thread):
    """后台怪卡监视线程（间隔 0.5 秒，只 OCR 顶部条）。"""

    def __init__(self, interval=0.5):
        super().__init__(name='enemy-watcher', daemon=True)
        self.interval = interval
        self._stop_evt = threading.Event()

    def run(self):
        while not self._stop_evt.is_set():
            try:
                if os.path.exists(PAUSE_FILE):
                    self._stop_evt.wait(0.4)
                    continue
                ok, txt = enemy_card_fast()
                now = time.time()
                if ok:
                    if not _ENEMY_STATE['card']:
                        log('      · [怪卡线程] 出现怪物卡片「%s」' % txt)
                    _ENEMY_STATE.update(card=True, text=txt, ts=now)
                else:
                    # 卡片消失要等 1.2 秒确认，避免一帧抖动就丢
                    if _ENEMY_STATE['card'] and now - _ENEMY_STATE['ts'] > 1.2:
                        _ENEMY_STATE.update(card=False, text='', ts=now)
            except Exception:
                pass
            self._stop_evt.wait(self.interval)

    def stop(self):
        self._stop_evt.set()


def enemy_watcher_start(interval=0.5):
    if _ENEMY_THREAD[0] is not None and _ENEMY_THREAD[0].is_alive():
        return
    th = _EnemyWatcher(interval)
    th.start()
    _ENEMY_THREAD[0] = th
    log('      · 怪卡监视线程已启动（只 OCR 顶部条，0.5 秒一次）')


def enemy_latest(max_age=1.5):
    """主线程读怪卡状态。返回 (bool, 文字)。"""
    if not _ENEMY_STATE['card']:
        return False, ''
    if time.time() - _ENEMY_STATE['ts'] > max_age:
        return False, ''
    return True, _ENEMY_STATE['text']


def walk_forward_to_door(hud0=None, max_seconds=40.0):
    """★★★ 2026-11-15 作者反馈「**走歪了，而且走过头了**」后重写：**闭环小步走**。

    原来是"沿正前方盲走 15 秒"，实测必然走歪+走过头 ✗。
    现在每一步都先看一眼门，再决定怎么走：
        每 0.6 秒一循环：
          ① 有「随意门」提示 ⇒ 按 F（这就是门前）
          ② 门位置偏了 ⇒ **只转不走**（转到 |偏|≤50）
          ③ 已对准 ⇒ 走 0.6 秒；若门已经很大（宽≥480）只走 0.4 秒（防走过头）
    """
    t0 = time.time()
    G = 1.49
    log('    · 闭环走向门（每 0.6 秒重测一次，边走边修，防走歪/走过头）')
    while time.time() - t0 < max_seconds:
        check_pause('走门')
        beat('走门')
        if hud0 is not None and _v2_progressed(hud0):
            return True
        # 中断检查（安全点：两次按键之间）——怪卡出现就跳去 打怪 ISR
        try:
            _has, _etxt = enemy_latest()
        except Exception:
            _has, _etxt = False, ''
        if _has:
            log('    · [中断] 走门途中出现怪物卡片「%s」⇒ 立刻开秘技，转去打怪' % _etxt)
            use_technique()
            _ENEMY_INTERRUPT[0] = True
            return False
        try:
            ok_p, txt_p = door_prompt_present()
        except Exception:
            ok_p, txt_p = False, ''
        if ok_p:
            log('    · 到门前，看到「%s」⇒ 按 F' % txt_p)
            press('f', 1.0)
            time.sleep(1.2)
            if hud0 is not None and _v2_progressed(hud0):
                log('    · ✓ 过门')
                return True
            press('f', 1.0)
            time.sleep(1.2)
            if hud0 is not None and _v2_progressed(hud0):
                log('    · ✓ 按 F 过门')
                return True
            continue
        d = None
        try:
            d = door_probe_inplace(verbose=False)
        except Exception:
            d = None
        if d is None:
            log('    · 走门途中丢门 → 返回找门')
            return False
        off = int(d['cx']) - 1920
        w = int(d['w'])
        if abs(off) > 50:
            mx = int(max(-150, min(150, off / G)))
            if mx == 0:
                mx = 1 if off < 0 else -1
            log('    · 走歪了（偏%+d 宽%d）→ 先转%+d 回到门的方向' % (off, w, mx))
            _send_input_move(mx, 0)
            time.sleep(0.15)
            continue
        step = 0.4 if w >= 480 else 0.6
        log('    · 对准（偏%+d 宽%d）→ 走 %.1f 秒' % (off, w, step))
        try:
            pyautogui.keyDown('w')
            time.sleep(step)
            pyautogui.keyUp('w')
        except Exception:
            try:
                pyautogui.keyUp('w')
            except Exception:
                pass
        time.sleep(0.1)
    log('    · 走门超时（%.0f 秒）' % max_seconds)
    return False


def walk_to_door_v6(hud0=None, max_seconds=180):
    """★★★ 2026-11-15 v6：**以「随意门」提示为唯一硬判据 + 系统性扫场**。

    为什么放弃"先找门再走过去"：SIFT 在远处/侧角召回不稳（实测 12~22 内点，
    贴着阈值抖动），于是"对准→丢门→回退"来回打摆子 ✗。
    而「随意门」提示是 OCR 文字判据，**一旦出现必然在门前**，几乎不会假。

    策略（扫场）：循环 { 有提示→按F | 前进 1.2s | 转 45° }
      ⇒ 最多 8 个扇区就能覆盖一圈；每个扇区都会"先走再看"，
        所以门在任何方向都能被撞到，且全程不需要识别门。
    若有强 SIFT 命中（内点≥20 且 |偏|≤900），顺带朝它走一步（加速）。
    """
    t0 = time.time()
    sector = 0
    # ★ 先沿正前方直走（作者的空间描述）——成功就直接返回，省掉扫场
    if walk_forward_to_door(hud0, max_seconds=40.0):
        return True
    log('    · v6 扫场找门（提示为准；前进+转40°）')
    while time.time() - t0 < max_seconds:
        check_pause('v6找门')
        beat('v6找门')
        if hud0 is not None and _v2_progressed(hud0):
            return True

        # ① 提示 = 硬判据
        try:
            ok_p, txt_p = door_prompt_present()
        except Exception:
            ok_p, txt_p = False, ''
        if ok_p:
            log('    · 看到「%s」⇒ 按 F' % txt_p)
            press('f', 1.0)
            time.sleep(1.0)
            if hud0 is not None and _v2_progressed(hud0):
                log('    · ✓ 过门')
                return True
            press('f', 1.0)
            time.sleep(1.2)
            if hud0 is not None and _v2_progressed(hud0):
                log('    · ✓ 按 F 过门')
                return True
            continue

        # ② 有强 SIFT 命中就先朝它走（加速，不做精确对准）
        strong = False
        try:
            d = find_door_by_features()
            if d is not None and int(d.get('score', 0)) >= 20 and abs(int(d['cx']) - 1920) <= 900:
                off = int(d['cx']) - 1920
                mx = int(max(-300, min(300, off / 1.49)))
                log('    · 强命中（内点%d 宽%d 偏%+d）→ 微转%+d 并前进' % (d['score'], d['w'], off, mx))
                if mx:
                    _send_input_move(mx, 0)
                    time.sleep(0.15)
                strong = True
        except Exception:
            strong = False

        # ③ 前进
        try:
            pyautogui.keyDown('w')
            time.sleep(2.2)          # ★ 2026-11-15：1.2 秒走得太近，扫场覆盖不到门 ⇒ 2.2 秒
            pyautogui.keyUp('w')
        except Exception:
            try:
                pyautogui.keyUp('w')
            except Exception:
                pass
        time.sleep(0.12)
        if hud0 is not None and _v2_progressed(hud0):
            return True

        # ④ 再查一次提示（走过去正好到门前的情况）
        try:
            ok_p, txt_p = door_prompt_present()
        except Exception:
            ok_p, txt_p = False, ''
        if ok_p:
            log('    · 走到门前，看到「%s」⇒ 按 F' % txt_p)
            press('f', 1.0)
            time.sleep(1.0)
            if hud0 is not None and _v2_progressed(hud0):
                log('    · ✓ 过门')
                return True
            continue

        # ⑤ 没提示 ⇒ **先看粉色块贴边（门只露一条边时的方向）**
        if not strong:
            try:
                hint = door_edge_hint()
            except Exception:
                hint = 0
            if hint:
                log('    · 粉色块贴在%s边 ⇒ 门在那侧，转 35°' % ('右' if hint > 0 else '左'))
                _send_input_move(int(hint * 500), 0)
                time.sleep(0.25)
                continue
            seen = None
            try:
                seen = vlm_door_seen()
            except Exception:
                seen = None
            if seen is True:
                log('    · VLM：画面里有门 → 继续往前走（不转）')
                continue
            sector += 1
            log('    · 扇区 %d（VLM=%r）：转 40° 换个方向' % (sector, seen))
            _send_input_move(580, 0)
            time.sleep(0.2)
    log('    · v6 扫场超时（%d 秒）' % max_seconds)
    return False


def walk_to_door_v5(hud0=None, max_seconds=90):
    """★★★ 2026-11-15 走位状态机（小角度、带记忆、会纠错）。

    作者的话：「转视角让门在中心走过去不就行了吗，又没有障碍」
    ⇒ 只有三种动作，而且**都是小角度**：
        找门：每个方向 ±120 px（SIFT 只在近乎正对时可靠，角度一大就丢）
        对准：|偏|>80 时转 clamp(偏/1.49, ±150)
        直走：对准后按 W 0.8 秒；若门"变小"说明走反了 → 小步转身重来
    门提示「随意门」一出现 ⇒ 立刻按 F（这是唯一可靠的过门信号）。
    """
    t0 = time.time()
    G = 1.49
    last_mx = 0
    lost = 0
    w_prev = 0
    aim_dir = 0          # ★ 正在对准的方向（丢帧时**沿原方向继续**，不要回退）
    aim_miss = 0
    log('    · v5 走门（找门±120 / 对准±150 / 见提示按F）')

    def find():
        try:
            d = find_door_by_features()
        except Exception:
            d = None
        if d is None or not (130 <= int(d.get('w', 0)) <= 2000):
            return None
        return d

    while time.time() - t0 < max_seconds:
        check_pause('v5走门')
        beat('v5走门')
        if hud0 is not None and _v2_progressed(hud0):
            return True

        # ① 门提示出现 ⇒ 已经在门前，按 F
        try:
            ok_p, txt_p = door_prompt_present()
        except Exception:
            ok_p, txt_p = False, ''
        if ok_p:
            log('    · 看到「%s」提示 ⇒ 按 F' % txt_p)
            press('f', 1.0)
            time.sleep(1.0)
            if hud0 is not None and _v2_progressed(hud0):
                return True
            press('f', 1.0)
            time.sleep(1.2)
            if hud0 is not None and _v2_progressed(hud0):
                return True
            continue

        d = find()
        if d is None:
            lost += 1
            if aim_dir:
                # ★ 关键修正：正在对准时丢帧 ⇒ **沿原方向继续转**（累积进展），
                #   原来一律回退，等于把刚转的转回去，永远到不了中心 ✗
                aim_miss += 1
                step = int(aim_dir * 150)
                log('    · 对准中丢帧 → 沿原方向继续 %+d（第%d次）' % (step, aim_miss))
                _send_input_move(step, 0)
                if aim_miss >= 6:
                    aim_dir = 0
                    aim_miss = 0
            elif last_mx and lost <= 3:
                back = int(-last_mx * 0.8)
                log('    · 丢门 → 回退 %+d（上次 %+d）' % (back, last_mx))
                _send_input_move(back, 0)
                last_mx = 0
            else:
                step = 300 if lost % 18 else -300
                log('    · 丢门 → 扫视 %+d（第%d次）' % (step, lost))
                _send_input_move(step, 0)
                last_mx = 0
            time.sleep(0.22)
            continue
        lost = 0

        off = int(d['cx']) - 1920
        w = int(d['w'])

        if abs(off) > 80:
            mx = int(max(-150, min(150, off / G)))
            if mx == 0:
                mx = 1 if off < 0 else -1
            log('    · 对准：偏%+d（宽%d）→ 转%+d' % (off, w, mx))
            _send_input_move(mx, 0)
            last_mx = mx
            aim_dir = 1 if mx > 0 else -1
            aim_miss = 0
            time.sleep(0.2)
            continue

        aim_dir = 0        # 已对准，清掉对准记忆
        aim_miss = 0
        # 已对准 ⇒ 直走一小步（走的过程中不转，保持 SIFT 能认）
        log('    · 已对准（偏%+d 宽%d）→ 前进一小步' % (off, w))
        try:
            pyautogui.keyDown('w')
            time.sleep(0.8)
            pyautogui.keyUp('w')
        except Exception:
            try:
                pyautogui.keyUp('w')
            except Exception:
                pass
        time.sleep(0.15)
        if hud0 is not None and _v2_progressed(hud0):
            log('    · ✓ 过门了')
            return True

        d2 = find()
        if d2 is not None:
            w2 = int(d2['w'])
            if w_prev and w2 + 10 < w_prev:
                log('    · 门在变小（%d→%d）⇒ 方向反了，小步转身重来' % (w_prev, w2))
                for _ in range(9):                       # 约 180°
                    _send_input_move(300, 0)
                    time.sleep(0.1)
                w_prev = 0
            else:
                w_prev = w2
    log('    · v5 走门超时（%d 秒）' % max_seconds)
    return False


def walk_steer_to_door(hud0=None, max_seconds=75, tol=70):
    """盯着门走过去：**一直按住 W**，每 0.15 秒小修正一次方向；走不动才横移。"""
    t0 = time.time()
    g = 1.49                # 受控实测（偏-150，发+400 → 偏-746 ⇒ 增益 +1.49）
    last_mx = 0
    side = 'a'
    no_prog = 0
    last_w = 0
    log('    · 盯门走：按住 W + 每 0.15s 微调（实时增益 %.1f）' % g)

    def detect():
        d = None
        for _ in range(2):
            d = door_latest(0.8)
            if d is None:
                try:
                    d = _door_ahead()
                except Exception:
                    d = None
            if d is not None:
                return d
            time.sleep(0.06)
        return None

    while time.time() - t0 < max_seconds:
        check_pause('盯门走')
        beat('盯门走')
        if hud0 is not None and _v2_progressed(hud0):
            return True
        # ★ 屏幕出现「F 随意门」⇒ 已经在门前，直接按 F
        try:
            ok_p, txt_p = door_prompt_present()
            if ok_p:
                log('    · 看到交互提示「%s」⇒ 已在门前，按 F' % txt_p)
                press('f', 1.0)
                time.sleep(1.0)
                if hud0 is not None and _v2_progressed(hud0):
                    log('    · ✓ 过门了')
                    return True
                try:
                    press('f', 1.0)
                except Exception:
                    pass
                time.sleep(1.0)
                if hud0 is not None and _v2_progressed(hud0):
                    return True
        except Exception:
            pass

        d = detect()
        if d is None:
            # ★ 2026-11-15：SIFT 只在"正面"好用，转多了就丢
            #   ⇒ 丢了先**反向回退一半**（大概率转回来就又能看见），别整圈全景
            try:
                pyautogui.keyUp('w')
            except Exception:
                pass
            if last_mx:
                back = int(-last_mx * 0.6)
                log('    · 门丢了 → 反向回退 %+d（上次转了 %+d）' % (back, last_mx))
                try:
                    _send_input_move(back, 0)
                except Exception:
                    pass
                last_mx = 0
                time.sleep(0.2)
                continue
            log('    · 门丢了且没有历史转向 → 全景转一圈找')
            bearing, cum = _v4_pano_scan_door()
            if bearing is not None:
                _v4_turn_to_bearing(bearing, cum)
            continue

        off = d['cx'] - 1920
        w_now = int(d.get('w', 0))
        # ★ 2026-11-15：偏得多（>350）先**站定转过去**，别边走边偏（会走歪）
        if abs(off) > 300:
            mx = int(max(-300, min(300, off / max(0.6, g))))   # 限幅 ±300，避免一次转过丢门
            if mx == 0:
                mx = 1 if off < 0 else -1
            log('    · 偏太多（%+d）→ 先站定转 %+d 对准' % (off, mx))
            last_mx = mx
            try:
                _send_input_move(mx, 0)
            except Exception:
                pass
            time.sleep(0.18)
            continue
        if abs(off) > tol:
            mx = int(max(-220, min(220, off / max(0.6, g))))
            if mx == 0:
                mx = 1 if off < 0 else -1
            log('    · 边走边调：偏%+d → 转%+d' % (off, mx))
            try:
                _send_input_move(mx, 0)
            except Exception:
                pass

        # 按住 W 走一小段，期间继续微调
        try:
            pyautogui.keyDown('w')
        except Exception:
            pass
        for _ in range(6):
            time.sleep(0.15)
            if hud0 is not None and _v2_progressed(hud0):
                break
            d2 = detect()
            if d2 is None:
                continue
            off2 = d2['cx'] - 1920
            if abs(off2) > tol:
                mx2 = int(max(-220, min(220, off2 / max(0.6, g))))
                if mx2:
                    try:
                        _send_input_move(mx2, 0)
                    except Exception:
                        pass
        try:
            pyautogui.keyUp('w')
        except Exception:
            pass

        if hud0 is not None and _v2_progressed(hud0):
            log('    · ✓ 过门了')
            return True
        # 门口有 F 提示就按（没有提示 = 没交互）
        try:
            if press_f_checked(settle=0.8):
                time.sleep(0.8)
                if hud0 is not None and _v2_progressed(hud0):
                    log('    · ✓ 按 F 过门')
                    return True
        except Exception:
            pass

        # 走不动？⇒ 横移躲障碍（左右交替）
        d3 = detect()
        if d3 is not None:
            w_new = int(d3.get('w', 0))
            if last_w and w_new <= last_w + 4:
                no_prog += 1
            else:
                no_prog = 0
            last_w = w_new
        if no_prog >= 2:
            log('    · 门没变大（走不动）⇒ **横移 %s 躲障碍**' % side.upper())
            _v4_strafe(side, 0.5)
            side = 'd' if side == 'a' else 'a'
            no_prog = 0
    log('    · 盯门走超时（%d 秒）' % max_seconds)
    return False


def walk_to_door_human(hud0=None, max_seconds=75, tol=70):
    """像人一样走到门：找（全景）→ 对准（转视角）→ 盯着走 → 受阻才横移。"""
    t0 = time.time()
    g = 1.0
    prev = None
    side = 'a'
    no_prog = 0
    log('    · 走向门（找 → 对准 → 盯着走 → 受阻横移）')
    while time.time() - t0 < max_seconds:
        check_pause('走向门')
        beat('走向门')
        if hud0 is not None and _v2_progressed(hud0):
            return True

        # ---------- 看目标 ----------
        d = None
        for _ in range(3):
            try:
                d = _door_ahead()
            except Exception:
                d = None
            if d is not None:
                break
            time.sleep(0.18)

        if d is None:
            # ★ 找不到 ⇒ **全景转一圈找**（只找，不掺着走）
            log('    · 视野里没有门 → **全景转一圈找门**')
            bearing, cum = _v4_pano_scan_door()
            if bearing is not None:
                _v4_turn_to_bearing(bearing, cum)     # 转完就停，下一轮对准
            continue

        off = d['cx'] - 1920

        # ---------- 没对准 ⇒ 转视角对准 ----------
        if abs(off) > tol:
            if prev is not None:
                try:
                    # 命令是 +off/g ⇒ 观测增益 = (前偏差-后偏差)/前命令
                    gobs = (prev[0] - off) / float(prev[1])
                    if 0.2 < abs(gobs) < 5.0:
                        g = 0.6 * g + 0.4 * abs(gobs)
                except Exception:
                    pass
            mx = int(max(-700, min(700, off / max(0.35, g))))
            if mx == 0:
                mx = 1 if off < 0 else -1
            log('    · 对准：门偏%+d（宽%d）→ 转%+d（增益%.2f）' % (off, d['w'], mx, g))
            _send_input_move(mx, 0)
            prev = (off, mx)
            time.sleep(0.2)
            continue

        # ---------- 已对准 ⇒ 盯着目标往前走 ----------
        w_before = int(d.get('w', 0))
        log('    · 已对准（偏%+d，宽%d）→ 往前走一步' % (off, w_before))
        hold_w(0.9)
        if hud0 is not None and _v2_progressed(hud0):
            log('    · ✓ 过门了')
            return True
        try:
            if press_f_checked(settle=0.8):
                time.sleep(0.8)
                if hud0 is not None and _v2_progressed(hud0):
                    log('    · ✓ 按 F 过门')
                    return True
        except Exception:
            pass

        # ---------- 走不动？⇒ 横移躲障碍（只在"没前进"时）----------
        d2 = None
        try:
            d2 = _door_ahead()
        except Exception:
            d2 = None
        if d2 is not None:
            w_after = int(d2.get('w', 0))
            if w_after <= w_before + 4:
                no_prog += 1
                log('    · 没前进（门宽 %d→%d）→ 第%d次' % (w_before, w_after, no_prog))
            else:
                no_prog = 0
        if no_prog >= 2:
            log('    · 连续没前进 ⇒ **横移 %s 躲障碍**' % side.upper())
            _v4_strafe(side, 0.55)
            side = 'd' if side == 'a' else 'a'
            no_prog = 0
    log('    · 走向门超时（%d 秒）' % max_seconds)
    return False


def walk_to_door_simple(hud0=None, max_seconds=45, tol=70):
    """转视角让门居中 → 按住 W 走过去。返回 True = 过门成功。"""
    t0 = time.time()
    g = 1.0                 # 屏幕像素 / 鼠标像素（在线学）
    prev = None
    log('    · 去门：转视角让门居中 → 按住 W 走过去')
    while time.time() - t0 < max_seconds:
        check_pause('去门')
        beat('去门')
        if hud0 is not None and _v2_progressed(hud0):
            return True
        # 检测门（丢帧就重试几次，别一帧没看到就放弃）
        d = None
        for _ in range(3):
            try:
                d = _door_ahead()
            except Exception:
                d = None
            if d is not None:
                break
            time.sleep(0.2)
        if d is None:
            # 原地转 25° 再找（**不移动**）
            log('    · 这一帧没看到门 → 原地转约 25° 继续找')
            _send_input_move(int(max(-700, min(700, 25 * PX_PER_DEG))), 0)
            time.sleep(0.25)
            continue
        off = d['cx'] - 1920
        if abs(off) > tol:
            if prev is not None:
                try:
                    # 命令是 +off/g ⇒ 观测增益 = (前偏差-后偏差)/前命令
                    gobs = (prev[0] - off) / float(prev[1])
                    if 0.2 < abs(gobs) < 5.0:
                        g = 0.6 * g + 0.4 * abs(gobs)
                except Exception:
                    pass
            mx = int(max(-700, min(700, off / max(0.35, g))))
            if mx == 0:
                mx = 1 if off < 0 else -1
            log('    · 门偏%+d（宽%d）→ 转%+d（增益%.2f）' % (off, d['w'], mx, g))
            _send_input_move(mx, 0)
            prev = (off, mx)
            time.sleep(0.22)
            continue
        # 已居中 ⇒ 按住 W 走过去
        log('    · 门已居中（偏%+d，宽%d）→ 按住 W 走' % (off, d['w']))
        hold_w(1.3)
        if hud0 is not None and _v2_progressed(hud0):
            log('    · ✓ 过门了')
            return True
        # 门口有 F 提示才按（没有提示 = 没交互）
        try:
            if press_f_checked(settle=0.9):
                time.sleep(0.9)
                if hud0 is not None and _v2_progressed(hud0):
                    log('    · ✓ 按 F 过门')
                    return True
        except Exception:
            pass
    log('    · 去门超时（%d 秒）' % max_seconds)
    return False


def find_door_pink_rect(img=None, min_w=60, max_w=1600):
    """★★★ 2026-11-15 作者监工点出的正解：**粉色竖矩形 + 位置校验**。

    实测（用作者给的已知画面标定，真门中心 ≈(1896,720)）：
        粉色阈值 (145,60,140)-(172,255,255) → 取最大连通域
        w=209 h=316 中心(1887,794) ⇒ 与真门只差 83 px ✓
    而模板匹配 0.63 分却匹配到了**角色脚下**（误报 ✗）⇒ 模板不能排第一。

    判据（全部满足）：
      * 粉色连通域，宽 60~1600，宽高比 0.9~3.2（竖长方形）
      * 面积 ≥ 2000
      * **中心在画面上方 85% 之内**（排除地面/脚下）
      * 粉色填充率 ≥ 0.45（是实心门板，不是零散霓虹）
    返回 {'cx','cy','w','h','score','src':'pinkrect'} 或 None。
    """
    import cv2
    import numpy as _np
    if img is None:
        img = screenshot()
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    best = None
    for lo, hi in (((145, 60, 140), (172, 255, 255)),
                   ((135, 30, 100), (180, 235, 255))):
        mask = cv2.inRange(hsv, lo, hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _np.ones((9, 9), _np.uint8))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w < min_w or w > max_w:
                continue
            ratio = h / float(max(1, w))
            if ratio < 0.9 or ratio > 3.2:
                continue
            area = cv2.contourArea(c)
            if area < 2000:
                continue
            fill = area / float(w * h)
            # ★ 实测（真门 w=209 h=316 area=21120 ⇒ fill=0.32）⇒ 阈值 0.45 会把真门漏掉 ✗
            if fill < 0.25:
                continue
            cx, cy = x + w // 2, y + h // 2
            if cy > H * 0.85:            # 排除地面/脚下
                continue
            score = float(area) / 1000.0 + fill
            # ★★★ 2026-11-15 作者：「我明明看着门在前方，但它就是走错路」
            #   原因：多候选取"面积最大" ⇒ 挑到左边远处一个粉色物体（偏-1606 宽104），
            #   而不是正前方那扇门 ✗  ⇒ 改成**优先离屏幕中心最近**（|=偏| 最小），
            #   同样偏心的再比面积。
            _off = abs(cx - W // 2)
            cand = {'cx': cx, 'cy': cy, 'w': w, 'h': h,
                    'score': score, 'src': 'pinkrect', '_area': area, '_off': _off}
            if best is None:
                best = cand
            else:
                bo = best.get('_off', 10 ** 9)
                # 明显更靠中心（差 300px 以上）就换；否则面积大的赢
                if _off + 300 < bo or (_off <= bo + 300 and area > best.get('_area', 0)):
                    best = cand
    if best is not None:
        best.pop('_area', None)
        best.pop('_off', None)
    return best


def door_eyes_state(img, d, verbose=False):
    """★★★ 判门是**睁眼还是闭眼**（作者：「现在门是闭眼的哦，有没有识别出来」）。

    实测特征（用作者给的样本标定）：
        睁眼：门板上有一对**圆度≥0.72 的对称白圆**（实测圆度 0.95/0.91、半径相同、y 相同）
        闭眼：眼睛变成**粉色圆 + 一道黑横线**，门板上**没有**对称白圆（圆度最高仅 0.56）
    VLM（qwen3-vl）在本机不可用（返回空），所以用这个几何判据。
    返回 'open' / 'closed' / 'unknown'。
    """
    import cv2
    try:
        w = int(d.get('w') or 0)
        h = int(d.get('h') or (w * 1.5))
        if w < 40:
            return 'unknown'
        cx, cy = int(d['cx']), int(d['cy'])
        x = max(0, cx - w // 2); y = max(0, cy - h // 2)
        roi = img[y:y + h, x:x + w]
        if roi.size == 0:
            return 'unknown'
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        cands = []
        for thr in (150, 170, 190):
            _, th = cv2.threshold(gray, thr, 255, cv2.THRESH_BINARY)
            cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                a = cv2.contourArea(c)
                if a < 50:
                    continue
                (ex, ey), r = cv2.minEnclosingCircle(c)
                if r < w * 0.035:
                    continue
                circ = a / max(1.0, 3.14159 * r * r)
                frac = a / float(w * h)
                if circ >= 0.72 and 0.006 <= frac <= 0.10:
                    cands.append((ex, ey, r, circ))
        for i in range(len(cands)):
            for j in range(i + 1, len(cands)):
                a, b = cands[i], cands[j]
                dx = abs(a[0] - b[0]); dy = abs(a[1] - b[1])
                rr = max(a[2], b[2]) / max(1.0, min(a[2], b[2]))
                if 0.25 * w <= dx <= 0.85 * w and dy <= 0.10 * h and rr <= 1.6:
                    if verbose:
                        log('    · 门眼判定：一对对称白圆 → **睁眼**')
                    return 'open'
        if verbose:
            log('    · 门眼判定：没有对称白圆 → **闭眼**')
        return 'closed'
    except Exception:
        return 'unknown'


# ★★★ 2026-11-16 卡死熔断器（离线补强）
#   昨晚四类卡点都表现为"同一个动作无限重试"：
#     ① 暂停面板打不开（连按 ESC 无效）② 站点卡认不出（连点"确定"无效）
#     ③ 找门失败后原地打转        ④ 战斗假结束（UI 里出现战斗字样）
#   统一用计数熔断：同一动作失败超过 limit 次 ⇒ 触发升级处理（换手段/跳过/求重置），
#   而不是继续无意义地循环。
_STUCK = {}


def stuck_tick(key, limit=3, verbose=True):
    """记一次失败；超过 limit 返回 True（调用方应换手段）。"""
    n = _STUCK.get(key, 0) + 1
    _STUCK[key] = n
    if n > limit:
        if verbose:
            try:
                log('    · [熔断] %s 已连续失败 %d 次 ⇒ 换手段' % (key, n))
            except Exception:
                pass
        return True
    return False


def stuck_reset(key):
    _STUCK.pop(key, None)


# ★★★ 2026-11-16 门方位记忆（离线补强，昨晚"打完仗找不到门"的真凶之一）
#   差分宇宙里打怪是**朝前走过去**的，打完站在怪堆位置 ⇒ 门在**身后**；
#   而整圈 600px 大步全景又会转过头。正确做法：记住门最后一次出现的方位，
#   打完先按记忆转回去，再"走一步看一步"地退回，最后才小角度扫场。
_DOOR_MEM = {'off': None, 'w': None, 'ts': 0.0}


def remember_door(d):
    """每次识别到门就记下方位（屏幕偏移像素）与宽度、时间。"""
    try:
        if d is not None:
            _DOOR_MEM.update(off=int(d['cx']) - 1920, w=int(d.get('w') or 0), ts=time.time())
    except Exception:
        pass


def recover_door_after_battle(max_walk=8, verbose=True):
    """战斗结束后找门：① 按记忆方位转回去 ② 走一步看一步地退回 ③ 小角度扫场。

    返回门的 dict 或 None。**不做大角度全景**（实测 600px×12 步会把方向彻底转乱）。
    """
    import pyautogui as _pg
    if verbose:
        log('    · [战后找门] 记忆方位=%r 宽=%r'
            % (_DOOR_MEM.get('off'), _DOOR_MEM.get('w')))
    # ① 按记忆方位转回去（方位是相对屏幕中心的偏移像素 / 增益 1.49）
    off = _DOOR_MEM.get('off')
    if off is not None and abs(off) > 40:
        mx = int(max(-1200, min(1200, off / 1.49)))
        if mx:
            if verbose:
                log('    · 按记忆转回门的方向 %+d px' % mx)
            try:
                _send_input_move(mx, 0)
                time.sleep(0.5)
            except Exception:
                pass
    d = door_probe_inplace(verbose=False)
    if d is not None:
        remember_door(d)
        if verbose:
            log('    · 转回后原地就看到门（宽%d 偏%+d 门眼=%s）'
                % (d['w'], d['cx'] - 1920, d.get('eyes', '?')))
        return d
    # ② 走一步看一步地退回（门在身后，往回走最容易看到）
    for i in range(max_walk):
        check_pause('战后走回')
        try:
            _pg.keyDown('w')
            time.sleep(1.0)
            _pg.keyUp('w')
        except Exception:
            try:
                _pg.keyUp('w')
            except Exception:
                pass
        time.sleep(0.25)
        d = door_probe_inplace(verbose=False)
        if d is not None:
            remember_door(d)
            if verbose:
                log('    · 退回第%d步看到门（宽%d 偏%+d 门眼=%s）'
                    % (i + 1, d['w'], d['cx'] - 1920, d.get('eyes', '?')))
            return d
        # 每 3 步小角度扫一眼（左右各 30°），防止走廊拐弯
        if (i + 1) % 3 == 0:
            for ang in (200, -400, 200):
                try:
                    _send_input_move(ang, 0)
                    time.sleep(0.25)
                except Exception:
                    pass
                d = door_probe_inplace(verbose=False)
                if d is not None:
                    remember_door(d)
                    if verbose:
                        log('    · 扫视时看到门（宽%d 偏%+d）' % (d['w'], d['cx'] - 1920))
                    return d
    if verbose:
        log('    · [战后找门] 退回 %d 步仍未看到门' % max_walk)
    return None


def door_probe_inplace(img=None, verbose=True):
    """★★★ 2026-11-15 作者反复强调的核心：**进入区域后先"原地判定"门**。

    作者原话：「进入区域后要**先判定门**嘛，我都告诉你门在正前方了」。
    所以这一步**不许转视角、不许移动**，就在当前这一帧上判定：

        ① 屏幕上有没有 `[F] 随意门` 提示（最可靠，OCR 文字）
        ② SIFT 特征匹配（内点 ≥12）
        ③ 模板匹配（分 ≥0.55，兜底）
        ④ 颜色法（分 ≥0.60 且宽度合理，最后兜底）

    任一命中 ⇒ 返回门的字典；全都不命中 ⇒ None（这时才允许"转视角找"）。
    """
    if img is None:
        img = screenshot()
    # ① F 提示「随意门」
    try:
        ok_p, txt_p = door_prompt_present(img)
        if ok_p:
            if verbose:
                log('    · 原地判定：看到提示「%s」⇒ 门就在面前' % txt_p)
            return {'cx': 1920, 'cy': 1080, 'w': 420, 'h': 620, 'score': 1.0,
                    'src': 'prompt:%s' % txt_p}
    except Exception as e:
        if verbose:
            log('    · 原地判定：提示读取异常 %r' % (e,))
    # ② 粉色竖矩形（实测与真门差 83px，最可靠的非文字判据）
    try:
        r = find_door_pink_rect(img)
        if r is not None:
            r['eyes'] = door_eyes_state(img, r)
            remember_door(r)
            if verbose:
                log('    · 原地判定：粉色矩形命中（宽%d 偏%+d 中心(%d,%d)）'
                    % (r['w'], r['cx'] - 1920, r['cx'], r['cy']))
            return r
    except Exception as e:
        if verbose:
            log('    · 原地判定：粉色矩形异常 %r' % (e,))
    # ③ SIFT
    try:
        d = find_door_by_features(img)
        if d is not None and 100 <= int(d['w']) <= 2200:
            if verbose:
                log('    · 原地判定：SIFT 命中（宽%d 偏%+d 内点%d）'
                    % (d['w'], d['cx'] - 1920, d.get('score', 0)))
            return d
    except Exception as e:
        if verbose:
            log('    · 原地判定：SIFT 异常 %r' % (e,))
    # ③ 模板
    try:
        t = find_door_by_tpl(min_score=0.75)   # ★ 0.63 会误报到脚下 ⇒ 提到 0.75
        if t is not None and 100 <= int(t['w']) <= 2200:
            if verbose:
                log('    · 原地判定：模板命中（宽%d 偏%+d 分%.2f）'
                    % (t['w'], t['cx'] - 1920, t.get('score', 0)))
            return t
    except Exception:
        pass
    # ④ 颜色
    try:
        c = find_door_pink()
        if c is not None and 100 <= int(c['w']) <= 2200 and float(c.get('score', 0) or 0) >= 0.60:
            if verbose:
                log('    · 原地判定：颜色法命中（宽%d 偏%+d 分%.2f）'
                    % (c['w'], c['cx'] - 1920, c.get('score', 0)))
            return c
    except Exception:
        pass
    if verbose:
        log('    · 原地判定：**四项都没命中**（门不在当前视野）')
    return None


def clear_area_v4(h=None, max_area_seconds=V4_AREA_BUDGET, _reset_retry=False):
    """★ 严格按设计文档的流程（见上方注释）。"""
    t_all = time.time()
    try:
        hud0 = hud_progress()
    except Exception:
        hud0 = None
    log('    【v4 按文档】进度号 %s；预算 %d 秒' % (hud0, max_area_seconds))

    for rnd in (1, 2, 3):
        if time.time() - t_all > max_area_seconds:
            log('    · 单区超预算')
            break
        beat('v4圈')
        log('    ── v4 第 %d/3 圈 ──' % rnd)
        if rnd == 1:
            capture_material(tag='area')      # 攒训练素材（零打扰，只存图）

        # ---------- ① 找门 ----------
        d = None
        if rnd == 1:
            # ★★★ 作者要求：**进区域先原地判定**（不转视角、不移动）
            log('    · ① 原地判定门（不转视角、不移动）')
            try:
                d = door_probe_inplace()
            except Exception as e:
                log('    · 原地判定异常: %s' % e)
                d = None
        if d is None and rnd > 1:
            # ★ 2026-11-15：打完仗后角色是**朝前走过去的**，门在身后 ⇒
            #   先转约 180° 再往前走找回门（比整圈全景更符合空间直觉）。
            # ★ 2026-11-16 改为调用统一恢复函数：按记忆方位转回 → 走一步看一步退 →
            #   每 3 步小角度扫视（不再用 600px×12 的大角度全景，实测定向会彻底转乱）
            try:
                d = recover_door_after_battle(max_walk=8)
            except Exception as e:
                log('    · 战后找门异常: %s' % e)
                d = None
        if d is None:
            # 兜底：小角度（300px≈20°）逐步扫，最多 6 步，不再整圈
            try:
                d = _door_behind(steps=6, per_step_px=300)
            except Exception as e:
                log('    · 回头找门异常: %s' % e)
        if d is None:
            try:
                d = _door_ahead()
            except Exception:
                d = None

        # ★ 作者纠正：看不到门（或后面居中失败）就**整圈全景**找，不再小范围磨
        if d is None:
            log('    · 四面都没有门 → **整圈全景扫一圈**')
            bearing, cur = _v4_pano_scan_door()
            if bearing is not None:
                _v4_turn_to_bearing(bearing, cur)
                hold_w(2.2)
                time.sleep(0.3)
                if _v2_progressed(hud0):
                    log('    · ✓ 全景找到门并走过去了')
                    return True

        if d is not None:
            off = d['cx'] - 1920
            log('    · ① 看到门：偏%+d 宽%d（门眼=%s）' % (off, d['w'], d.get('eyes', '?')))
            if d.get('eyes') == 'closed':
                log('    · 门是**闭眼**（锁着）⇒ 先去清区域（战斗类就开秘技打怪），别白走去门')
                try:
                    _k = area_kind() or ''
                except Exception:
                    _k = ''
                _v4_clear_by_kind(_k, budget=90)
                if _v2_progressed(hud0):
                    return True
                continue
            # ★ 2026-11-15 按文档：**门闭眼（锁着）= 还有东西要交互**
            #   （作者：「现在门是闭眼」⇒ 应该先去打首领，而不是硬走门）
            try:
                _eyes = vlm_door_eyes(d)
            except Exception:
                _eyes = None
            log('    · 门状态: %s' % (_eyes or '判不出'))
            if _eyes == 'closed':
                log('    · 门是**闭眼/锁着** ⇒ 按文档先去清区域（战斗类就开秘技打怪）')
                try:
                    kind0 = area_kind()
                except Exception:
                    kind0 = ''
                _v4_clear_by_kind(kind0, budget=80)
                if _v2_progressed(hud0):
                    return True
                continue

            if walk_to_door_v6(hud0):
                return True
            if _ENEMY_INTERRUPT[0]:
                _ENEMY_INTERRUPT[0] = False
                log('    · 走位被怪卡中断 ⇒ 先打怪（秘技已放）')
                try:
                    _k2 = area_kind() or '战斗'
                except Exception:
                    _k2 = '战斗'
                _v4_clear_by_kind(_k2, budget=90)
                if _v2_progressed(hud0):
                    return True
                continue
            log('    · 这一圈没走过门 → 按文档处理区域内容（打怪/交互）再回来')

        # ---------- ② 按区域类型处理 ----------
        try:
            kind = area_kind()
        except Exception:
            kind = ''
        log('    · ② 区域类型=%r' % (kind,))
        _v4_clear_by_kind(kind, budget=80)
        if _v2_progressed(hud0):
            return True
        if _v2_idle_check('v4第%d圈' % rnd):
            log('    · 原地不动 → 提前放弃')
            break

    # ---------- ④ 重置位置（文档兜底：回起点，正前方就是随意门）----------
    log('    · ④ 多次没成就 **重置位置**（暂离→重进→继续进度）')
    try:
        _h = h or find_game()
        if _h and leave_and_rejoin(_h):
            log('    · ✓ 已重置位置（角色回起点，正前方就是门）')
            if not _reset_retry:
                # ★ 重置 ≠ 过门！原来 return True 让调用方以为"门开了"→ 去点站点卡 ✗
                log('    · 重置后**重新走一遍门**')
                return clear_area_v4(h, max_area_seconds, _reset_retry=True)
            log('    · 重置过一次仍没成 → 放弃本区')
            return False
    except Exception as e:
        log('    · 重置出错: %s' % e)
    return False


def clear_area_v3(h=None, max_area_seconds=90):
    t_all = time.time()
    try:
        hud0 = hud_progress()
    except Exception:
        hud0 = None
    log('    【v3 笨办法】起点进度号 %s；规则：门在180°、无 F 标签不交互、3 次不成→重置' % (hud0,))

    for attempt in (1, 2, 3):
        if time.time() - t_all > max_area_seconds:
            log('    · 超预算 %d 秒' % max_area_seconds)
            break
        log('    ── v3 第 %d/3 次 ──' % attempt)
        beat('v3')

        # ① 转 180°（门在身后）
        _v3_turn(DEG180_PX)
        beat('v3-转完')
        time.sleep(0.25)

        if _v2_progressed(hud0):
            return True

        # ② 看门
        d = None
        try:
            d = _door_ahead()
        except Exception:
            d = None
        if d is not None:
            off = d['cx'] - 1920
            log('    · 看到门：偏%+d 宽%d → 转向 + 直走 2.5 秒' % (off, d['w']))
            try:
                steer(off)
            except Exception:
                pass
            hold_w(2.5)
            time.sleep(0.3)
            if _v2_progressed(hud0):
                log('    · ✓ 过了（站点卡/进度号变化）')
                return True
            # 到门口仍然有 F 提示（且不是黑名单）才按
            try:
                if press_f_checked(settle=1.2):
                    time.sleep(1.2)
                    if _v2_progressed(hud0):
                        return True
            except Exception:
                pass
        else:
            log('    · 没看到门 → 走两步 + 转 60°（不按 F：没有标签就是没交互）')
            hold_w(1.5)
            _v3_turn(DEG60_PX)
            if _v2_progressed(hud0):
                return True

        # ③ 静止检测（发呆就不许继续）
        if _v2_idle_check('v3 第%d次' % attempt):
            log('    · 原地不动 → 提前放弃，交给重置')
            break

    log('    · v3 三次都没成 → **重置位置**（暂离重进）')
    try:
        _h = h or find_game()
        if _h and leave_and_rejoin(_h):
            log('    · ✓ 已重置位置')
            return True
    except Exception as e:
        log('    · 重置出错: %s' % e)
    return False


def clear_area_v2(h=None, max_area_seconds=180):
    """★ 2026-11-15 作者要求的**极简流程**（重写版）。

    ① 回头找门（门在 180°）→ 有门就转向 + 直走；
    ② 走不通 → 清挡路的东西（怪/装置）；
    ③ 还不行 → 全景找一次（一次就对得上方位）；
    ④ 单区超时 → **重置位置**（暂离重进）。
    """
    t_all = time.time()
    try:
        hud0 = hud_progress()
    except Exception:
        hud0 = None
    log('    【v2 极简流程】起点进度号: %s，单区预算 %d 秒' % (hud0, max_area_seconds))
    if _FORCE_RESET[0]:
        _FORCE_RESET[0] = False
        log('    · 看门狗已请求重置 → 直接暂离重进')
        try:
            _h0 = h or find_game()
            if _h0 and leave_and_rejoin(_h0):
                return True
        except Exception as _e:
            log('    · 重置出错: %s' % _e)
        return False
    if _v2_progressed(hud0):
        return True

    for rnd in (1, 2, 3):
        # ★ 作者规则：**3 次没找到就重置位置**；出现原地发愣也立刻重置
        if time.time() - t_all > max_area_seconds:
            log('    · 已到单区预算 %d 秒' % max_area_seconds)
            break
        log('    ── v2 第 %d/3 圈 ──' % rnd)
        if _v2_idle_check('第%d圈开始' % rnd):
            break

        # ① 回头找门
        d = None
        try:
            d = _door_behind(steps=12, per_step_px=600)
        except Exception as e:
            log('    · 回头找门异常: %s' % e)
        if d is None:
            try:
                d = _door_ahead()
            except Exception:
                d = None
        if d is not None:
            off = d['cx'] - 1920
            log('    · ① 门：偏%+d 宽%d → 转向 + 直走' % (off, d['w']))
            steer(off)
            hold_w(2.0)
            time.sleep(0.4)
            if _v2_progressed(hud0):
                log('    · ✓ 过了（进度号/站点卡变化）')
                return True
            if press_f_checked(settle=1.2):
                time.sleep(1.2)
                if _v2_progressed(hud0):
                    return True
        else:
            log('    · ① 回头也看不到门')

        # ② 清挡路
        try:
            kind = area_kind()
        except Exception:
            kind = ''
        _v2_clear_blockers(kind, budget=75)
        if _v2_progressed(hud0):
            return True

        if _v2_idle_check('第%d圈动作后' % rnd):
            break

        # ③ 全景
        log('    · ③ 全景找门')
        try:
            r = pano_once('v2找门')
            if r is None:                     # 全景次数到上限
                log('    · 全景次数超限 → 直接重置')
                break
            time.sleep(0.5)
            if _v2_progressed(hud0):
                return True
        except Exception as e:
            log('    · 全景异常: %s' % e)

    # ④ 超时/没进展 → 重置位置
    log('    · ④ 单区 %.0f 秒没进展 → **重置位置**（暂离重进）' % (time.time() - t_all))
    try:
        _h = h or find_game()
        if _h and leave_and_rejoin(_h):
            log('    · ✓ 已重置位置')
            return True
    except Exception as e:
        log('    · 重置出错: %s' % e)
    return False


def clear_area(h=None, tries=3, walk_seconds=60, roam_seconds=90):
    """单区域主流程（**v3，作者 2026-11-10 定案**）。

    ```
    ① 进入区域只看**正前方**找门（不转视角）
         找到门且睁眼 → ② 朝门走（闭环；遇障碍侧移绕）
         找不到 / 闭眼 → ③ 判左上角区域类型
              战斗/精英/首领 → 开秘技打怪
              事件等         → 把正前方的可交互物逐个交互掉
         ③ 做完 → 回 ①（此时**允许转向、允许全景**）
    ```
    最多 3 轮 / 5 分钟（2026-11-11 作者审批），超时就交给重置。返回 True = 出现选站点卡。
    """
    if USE_CLEAR_V2:
        # ★ 2026-11-15：v4 = 严格照设计文档（找门→居中→走；没有门/闭眼→判区域→秘技打怪/交互）
        return clear_area_v4(h)
    t_all = time.time()
    _hud0 = hud_progress()
    log('      · 起点进度号: %s' % (_hud0,))

    for rnd in range(1, 4):        # ★ 2026-11-11 作者审批：6 轮 → 3 轮
        if time.time() - t_all > 300:   # ★ 2026-11-11 作者审批：8 分钟 → 5 分钟
            # ★★ 2026-11-15 作者要求：「如果在一个区域时间太长也**重置位置**吧」
            log('      · 单区已耗时 %.0f 秒 → **重置位置**（暂离重进）' % (time.time() - t_all))
            try:
                _hh = find_game()
                if _hh and leave_and_rejoin(_hh):
                    log('      · ✓ 已重置位置')
                    return True
                log('      · 重置没成功 → 交给外层')
            except Exception as _e:
                log('      · 重置出错: %s' % _e)
            break
        if on_station_picker() or area_passed():
            return True

        log('      · ── 第 %d 轮 ──' % rnd)

        # ---------- ① 找门 ----------
        d = None
        if rnd == 1:
            # ★★ 2026-11-15 作者原话：「**门应该在 180 度方向**」
            #   实测正前方的检测**几乎全是误报**（宽 176、偏 +1120，一转就没 ⇒ 每轮白烧 90 秒 ✗）
            #   所以第 1 轮**直接回头看**（一看到门就停），不再先看正前方。
            log('      · ① 进区域 → 门在 180° 方向，**直接回头看门**')
            d = _door_behind()
            if d is None:
                log('      · 回头没看到门 → 再试一次正前方（兜底）')
                d = _door_ahead()
        else:
            log('      · ① 找门（允许转向/全景）')
            d = _door_ahead()
            if d is None:
                d = _reacquire_door()

        # ---------- ② 门睁眼 → 直接走 ----------
        if d is not None:
            off = d['cx'] - 1920
            log('      · 看到门：偏%+d 宽%d' % (off, d['w']))
            eyes = None
            try:
                eyes = vlm_door_eyes(d)
            except Exception:
                eyes = None
            log('      · VLM 判门状态: %s' % (eyes or '判不出（按睁眼处理）'))
            if eyes != 'closed':
                if approach_door_servo(max_seconds=90):
                    log('      · ✓ 已过门')
                    return True
                log('      · 走门没成功 → 继续判区域类型')
            else:
                log('      · 门是**闭眼**（锁着）→ 还有东西要交互')

        # ---------- ③ 判区域类型 ----------
        kind = ''
        try:
            kind = area_kind()
        except Exception:
            kind = ''
        log('      · ③ 区域类型=%r' % kind)

        if kind in BATTLE_KINDS:
            log('      · 战斗类(%s) → 开秘技打怪' % kind)
            if hunt_enemies(max_seconds=180):
                if on_station_picker() or area_passed() or hud_progress() != _hud0:
                    log('      · ✓ 清怪后已推进')
                    return True
            log('      · 清怪没成 → 下一轮再找门')
            continue

        # 事件/其它 → 交互掉能看到的东西
        log('      · 事件类(%s) → 把正前方的可交互物交互掉' % (kind or '?'))
        did = _interact_labels_once()
        if on_station_picker() or area_passed() or hud_progress() != _hud0:
            log('      · ✓ 交互后已推进')
            return True
        if not did:
            log('      · 没找到可交互物 → 这一轮没进展')

    # ---------- 收尾 ----------
    log('      · 清场结束（进度号 %s → %s）' % (_hud0, hud_progress()))
    return on_station_picker() or area_passed()


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


# ★★★ 2026-11-15 作者：「**你为什么会点鼠标左键呢**」
#   —— 因为星铁世界里左键 = 攻击/位移，而脚本有 17 处"OCR 失败就点固定坐标"的兜底，
#   一旦落在大世界画面上，角色就会自己跑/打空气。所以在这里**统一加守卫**：
#   当前是世界画面（in_area）时，默认**拒绝点击**；确实需要点世界时显式 allow_world=True。
_WORLD_STATES = ('in_area',)


def release_all_keys(why=''):
    """★★★ 2026-11-15 作者：「**人物为什么还在往前走**」——脚本被强杀时
    `keyDown('w')` 没配对的 keyUp，游戏就一直认为 W 按着，角色自己走 ✗

    ⇒ 这条函数在任何"可能退出/被打断"的地方都要调：
      启动时、正常退出时、异常退出时（try/finally）、atexit。
    """
    import pyautogui as _pg
    keys = ('w', 'a', 's', 'd', 'e', 'f', 'space', 'shift', 'tab')
    for k in keys:
        try:
            _pg.keyUp(k)
        except Exception:
            pass
    # SendInput 扫码 KEYUP 补一遍（部分游戏只认这个）
    try:
        _sc = {'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20, 'e': 0x12, 'f': 0x21}
        for k, sc in _sc.items():
            inp = _INPUT()
            inp.type = 1
            inp.u.ki = _KEYBDINPUT(0, sc, KEYEVENTF_KEYUP | KEYEVENTF_SCANCODE, 0, None)
            _ct.windll.user32.SendInput(1, _ct.byref(inp), _ct.sizeof(_INPUT))
    except Exception:
        pass
    if why:
        try:
            log('  · 已释放所有按键（%s）' % why)
        except Exception:
            pass


CAPTURE_DIR = r"D:\_PROGRAM_PythonProjects\Learn\debug_shots\event_frames\live"
_CAP_N = [0]


def capture_material(img=None, tag='', force=False):
    """★★★ 2026-11-15：进新区域时存一张全分辨率截图当**训练素材**（零打扰）。

    只在进区域/首个 v4 圈各存一次；文件名纯 ASCII（cv2.imwrite 对中文路径会静默失败，
    所以用 imencode+tofile）。素材攒够后由 event_frame_ds 的标注+训练流程消费。
    """
    try:
        import os as _os
        import cv2 as _cv2
        if not force and _CAP_N[0] >= 200:
            return
        _os.makedirs(CAPTURE_DIR, exist_ok=True)
        if img is None:
            img = screenshot()
        _CAP_N[0] += 1
        fn = _os.path.join(CAPTURE_DIR, "live_%03d%s.png" % (_CAP_N[0], ('_' + tag) if tag else ''))
        ok, buf = _cv2.imencode('.png', img)
        if ok:
            buf.tofile(fn)
    except Exception:
        pass


def click_phys(x, y, hover=0.5, settle=0.5, guard_world=False):
    """平滑移动到物理坐标再点 ✓（一次性 SetCursorPos 会被吞 ✗）。

    ★★★ 2026-11-15 作者：「**你为什么会点鼠标左键呢**」
    星铁世界里左键 = **攻击/位移**，而脚本有十几处"OCR 失败就点固定坐标"的兜底，
    一旦落在大世界画面上，角色就会自己跑或打空气 ✗
    ⇒ 这里统一守卫：当前是 `in_area`（世界画面）时**拒绝点击**；
       确实需要点世界时调用方显式传 `allow_world=True`。
    """
    # 只有**盲点兜底**那几处才传 guard_world=True（世界画面里点固定坐标会把角色打跑）
    if guard_world:
        try:
            _st = detect_state(verbose=False)[0]
        except Exception:
            _st = None
        if _st == 'in_area':
            log('      · [点击守卫] 世界画面 + 盲点坐标 ⇒ 拒绝点击 (%d,%d)' % (int(x), int(y)))
            return False
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


_E_USED = [None]      # 记录"上一次放秘技时所在的区域"
# ★★ 2026-11-15 作者指出：「这个区域不是都放过一次了吗」 —— 原来 key 用的是 area_kind()，
#   它 OCR 失败时会在 '首领' 和 '' 之间跳 ⇒ key 变了 ⇒ "只放一次"失效 ⇒ **重复放秘技** ✗
#   改成**稳的区域序号**：只有 reset_technique_flag()（进新区域）才 +1。
_AREA_SEQ = [0]


def reset_technique_flag():
    """进入新区域/新一趟时调用：允许再放一次秘技。"""
    _AREA_SEQ[0] += 1
    _E_USED[0] = None
    log('      · 新区域序号 %d → 允许再放一次秘技' % _AREA_SEQ[0])


# ★★★ 2026-11-15 作者：「怎么不放秘技捏」——日志说放了，但画面没反应。
#   根因：press() 走 pyautogui(keybd_event)，而本机实测**星铁只吃 SendInput 扫描码**
#   （鼠标那边早就证实过：mouse_event 被吞）。所以键盘也改用 SendInput。
# ★★★ 2026-11-15 修：原来把 ctypes 结构体定义在函数内部、还用 INPUT._I(...) 构造，
#   但 _I 没挂到类上 ⇒ 每次 AttributeError ⇒ send_key_sendinput 恒返回 False
#   ⇒ **E 键从来没发出去**（作者："为什么连秘技都没有放"）。现在改为模块级结构体。
import ctypes as _ct


class _KEYBDINPUT(_ct.Structure):
    _fields_ = [("wVk", _ct.c_ushort), ("wScan", _ct.c_ushort),
                ("dwFlags", _ct.c_ulong), ("time", _ct.c_ulong),
                ("dwExtraInfo", _ct.POINTER(_ct.c_ulong))]


class _INPUTUNION(_ct.Union):
    _fields_ = [("ki", _KEYBDINPUT)]


class _INPUT(_ct.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", _ct.c_ulong), ("u", _INPUTUNION)]


_KEY_SCAN = {'e': 0x12, 'f': 0x21, 'w': 0x11, 'a': 0x1E, 's': 0x1F, 'd': 0x20,
             'q': 0x10, 'r': 0x13, 'space': 0x39, 'esc': 0x01, 'tab': 0x0F,
             '1': 0x02, '2': 0x03, '3': 0x04, '4': 0x05}

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


def send_key_sendinput(key, hold=0.09):
    """用 SendInput + 扫描码按一次键（星铁只认这个）。返回 True/False。"""
    try:
        sc = _KEY_SCAN.get(str(key).lower())
        if sc is None:
            return False
        user32 = _ct.windll.user32
        down = _INPUT()
        down.type = 1                      # INPUT_KEYBOARD
        down.u.ki = _KEYBDINPUT(0, sc, KEYEVENTF_SCANCODE, 0, None)
        up = _INPUT()
        up.type = 1
        up.u.ki = _KEYBDINPUT(0, sc, KEYEVENTF_KEYUP | KEYEVENTF_SCANCODE, 0, None)
        n1 = user32.SendInput(1, _ct.byref(down), _ct.sizeof(_INPUT))
        time.sleep(max(0.03, hold))
        n2 = user32.SendInput(1, _ct.byref(up), _ct.sizeof(_INPUT))
        return bool(n1 == 1 and n2 == 1)
    except Exception as e:
        log('      · send_key_sendinput 异常: %s' % e)
        return False


def _skill_icon_sig(img=None):
    """右下角秘技图标区域的指纹（用来验证 E 到底有没有生效）。"""
    import cv2
    try:
        if img is None:
            img = screenshot()
        H, W = img.shape[:2]
        roi = img[int(H * 0.68):int(H * 0.86), int(W * 0.80):int(W * 0.95)]
        if roi.size == 0:
            return None
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        return float(g.mean()), float(g.std())
    except Exception:
        return None


def use_technique_verified(max_try=3):
    """放秘技 + **验证真的生效**（右下角图标变化）。返回 True=确认生效。

    ★★★ 2026-11-15 实测反证（作者盯着屏幕说"连秘技都没有放"）：
        SendInput(扫描码) → 游戏**收不到**（返回 False，画面只差 3.00）
        pyautogui.press('e') → **有效**（画面差 43.25，脚下出现秘技范围圈、
                                        右下角图标变「下落」、顶部提示「△指令-焦土陨击」）
    ⇒ 键鼠注入方式相反：**鼠标必须 SendInput，键盘必须用 pyautogui**。
    """
    sig0 = _skill_icon_sig()
    for i in range(max_try):
        try:
            pyautogui.press('e')
            ok = True
        except Exception as e:
            ok = False
            log('      · pyautogui 按 E 失败: %s' % e)
        log('      · 放秘技(E) 第%d次 via pyautogui（ok=%r）' % (i + 1, ok))
        # ★ 2026-11-15 作者：「流萤秘技浮空时间很短」⇒ 只等 0.30 秒就去看效果
        time.sleep(0.30)
        sig1 = _skill_icon_sig()
        if sig0 is not None and sig1 is not None:
            d = abs(sig1[0] - sig0[0]) + abs(sig1[1] - sig0[1])
            log('      · 秘技图标区域变化量 %.2f（实测有效时约 26，阈值 4.0）' % d)
            if d > 4.0:
                return True
        else:
            # 拿不到指纹时按"按键已发出"算，但日志标出来
            if i == max_try - 1:
                log('      · 无法取到图标指纹，按已发送处理')
                return True
    return False


def use_technique():
    """放秘技——**作者 2026-11-09 硬规则：同一区域只放一次**。

    「以后战斗或者精英或者首领场景只放一次秘技，没打到怪只能说明没覆盖到怪物，直接联系我」

    ⚠ 之前代码里有 7 处 press('e')，包括「战斗后再放一次」⇒ 白耗秘技点 ✗
    现在统一走这里：同一区域第二次调用直接拒绝。
    返回 True = 真的按了；False = 本区域已用过，跳过。
    """
    # ★ 稳定 key（不再用 area_kind，它会抖）
    key = _AREA_SEQ[0]
    if _E_USED[0] == key:
        log('      · 本区域秘技已放过一次（作者规则）→ 不再重复放')
        return False
    log('      · 放秘技（E）— 本区域仅此一次')
    try:
        _oktech = use_technique_verified()
    except Exception as _e:
        log('      · 验证放秘技出错: %s' % _e)
        _oktech = send_key_sendinput('e', hold=0.09)
    if not _oktech:
        log('      · ⚠ 秘技未能确认生效（三次都没看到图标变化）')
    _E_USED[0] = key
    return bool(_oktech)


PAUSE_FILE = r"D:\_TOOL_DSH\workbench\div_uni.pause"


def check_pause(tag=''):
    """**可暂停**（作者 2026-11-09 要求）：插队插件写 PAUSE_FILE ⇒ 脚本原地挂起。

    设计要点：
      * **不杀进程、不丢进度** —— 只是松开所有按键、原地等；
      * 文件被删掉后自动继续；
      * 在每一步/每个循环里调用（开销极小：一次 os.path.exists）。
    """
    if not os.path.exists(PAUSE_FILE):
        return False
    log('      ⏸ 检测到暂停文件 → 松开按键，原地挂起%s' % (('（' + tag + '）') if tag else ''))
    try:
        for k in ('w', 'a', 's', 'd'):
            pyautogui.keyUp(k)
    except Exception:
        pass
    _waited = 0
    while os.path.exists(PAUSE_FILE):
        time.sleep(1.0)
        _waited += 1
        if _waited % 30 == 0:
            log('      ⏸ 仍在暂停中（已等 %d 秒）…' % _waited)
    log('      ▶ 暂停文件已移除 → 继续')
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


# ★★ 2026-11-15 作者给的进场路径（**不需要在大世界找终端**）：
#     路径 A：ESC →「指南」→ 顶部**星球图标**（旷宇纷争）→「差分宇宙」→「前往参与」
#              → 差分宇宙主界面「开始游戏」→（有「继续进度」就点它）
#     路径 B：按 **F1** 直接打开指南（与 A 的 ESC→指南 等价）
#   两条实测都通；下面按 OCR 找文字点，找不到才退回固定坐标。
GUIDE_PLANET_XY = (1200, 420)      # 指南顶部第三个图标（星球/土星形，带红点）

def enter_via_guide():
    """走「指南→星球图标→差分宇宙」进场。返回 True = 已经进局/进了大厅。"""
    log('      · 走「指南」路径进场（ESC → 指南 → 星球 → 差分宇宙）')

    def _click(kw, timeout_tries=4):
        # ★ 2026-11-15 修正：ocr_find 返回 **物理坐标 (x, y)**，不是 (文字,x,y) ✗
        p = ocr_find(kw, tries=timeout_tries, interval=0.9)
        if not p:
            return False
        cx, cy = int(p[0]), int(p[1])
        log('        点「%s」(%d,%d)' % (kw, cx, cy))
        click_phys(cx, cy, settle=1.8)
        try:
            beat('guide-click')
        except Exception:
            pass
        return True

    # ① 打开手机菜单（ESC 连按，最多 3 次；已经在菜单里就不按）
    st = detect_state(verbose=False)[0]
    for _ in range(3):
        if st == 'phone_menu':
            break
        press('esc', 1.0)
        time.sleep(1.4)
        st = detect_state(verbose=False)[0]
    if st != 'phone_menu':
        # 兜底：F1 直接开指南
        log('        ESC 没到手机菜单 → 试 F1')
        press('f1', 1.2)
        time.sleep(1.6)

    # ② 指南 → 星球图标
    if _click('指南'):
        time.sleep(1.8)
    if ocr_find('旷宇纷争', tries=3, interval=0.9) is None:
        log('        点星球图标 %s' % (GUIDE_PLANET_XY,))
        click_phys(GUIDE_PLANET_XY[0], GUIDE_PLANET_XY[1], settle=1.8)
        time.sleep(1.6)

    # ③ 差分宇宙 → 前往参与
    if not _click('差分宇宙'):
        log('        ✗ 旷宇纷争里没有「差分宇宙」')
        return False
    time.sleep(1.6)
    if not _click('前往参与'):
        log('        ✗ 没找到「前往参与」（可能在别的标签页）')
        return False
    time.sleep(3.0)

    # ④ 差分宇宙主界面：继续进度 / 开始游戏 → 周期演算
    if ocr_find('继续进度', tries=3, interval=0.9):
        _click('继续进度')
    else:
        if _click('开始游戏'):
            time.sleep(2.5)
        if ocr_find('周期演算', tries=3, interval=0.9):
            _click('周期演算')
            time.sleep(1.6)
            if ocr_find('继续进度', tries=2, interval=0.9):
                _click('继续进度')
            else:
                _click('开始挑战')
    time.sleep(3.0)
    st = detect_state(verbose=False)[0]
    log('        进场后 state=%r 进度=%s' % (st, hud_progress()))
    return st in ('in_area', 'pick_station', 'pick_mask', 'event_dialog', 'roulette')


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
    if on_station_picker() or in_div_uni_area() or is_event_ui():
        if is_event_ui():
            log('      · 当前在事件全屏界面 → 先把剧情走完（不要按 ESC）')
            do_event_dialog()
        else:
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
    _guide_tried = False
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

        # ---------- 大世界：**先走「指南」路径**（2026-11-15 作者给的，稳定不依赖终端）
        #   路径：ESC → 指南 → 顶部星球图标(旷宇纷争) → 差分宇宙 → 前往参与
        #         → 开始游戏 → 周期演算/继续进度
        if is_world():
            if not _guide_tried:
                _guide_tried = True
                if enter_via_guide():
                    continue
                log('      · 指南路径没成功 → 退回按 F 试开终端')
            if f_tried < 3:
                f_tried += 1
                log('      · 大世界：按 F 试开差分宇宙终端（第%d次）' % f_tried)
                press('f', 1.6)
                time.sleep(1.2)
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
    use_technique()
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


def pick_curio(budget=25):
    """「选择奇物」：**图鉴优先**（§3.6 选奖铁律）。

    判据：卡片右上角有**书籍（图鉴）图标** = 这个奇物没拿过 → 优先选它；
    都没有就选最左边那张（强度溢出，不必纠结）。
    三张卡实测中心 x ≈ 964 / 1920 / 2872，y ≈ 1034。
    """
    import os as _os
    t0 = time.time()
    for _ in range(3):
        check_pause('选奇物')
        if time.time() - t0 > budget:
            break
        img = screenshot()
        items = ocr_items(img)
        txt = '|'.join(_texts(items))
        if '选择奇物' not in txt and '请选择奇物' not in txt:
            log('    · 奇物界面已离开')
            return True
        xs = [964, 1920, 2872]
        cx = 964
        try:
            badge = find_template(_os.path.join(TPL_DIR, 'badge_book.png'), threshold=0.60)
        except Exception:
            badge = None
        if badge is not None:
            cx = min(xs, key=lambda v: abs(v - int(badge[0])))
            log('    · 图鉴标记在 (%d,%d) → 选最近的卡 x=%d' % (badge[0], badge[1], cx))
        else:
            log('    · 三张都没图鉴标记 → 选最左一张（x=%d）' % cx)
        click_phys(cx, 1034, settle=1.2)
        time.sleep(1.2)
        # 选完点确认（实测 4.7 这个按钮叫「**确认**」，在 (3414,1941)）
        for _kw in ('确认', '确定', '任务完成', '前往'):
            if click_text(_kw, tries=2, interval=0.8):
                log('    · 已点「%s」确认奇物' % _kw)
                time.sleep(1.2)
                return True
        log('    · 没找到确认按钮（确认/确定/任务完成）→ 再试一次')
    return False


def pick_station():
    reset_technique_flag()      # 新区域：允许再放一次秘技
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
    # ★★ 2026-11-15 作者定：「**区域选战斗优先，其次精英**」
    #   （原来按"零操作优先"，战斗排在第 9 位 ⇒ 有战斗卡也去挑休整/商店 ✗）
    # 作者规则（2026-11-15）：**战斗优先，其次精英**；首领也是战斗类，必须排前面
    # （原来首领排在最后 ⇒ 有首领卡却去挑「休整」✗）
    # ★ 2026-11-15 作者第二次修订：**空白放最前面**（空白=无战斗零操作，最省时间），
    #   其后再按 战斗 > 精英 > 首领 > ... 排
    # ★ 2026-11-15 作者第三次修订：**商店放到首领后面**
    # ★ 2026-11-15 作者第四次修订：**事件、奖励依次放在商店后面**
    # ★ 2026-11-15 作者第五次修订：**财富、铸造依次放在奖励后面**
    # ★ 2026-11-16 作者第六次修订：**商店放到空白后面**
    PREF = ('空白', '商店', '战斗', '精英', '首领', '事件', '奖励', '财富', '铸造',
            '异常', '冒险', '休整')
    # ★ 2026-11-15：把**本屏实际出现的区域类型**先打出来，日志才可核对
    _found = []
    for want in PREF:
        for t, cx, cy in txts:
            if t == want or t.startswith(want):
                if want not in _found:
                    _found.append(want)
    log('      · 本屏候选项: %r   （优先级: 空白>商店>战斗>精英>首领>事件>奖励>其余）' % (_found,))
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
        _rank = PREF.index(want) + 1
        log('      · 挑「%s」（优先级第 %d 位：空白>商店>战斗>精英>首领>事件>奖励>其余）→ 点 (%d,%d)'
            % (want, _rank, cx, max(0, cy - 80)))
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
            click_phys(1920, 1200, settle=1.2, guard_world=True)
    time.sleep(1.5)
    if not click_text('确定', tries=4, interval=1.0) and \
            not click_text('确认', tries=2, interval=0.8):
        log('      · 没有「确定」文字 → 点实测位置 (3254,1872)')
        click_phys(3254, 1872, settle=1.2, guard_world=True)
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
    # ★★ 2026-11-09：**事件全屏界面也算"在局内"**。
    #   实测：事件页的 detect_state 是 in_area、in_div_uni_area=False
    #   ⇒ auto_entry 判成"不在局内"→ enter_divergent 在事件页上一直按 ESC ✗
    if is_event_ui():
        return True, 'event_dialog'
    # ★★ 2026-11-15 实测：**暂停面板里有「继续进度」，会命中 LOBBY_HINTS**
    #   ⇒ 被判成"不在局内" → auto_entry 去点「周期演算」乱走 ✗
    if st == 'pause_panel':
        return True, 'pause_panel'
    # ★ 2026-11-15：进度号读得出来（n/13）就说明已经在局内（state 可能是过渡期的 None）
    try:
        if hud_progress() is not None:
            return True, 'hud_progress'
    except Exception:
        pass
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
    detector_start()          # ★ 常驻门检测线程（多线程改造）
    try:
        release_all_keys('启动清理')
    except Exception:
        pass
    try:
        enemy_watcher_start(interval=0.5)
    except Exception as _e:
        log('      · 怪卡线程启动失败: %s' % _e)
    try:
        _idle_watchdog_start()   # ★ 发呆看门狗（作者 2026-11-15：不许原地愣着）
    except Exception as _e:
        log('      · 看门狗启动失败: %s' % _e)
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
        check_pause('主循环')
        if _PANO_LIMIT_HIT[0]:
            log('  ⚠ 本区域全景次数已达上限 → 结束整局（返回 6）')
            return 6
        log('')
        log('  === 第 %d 轮 ===' % i)

        beat('主循环')        # ★ 喂狗（作者 2026-11-15：由主循环负责喂狗）
        # ---- 先看当前在哪个界面，再决定干什么 ✓（状态机）----
        state, texts = detect_state()

        # ★ 2026-11-15 安全网：说在区域里却读不到进度号 ⇒ 多半有未识别的全屏面板盖着
        #   （实测被「造物调试台」坑过一次：对着面板 UI 转了 20 分钟找门）
        if _unknown_panel_guard(state):
            continue

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

        if state == 'roulette':
            # ★★ 2026-11-09 实测：**「剩余次数：0」时点「开始抽奖」毫无反应**，
            #   之前脚本就在这儿空点到卡死。次数为 0 要**直接关面板**。
            _rt = '|'.join(_texts(ocr_items()))
            if ('剩余次数：0' in _rt) or ('剩余次数:0' in _rt):
                _rk = roulette_area_key()
                if _rk not in _ROULETTE_DONE:
                    _ROULETTE_DONE[_rk] = True
                    log('      · 奇物大转盘：剩余次数 0 → **本区域不再打开抽奖机**（已拉黑）')
                log('      · 奇物大转盘：剩余次数 0 → 按 ESC 退出（不行就点右上角 X）')
                press('esc', 1.5)
                time.sleep(1.2)
                if roulette_topleft():
                    log('        · ESC 没退掉 → 点右上角 X (3744,129)')
                    click_phys(3744, 129, settle=1.2)
                    time.sleep(1.2)
                if roulette_topleft():
                    log('        · 还是转盘 → 再按一次 ESC')
                    press('esc', 1.5)
                    time.sleep(1.0)
                continue
            if roulette_area_key() in _ROULETTE_DONE:
                log('      · 奇物大转盘（本区域已拉黑）→ 直接 ESC 关掉')
                press('esc', 1.3)
                time.sleep(1.0)
                continue
            log('      · 奇物大转盘 → 点「开始抽奖」')
            g = ocr_find('开始抽奖', tries=4, interval=1.0)
            if g:
                click_phys(g[0], g[1], settle=1.5)
                time.sleep(4.0)          # 等转盘转完
                # 抽完通常会弹结果/确认
                for _ in range(4):
                    if not roulette_topleft():
                        log('        · 转盘已关闭')
                        break
                    gg = ocr_find('确定', tries=1) or ocr_find('确认', tries=1) or ocr_find('关闭', tries=1)
                    if gg:
                        log('        · 点 %s' % (gg,))
                        click_phys(gg[0], gg[1], settle=1.2)
                    else:
                        log('        · 按 ESC 退出转盘')
                        press('esc', 1.2)
                    time.sleep(1.2)
            else:
                log('      · 没找到「开始抽奖」→ 点空白关闭')
                click_phys(1920, 1860, settle=1.0, guard_world=True)
            time.sleep(1.5)
            continue
        if state == 'misc_panel':
            # ★ 2026-11-15 作者要求：这类面板**直接退出**，不研究内容
            log('      · 杂项面板(misc_panel) → ESC 直接退出')
            press('esc', 1.0)
            time.sleep(1.2)
            if detect_state(verbose=False)[0] == 'misc_panel':
                log('      · ESC 没关掉 → 点右上 X')
                click_phys(3727, 137, settle=1.0)
                time.sleep(1.0)
            continue

        if state in ('device_curio', 'leave_confirm'):
            # ★ 2026-11-15 作者要求「效率优先」：直接关，不去用热量强化祝福
            if state == 'leave_confirm':
                log('      · 「还有未使用的热量」→ 确认离开')
                if not click_text('确认', tries=3, interval=0.8):
                    click_phys(2332, 1336, settle=1.2)
            else:
                log('      · 造物调试台面板 → 点右上 X 关闭')
                click_phys(3727, 137, settle=1.2)
            time.sleep(1.2)
            st2 = detect_state(verbose=False)[0]
            if st2 in ('device_curio', 'leave_confirm'):
                log('      · 还没关掉(%s) → 按 ESC' % st2)
                press('esc', 1.0)
                time.sleep(1.2)
                st3 = detect_state(verbose=False)[0]
                if st3 == 'leave_confirm':
                    click_text('确认', tries=2, interval=0.8)
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
                click_phys(3050, 1970, settle=1.2, guard_world=True)   # 兜底：右下角确认
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
        if state == 'pick_curio':
            log('      · 选择奇物（§3.6 图鉴优先）')
            pick_curio()
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
            _st = detect_state(verbose=False)[0]
            try:
                _picker = on_station_picker()
            except Exception:
                _picker = False
            if _picker or _st == 'pick_station':
                log('      · 门开了 → 选站点卡')
                pick_station()
            else:
                log('      · 本区已处理完（state=%r，无选卡界面）→ 下一轮' % _st)
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
    ap.add_argument('--reset', action='store_true',
                    help='先「暂离重进」回到区域出生点，再从头跑（用于复现/验证走位）')
    args = ap.parse_args(argv)
    if args.reset and not args.dry:
        # ★★ 2026-11-09 修：leave_and_rejoin 需要游戏窗口句柄 h。
        #   之前漏传 → TypeError → 重置没生效 → 脚本从"门口"直接按 F 进门 ✗
        log('==== --reset：先暂离重进，回到出生点 ====')
        try:
            _h = find_game()
            if not _h:
                log('  ! 找不到游戏窗口 → --reset 跳过（**这会导致从当前位置继续**）')
            else:
                ensure_foreground(_h)
                time.sleep(0.8)
                ok = leave_and_rejoin(_h)
                log('  %s 暂离重进：%s' % ('✓' if ok else '✗', ok))
                if not ok:
                    log('  ! 重置失败 ⇒ 为避免"从门口直接进门"，这里**直接退出**，请手动暂离重进后重跑')
                    return 3
        except Exception as e:
            log('  ! 暂离重进出错: %s ⇒ 退出（不继续跑）' % e)
            return 3
    return run(max_areas=args.areas, dry=args.dry, skip_entry=args.skip_entry,
               force_entry=args.force_entry)


if __name__ == '__main__':
    sys.exit(main())
