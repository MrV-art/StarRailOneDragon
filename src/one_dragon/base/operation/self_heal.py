# -*- coding: utf-8 -*-
"""失败自愈 · 取证（Self-Heal Evidence）

设计原则（2026-11-07）
--------------------
1. **默认关闭** ✓ —— 配置文件 `custom/selfheal/config.json` 里 `enabled` 为 false 时，
   本模块所有函数立即返回，**零副作用** ✓。
2. **永不抛异常** ✓ —— 所有入口都包了 try/except。取证失败绝不能影响一条龙本身 ✗。
3. **不碰框架逻辑** ✓ —— 唯一挂载点是 `Application.after_operation_done()` 里的一行调用 ✓，
   删掉那一行就完全恢复原状 ✓。
4. **限流** ✓ —— 同一个 app 一小时内最多取一次证 ✓，避免整轮十几个节点全失败时刷屏 ✗。

它会做什么
----------
应用失败（`OperationResult.success == False`）时，在
`custom/selfheal/reports/<时间戳>_<app_id>/` 下生成一个「取证包」：

    manifest.json     包清单
    context.md        给人看：app_id / status / 时间 / git 状态 / 日志尾部 / 复现提示
    events.jsonl      结构化事件（一行一条）
    shot.png          失败瞬间的截图
    prompt.md         **给模型的提问**（含 schema 与已知坑）
    log_tail.txt      一条龙日志尾部

后续人工（或脚本）把 `prompt.md` 交给模型 → 拿到 `patch.json` →
用 `tools/selfheal/apply.py` 或 `git apply` 落到源码 → 跑回归 ✓。

配置示例（custom/selfheal/config.json）
------------------------------------
{
  "enabled": false,
  "max_per_hour": 1,
  "keep_days": 14,
  "log_tail_lines": 120,
  "shot": true
}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import traceback

try:
    from one_dragon.utils.log_utils import log
except Exception:                                    # 单测/脱离框架时
    class _L:
        def info(self, *a, **k):
            pass
        warning = error = debug = info
    log = _L()


# ---------------------------------------------------------------- 路径

def _project_root() -> str:
    """一条龙根目录（src/one_dragon/base/operation/self_heal.py → 上四级）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))


def _cfg_dir() -> str:
    return os.path.join(_project_root(), 'custom', 'selfheal')


def _reports_dir() -> str:
    return os.path.join(_cfg_dir(), 'reports')


def _log_dir() -> str:
    return os.path.join(_project_root(), '.log')


# ---------------------------------------------------------------- 配置

_DEFAULT_CFG = {
    'enabled': False,          # ★ 默认关闭 ✓
    'max_per_hour': 1,
    'keep_days': 14,
    'log_tail_lines': 120,
    'shot': True,
}

_CFG_CACHE = {'mtime': None, 'cfg': None}


def load_config() -> dict:
    """读配置。任何异常都退化成「关闭」✓（宁可什么都不做 ✓）。"""
    path = os.path.join(_cfg_dir(), 'config.json')
    try:
        if not os.path.isfile(path):
            return dict(_DEFAULT_CFG)
        mtime = os.path.getmtime(path)
        if _CFG_CACHE['mtime'] == mtime and _CFG_CACHE['cfg'] is not None:
            return _CFG_CACHE['cfg']
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        cfg = dict(_DEFAULT_CFG)
        cfg.update(raw if isinstance(raw, dict) else {})
        _CFG_CACHE['mtime'] = mtime
        _CFG_CACHE['cfg'] = cfg
        return cfg
    except Exception:
        return dict(_DEFAULT_CFG)


def is_enabled() -> bool:
    try:
        return bool(load_config().get('enabled'))
    except Exception:
        return False


# ---------------------------------------------------------------- 工具

def _safe_name(s: str) -> str:
    keep = []
    for ch in str(s or 'unknown'):
        keep.append(ch if (ch.isalnum() or ch in '-_.') else '_')
    return ''.join(keep)[:60] or 'unknown'


