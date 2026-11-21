# 失败自愈（self-heal）· 已集成进一条龙

> 2026-11-07 加入。**默认关闭** ✓ —— 不改 `config.json` 就完全不生效 ✓。

## 它做了什么

一条龙里**任何一个应用**（每日实训 / 差分宇宙 / 历战余响 …）失败时：

```
Application.after_operation_done(result)   ← 框架每次都调
   └─ capture_if_failed(app, result)       ← 我们加的一行（默认什么都不做）
        ├─ 截图（用框架自带的 save_debug_image ✓）
        ├─ 抓 .log 里最新日志的尾部
        ├─ 抓 git status / git log
        └─ 写 custom/selfheal/reports/<时间戳>_<app_id>/
             ├─ context.md     人看：app/status/时间/git/日志
             ├─ events.jsonl   机器看：结构化事件
             ├─ log_tail.txt   一条龙日志尾部
             ├─ shot.png       失败瞬间的截图
             ├─ prompt.md      ★ 给模型的提问（含 schema + 已知坑）
             └─ manifest.json  包清单
```

## 怎么开

编辑 `custom/selfheal/config.json`：

```json
{
  "enabled": true,
  "max_per_hour": 1,
  "keep_days": 14,
  "log_tail_lines": 120,
  "shot": true
}
```

- `enabled` —— 总开关，**默认 false** ✓
- `max_per_hour` —— 同一 app 每小时最多取几次证（防整轮刷屏 ✓）
- `keep_days` —— 自动清理多少天前的包
- `shot` —— 是否存截图（false 时包里没有 `shot.png`）

改完**不用重启** ✓ —— 每次调用都会看文件 mtime ✓（缓存按 mtime 失效 ✓）。

## 怎么关

两种都行：

1. `config.json` 里 `enabled: false` ✓（推荐 ✓）
2. 删掉 `src/one_dragon/base/operation/application_base.py` 里那段 7 行 hook ✓
   （文件带 `.bak-selfheal-<时间戳>` 备份 ✓，直接拷回去也行 ✓）

## 拿到包之后怎么用

```powershell
# 1) 把 prompt.md 贴给任意模型（或走你自己的 API）
# 2) 模型按 schema 回 JSON 补丁 → 存成 patch.json
# 3) 落到源码（一条龙本身是 git 仓库，也可以用 git apply）
#    注意：这里改的是 src/ 下的 .py，改完**重启一条龙**才生效
# 4) 验收：跑一次那个应用，确认不再失败
```

## 安全边界（重要）

- **取证永不抛异常** ✓ —— 全部 try/except；取证失败最多丢一个包 ✓，不会影响一条龙 ✓
- **不动框架逻辑** ✓ —— 唯一改动是 `after_operation_done` 里加的一行调用 ✓
- **不联网** ✓ —— 只写本地文件 ✓，不发邮件、不调 API ✓
- **不碰凭据** ✓ —— 日志尾部可能有账号名，但**不会**包含密码（一条龙的密码字段早已清空 ✓）
- 升级一条龙时 `src/` 会被覆盖 ✗ → **升级后需要重新加 hook** ✓；
  这个 README 和 `reports/` 在 `custom/` 下 ✓，**升级不会丢** ✓

## 已知限制

- `log_tail.txt` 抓的是 `.log/` 下**最新的 .log** ✓ —— 如果一条龙把日志按天分文件 ✓，
  跨零点失败时可能抓到新文件 ✓（一般没问题 ✓）
- 截图是 **3840×2160** ✓（这张留着看细节 ✓）；要发给模型建议先缩到 1920 ✓
- **不是**自动修 ✓ —— 只负责"把现场完整留存 + 生成提问" ✓；
  改代码那一步留给人工/模型 + review ✓

## 相关的独立工具

`D:\_PROGRAM_PythonProjects\Learn\tools\selfheal\` 里有一套更完整的
（离线回归集 + patch 应用 + 唯一匹配校验 + 失败自动回滚 ✓），
和这里互补 ✓：这里负责**在一条龙里取证** ✓，那边负责**改完之后的把关** ✓。