def _run(cmd, cwd=None, timeout=15) -> str:
    try:
        r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return (r.stdout or b'').decode('utf-8', 'replace').strip()
    except Exception as e:
        return '(%s: %s)' % (type(e).__name__, e)


def _tail_lines(path: str, n: int = 120) -> str:
    try:
        if not os.path.isfile(path):
            return '(日志不存在：%s)' % path
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return ''.join(lines[-n:])
    except Exception as e:
        return '(%s: %s)' % (type(e).__name__, e)


def _find_run_log() -> str:
    """一条龙的运行日志（.log/ 下最新的 log.txt / *.log）✓"""
    try:
        d = _log_dir()
        if not os.path.isdir(d):
            return ''
        cands = []
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p) and name.lower().endswith('.log'):
                cands.append((os.path.getmtime(p), p))
        cands.sort(reverse=True)
        return cands[0][1] if cands else ''
    except Exception:
        return ''


_RATE = {}          # app_id -> 上次取证时间戳


def _rate_ok(app_id: str, max_per_hour: int) -> bool:
    try:
        if not max_per_hour or max_per_hour <= 0:
            return True
        now = time.time()
        last = _RATE.get(app_id, 0)
        if now - last < 3600.0 / float(max_per_hour):
            return False
        _RATE[app_id] = now
        return True
    except Exception:
        return True


def _cleanup(keep_days: int) -> None:
    try:
        if not keep_days or keep_days <= 0:
            return
        d = _reports_dir()
        if not os.path.isdir(d):
            return
        limit = time.time() - keep_days * 86400
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isdir(p) and os.path.getmtime(p) < limit:
                shutil.rmtree(p, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------- 提问模板

def _known_pitfalls() -> str:
    return '''## 已知坑（别的助手踩过，别再踩）

- 游戏自动化里「**画面大变**」**不能**当作"界面打开了"的判据 ✗
  （走路转视角、走到锁门前都会让整帧大变 ✗）。要用**结构性证据**：
  例如「右侧 4 个角色头像的亮像素占比 > 0.15」= 仍在游戏内 ✓。
- 颜色/HSV 检测在**夜景**里不可靠 ✗（整体偏蓝会淹没目标 ✗）→ 首选**模板匹配** ✓。
- 模板匹配**不要**加太小尺度 ✗（0.4/0.55 会匹配到画面里的假色块 ✗）→ 去掉 ✓；
  并且要加**时间一致性**（新候选离上次 ≤320px 才采纳 ✓）否则控制环会左右摇摆 ✗。
- 转向符号：本机实测 `mouse_event(+60)` ⇒ **视角往右** ✓（画面内容左移 ✓）。
  不要凭直觉改符号 ✗ —— 先做"发一次 + 量一次"的直接实验 ✓。
- 走路必须**小步**（0.4~0.6 秒/步 ✓）且**每步重新观测** ✓；一次按 W 超 1.2 秒必过站 ✗。
'''

def _prompt_text(app_id: str, status: str, ctx_md: str, log_tail: str) -> str:
    return '''# 任务：诊断 StarRail OneDragon 里这个应用为什么失败，并给出最小补丁

## 失败信息
- 应用：`%s`
- 状态：`%s`

## 现场上下文
%s

## 日志尾部
```
%s
```

%s
## 要求

1. **只改** `src/sr_od/**` 或 `src/one_dragon/base/**` 下的 `.py`，
   **不许**改依赖、配置文件、`assets/`、`deploy/`。
2. 补丁用「唯一匹配的 old/new 字符串」表示（可多条）。
3. 必须说明**为什么这样改不会影响别的应用**。
4. 不确定就输出 `{"diagnosis": "...", "needs_more_evidence": "还要什么"}` ✓
   —— **宁可说不知道 ✗ 也不要瞎改** ✓。

## 输出（严格 JSON）

```json
{
  "diagnosis": "一句话根因",
  "evidence": ["你依据了哪几条日志/截图"],
  "patches": [
    {"file": "src/sr_od/xxx.py", "old": "原文（唯一匹配）", "new": "改后"}
  ],
  "risk": "低/中/高",
  "why_it_wont_break_others": "..."
}
```
''' % (app_id, status, ctx_md, log_tail, _known_pitfalls())


# ---------------------------------------------------------------- 主入口

def capture_if_failed(app, result) -> str | None:
    """在 `Application.after_operation_done()` 里调用的一行接口 ✓。

    返回取证包目录（成功时）或 None。**永不抛异常** ✓。
    """
    try:
        if not is_enabled():
            return None
        if result is None or getattr(result, 'success', True):
            return None

        cfg = load_config()
        app_id = _safe_name(getattr(app, 'app_id', 'unknown'))
        if not _rate_ok(app_id, int(cfg.get('max_per_hour') or 1)):
            log.info('[selfheal] %s 一小时内已取过证，跳过 ✓', app_id)
            return None

        stamp = time.strftime('%Y%m%d-%H%M%S')
        root = os.path.join(_reports_dir(), '%s_%s' % (stamp, app_id))
        os.makedirs(root, exist_ok=True)

        status = str(getattr(result, 'status', '') or '')
        log_tail = _tail_lines(_find_run_log(), int(cfg.get('log_tail_lines') or 120))

        # 截图（框架自带 ✓）
        shot_path = ''
        if cfg.get('shot', True):
            try:
                code = getattr(app, 'screenshot', None)
                shot = code() if callable(code) else None
                if shot is not None:
                    # 用一条龙**自带**的存图函数 ✓ —— 不在这里直接 import cv2 ✗
                    # （.install 那个解释器没有 cv2 ✓，运行时的 .venv 才有 ✓）
                    from one_dragon.utils.debug_utils import (
                        save_debug_image, get_debug_image_path)
                    fname = save_debug_image(shot, file_name='selfheal_%s' % stamp,
                                             prefix='selfheal')
                    src = get_debug_image_path(fname)
                    dst = os.path.join(root, 'shot.png')
                    shutil.copy2(src, dst)
                    shot_path = dst
            except Exception:
                shot_path = ''

        # git 状态（一条龙本身就是 git 仓库 ✓）
        git_txt = _run(['git', 'status', '--short'], cwd=_project_root())
        git_log = _run(['git', 'log', '--oneline', '-3'], cwd=_project_root())

        ctx_md = '\n'.join([
            '- 时间：%s' % time.strftime('%Y-%m-%d %H:%M:%S'),
            '- 项目：%s' % _project_root(),
            '- 截图：%s' % (shot_path or '(无)'),
            '',
            '### git log（近 3 条）',
            '```',
            git_log,
            '```',
            '### git status',
            '```',
            git_txt or '(干净)',
            '```',
        ])

        with open(os.path.join(root, 'log_tail.txt'), 'w', encoding='utf-8') as f:
            f.write(log_tail)
        with open(os.path.join(root, 'context.md'), 'w', encoding='utf-8') as f:
            f.write(ctx_md)
        with open(os.path.join(root, 'events.jsonl'), 'w', encoding='utf-8') as f:
            f.write(json.dumps({'t': 0, 'kind': 'app_result', 'app_id': app_id,
                                'success': False, 'status': status},
                               ensure_ascii=False) + '\n')
        with open(os.path.join(root, 'prompt.md'), 'w', encoding='utf-8') as f:
            f.write(_prompt_text(app_id, status, ctx_md, log_tail))
        with open(os.path.join(root, 'manifest.json'), 'w', encoding='utf-8') as f:
            json.dump({'app_id': app_id, 'status': status, 'created': stamp,
                       'root': root}, f, ensure_ascii=False, indent=2)

        _cleanup(int(cfg.get('keep_days') or 14))
        log.warning('[selfheal] 已生成取证包：%s', root)
        return root
    except Exception:
        try:
            log.error('[selfheal] 取证失败（已忽略，不影响主流程）✗\n%s' % traceback.format_exc())
        except Exception:
            pass
        return None
