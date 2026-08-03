# 系统架构优化方案 — 目录治理 + 推广效率提升

> 编写日期：2026-07-31
> 状态：**清理已执行 / 性能优化待确认后实施**
> 基线数据来源：`logs/20260731_150710`（164 张截图时间戳，6 条笔记连续推广，总耗时 1122s）

---

## 一、已执行：目录治理

### 1.1 删除的前置文件（已归档，可回滚）

所有删除项已打包至 `_archive/pre-production_20260731.zip`（1.3 MB），可随时解压恢复。

| 类别 | 文件 | 删除理由 |
|------|------|----------|
| 录制脚本 | `src/record.py`（19 KB） | 选择器已全部回填至 `selectors.json`，录制能力不再需要 |
| 探测脚本 | `probe_budget_row.py`、`probe_carousel.py`、`probe_modify.py`、`probe_modify_btn.py`、`probe_store_interact.py`、`probe_store_selector.py`、`probe_toolbox_menu.py` | 一次性 DOM 探测工具，产出已固化 |
| 探测截图 | `probe_*.png`（4 张） | 探测过程留痕 |
| 临时测试 | `test_scrape.py`、`test_store_full.py`、`diag.py` | 非 pytest 规范的手工调试脚本，`tests/test_notes.py` 保留 |
| DOM dump | `notes_drawer_dom.txt`、`current_state.png`、`debug_state.png` | 调试期页面快照 |
| 过程文档 | `自动化流程调试总结_20260730.md`、`_v2.md` | 内容已被 `PROJECT_STATUS.md` 覆盖 |
| 运行日志 | 根目录 `run_*.log`（10 个） | 散落在根目录的旧日志，正式日志在 `logs/` |
| 散落调试图 | `logs/` 根下 16 个 `diag_*.png` / `probe_*.png` / `cpm_edit_dump.html` | 不属于任何一次 run 的调试产物 |
| 缓存 | `.pytest_cache/`、`src/__pycache__/`、`tests/__pycache__/` | 可再生 |

### 1.2 代码同步修改

`src/main.py`：移除 `--record` 命令行参数及 `import record` 分支，避免指向已删除模块的死引用。已通过 AST 语法校验。

### 1.3 保留项

- **`logs/` 143 个运行目录（420 MB）全部保留**，未删除任何一次 run 记录。
- `tests/test_notes.py` 保留（正式单测）。
- `data/promoted.db`（查重库）、`data/edge_debug_profile`（登录态）保留。

### 1.4 清理后目录结构

```
mt-note-promoter/
├── config/          settings.json / selectors.json / stores.json
├── src/             browser.py budget_db.py flow.py history_import.py
│                    logging_setup.py main.py notes.py retry.py
├── tests/           test_notes.py
├── data/            promoted.db / edge_debug_profile / browser_profile
├── logs/            143 个运行目录（保留）
├── _archive/        pre-production_20260731.zip（回滚包）
├── PROJECT_STATUS.md / README.md / requirements.txt / start_edge_debug.bat
└── ARCHITECTURE_OPTIMIZATION.md（本文档）
```

---

## 二、性能诊断：为什么一条笔记要 2-3 分钟

### 2.1 实测基线

从 `logs/20260731_150710` 的 164 张截图 mtime 反推每一步真实耗时。稳定态单轮（无重试）≈ **115 秒**；含导航失败重试的实际均值 ≈ **187 秒/条**（1122s ÷ 6 条）。

**分段占比：**

| 分段 | 耗时 | 占比 |
|------|------|------|
| 导航段（门户页 → 打开新建推广表单） | 62 s | 54% |
| 业务段（选笔记 → 地域 → 出价 → 提交） | 53 s | 46% |

**结论：一半以上的时间没有在做业务，只是在找路。**

### 2.2 根因 1（最大）：`_locate()` 串行全额超时探测

**这是唯一最大的时间黑洞，单项占 28%。**

`src/browser.py` 的 `_locate()` 在多 iframe 页面中查找元素：

```python
# 阶段 1：先赌当前活跃 frame —— 赌错就白等满 timeout_ms
try:
    loc = self._active_frame.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout_ms)   # ← miss = 白烧 15s
except Exception:
    pass

# 阶段 2：按优先级遍历所有 frame —— 每个又是满额 timeout_ms
for frame in ordered:
    loc = frame.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout_ms)   # ← 每 miss 一次再烧 15s
```

美团经营宝页面同时挂载 5~8 个 iframe（菜单 iframe、内容 iframe、cpm-edit、budget-group-list…）。目标元素若排在第 3 个被试到，实际耗时 = `timeout × 2 + 命中时间`。

**实测对账（完全吻合）：**

| 步骤 | timeout | 理论 = timeout×2 + 命中 | 实测 |
|------|---------|------------------------|------|
| 等待工具箱 tab | 15000 ms | 15 + 15 + 2 = 32 s | **32.3 s** |
| 推广地域-修改 | 5000 ms（默认） | 5 + 5 + 1 = 11 s | **10.9 s** |
| 推广中心 | 5000 ms | 5 + 3 = 8 s | **8.3 s** |
| 智选展位 | 5000 ms | 5 + 2 = 7 s | **6.8 s** |

这些秒数**不是页面慢，是脚本在空等**。元素其实早就在某个 frame 里渲染好了。

**修复方案：把「串行满额等待」改为「全 frame 短周期轮询」**

```python
def _locate(self, selector, timeout_ms=5000, only_in_frame=None):
    """单一全局 deadline，所有 frame 每轮各探 120ms，命中即返回。
    总耗时 = 元素真实就绪时间，不再是 timeout × frame 数。"""
    deadline = time.time() + timeout_ms / 1000
    # 活跃 frame 优先，其余按 menu/content 优先级排序
    while time.time() < deadline:
        for frame in self._ordered_frames():
            try:
                loc = frame.locator(selector).first
                if loc.count() and loc.is_visible():       # 非阻塞判定
                    self._switch_frame(frame)
                    return loc, frame
            except Exception:
                continue
        time.sleep(0.12)
    raise TimeoutError(f"{timeout_ms}ms 内所有 frame 均未找到: {selector}")
```

配套：为已知选择器建立 **frame 亲和缓存**（`selector → frame.url 特征`），命中缓存的 frame 优先探测，二次调用几乎零成本。

> 预计节省：**约 55 s/轮**（63 s → 8 s）

### 2.3 根因 2：每轮重爬四级菜单树

现状每推一条笔记都执行：
`goto 门户页 → 点推广中心 → 点子菜单 → 点智选展位 → 悬停工具箱 → 点共享预算 → 翻页找预算行 → 悬停 → 新增推广 → 去新建推广`

但后台存在**可直达的真实 URL**（已从 `data/edge_debug_profile` 的 IndexedDB 中确认）：

```
https://e.dianping.com/app/peon-cpm-ncpm/html/budget-group-list.html   共享预算列表
https://e.dianping.com/app/peon-cpm-ncpm/html/cpm-edit.html            新建推广编辑页
```

`goto(budget-group-list.html)` 一步到位，整条菜单链（含那 32 s）直接消失。

同时，**同门店连推 N 条时不必回门户页**——提交成功后直接 `goto` 预算列表页即可，第 2 条起导航成本再降。

> 预计节省：**约 50 s/轮**（与根因 1 有重叠，合并计算见 §2.7）

### 2.4 根因 3：`slow_mo_ms = 300`

`config/settings.json` → `browser.slow_mo_ms: 300`。Playwright 会给**每一个**原子操作（click / fill / hover / count / is_visible）强制加 300 ms 延迟。单轮约 40 个原子操作 = **12 s 纯浪费**。

调试期需要它看清动作，现在流程已稳定，应降到 `0`（或保留 50 ms 作为反检测抖动）。

> 预计节省：**约 10-12 s/轮**

### 2.5 根因 4：38 处硬编码 `time.sleep`

`flow.py` 中统计到 **38 处 `time.sleep`（合计 53.4 s）** + **5 处 `wait_for_timeout`（合计 15 s）**：

```
sleep(0.5) ×5    sleep(0.8) ×3    sleep(1.0) ×8    sleep(1.2) ×5
sleep(1.5) ×9    sleep(2.0) ×5    sleep(3.0) ×2    sleep(5.0) ×1
```

这些是调试期「不知道等多久就先睡一会」的产物。绝大多数应替换为**条件等待**：

| 现状 | 替换为 |
|------|--------|
| `b.click(...); time.sleep(2)` | `wait_until(抽屉可见)` |
| `time.sleep(3)`（点下一步后） | `wait_until(创意页指示器出现)` |
| `time.sleep(5)`（提交后） | `wait_until(弹窗出现 or URL 跳转)` |
| `wait_for_timeout(3000)`（goto 后） | `wait_until(目标 frame 就绪)` |

保留少量必要的 UI 动画等待（如 `sleep(0.3)` 等抽屉动画）。

> 预计节省：**约 25-30 s/轮**

### 2.6 根因 5：失败重试整段重来，单次代价 2 分钟

时间线上出现两处异常尖峰：**104.5 s** 和 **130.1 s**。这是导航失败后 `goto 门户页 → 整段菜单重跑` 的代价。

`_nav_to_shared_budget` / `_open_new_promotion` 都是 `max_try=3` 的**粗粒度整段重试**——中间任何一步失败，前面成功的步骤全部作废重来。

**改进：**
- 直达 URL 后，导航失败率本身会大幅下降（不再依赖 hover 弹层这种脆弱交互）
- 重试粒度细化到「步」，只重做失败的那一步
- 增加**熔断**：同一门店连续 3 轮失败即跳过该门店并告警，避免在坏门店上空转

### 2.7 优化收益汇总

| 优化项 | 优先级 | 现状 | 优化后 | 节省 |
|--------|--------|------|--------|------|
| `_locate` 改全 frame 短轮询 + frame 亲和缓存 | **P0** | 63 s | 8 s | −55 s |
| 直达 URL 跳过菜单树 | **P0** | （含在上项） | — | 与上项重叠 |
| `slow_mo_ms` 300 → 0 | **P0** | 12 s | 0 | −12 s |
| 38 处 `sleep` → 条件等待 | **P1** | 35 s | 8 s | −27 s |
| 同门店连推免重导航 | **P1** | 每轮全量 | 复用列表页 | −8 s |
| 截图降级（失败/关键节点才截） | **P2** | 25 张/轮 | 4 张/轮 | −3 s，磁盘 −85% |
| 细粒度重试 + 门店熔断 | **P2** | 失败 −120 s | 失败 −20 s | 稳定性 |

**单轮预期：115 s → 35-40 s，提速约 3 倍。**

**全量 35 家门店测算：**

| | 现状 | 优化后 |
|---|---|---|
| 单条耗时（含失败均摊） | 187 s | 50 s |
| 单店 40 条 | 2.1 h | 33 min |
| 35 家门店 | **约 73 h** | **约 19 h** |

---

## 三、架构重构方案

### 3.1 现状问题

`flow.py` 已膨胀到 **1043 行 / 44 KB**，单文件混杂了四层职责：

- 浏览器原语调用（`b.click` / `time.sleep` / frame 遍历）
- 页面交互细节（抽屉怎么开、下拉怎么关、弹窗怎么清）
- 业务规则（去重、浏览量门槛、上限判断）
- 流程编排（门店循环、轮次循环、重试）

后果：改一个选择器要在 1000 行里翻；页面交互逻辑无法单测；同一段 frame 遍历代码在 `_scrape_notes` / `_check_note` / `_dismiss_modals` / `_set_region` 里重复了 4 遍。

### 3.2 目标分层

```
src/
├── main.py                  CLI 入口（保持不变）
├── core/
│   ├── browser.py           连接管理 + 操作原语（click/fill/hover/shot）
│   ├── frames.py            【新】frame 解析、优先级排序、亲和缓存  ← P0 修复落点
│   └── waits.py             【新】条件等待原语，替代 time.sleep
├── pages/                   【新】页面对象层：一个页面/组件一个类
│   ├── budget_list.py       共享预算列表：直达 URL、翻页找行、悬停新建
│   ├── promo_edit.py        新建推广表单：推广目的、门店选择、地域、出价、提交
│   ├── notes_drawer.py      笔记抽屉：抓取、匹配、勾选、确认
│   └── modals.py            通用弹窗清理（收编 _dismiss_modals）
├── domain/
│   ├── notes.py             Note 模型 + 查重库
│   ├── budget_db.py         共享预算镜像库
│   └── strategy.py          【新】选笔记策略（浏览量门槛、去重规则）
└── flow.py                  纯编排：门店循环 + 轮次循环 + 熔断（目标 < 200 行）
```

**收益：**
- frame 遍历逻辑收敛到 `core/frames.py` 一处，P0 修复只改一个文件即全局生效
- 页面对象可脱离完整流程单独测试
- `flow.py` 从 1043 行降到 200 行以内，业务规则一眼可见
- 新增门店类型 / 新增推广目的时，只动 `pages/` 不动编排

### 3.3 实施顺序（建议分三批，每批可独立验证）

**批次 A — 纯性能，不改结构（风险最低，收益最大）**
1. 重写 `browser._locate()` 为全 frame 短轮询 + 亲和缓存
2. `settings.json` 增加 `nav.direct_urls` 配置，`_nav_to_shared_budget` 优先走直达 URL，失败再回退菜单点击
3. `slow_mo_ms` 300 → 0
4. 用 `--dry-run` 单门店验证，对比截图时间戳确认提速

> 预期：115 s → 约 50 s。**此批不动业务逻辑，回归风险极小。**

**批次 B — 等待策略与稳定性**
5. 新增 `core/waits.py`，逐个替换 38 处 `time.sleep`
6. 同门店连推复用预算列表页
7. 细粒度重试 + 门店级熔断
8. 截图降级为「关键节点 + 失败时」

> 预期：50 s → 35-40 s。需要逐步替换并观察，建议每替换 5-8 处跑一次 dry-run。

**批次 C — 结构重构**
9. 拆分 `pages/` 页面对象层
10. `flow.py` 瘦身为纯编排
11. 补 `pages/` 层单测

> 纯代码搬运，不改行为。可在批次 A/B 收益落地后从容进行。

---

## 四、风险与权衡

| 优化项 | 风险 | 缓解措施 |
|--------|------|----------|
| 直达 URL | 美团可能校验 referer 或菜单态，直接 goto 可能被重定向 | **保留菜单点击作为 fallback**：直达失败自动回退旧路径，行为不退化 |
| `slow_mo` 归零 | 操作过快可能触发风控，或页面来不及响应 | 先降到 100 ms 观察一轮，无异常再归零；条件等待会兜住页面响应 |
| `sleep` → 条件等待 | 某些 `sleep` 实际在等**没有可观测信号**的异步副作用（如表单内部状态同步），改掉可能引入偶发失败 | 分批替换，每批 dry-run 验证；对找不到可靠信号的保留短 sleep |
| 截图降级 | 出问题时排障信息变少 | 只降级成功路径的截图，**失败路径截图全部保留**并加截 DOM dump |
| 结构重构 | 大范围搬运可能引入低级错误 | 放到最后做；`_archive` 回滚包 + 逐模块搬运后立即 dry-run |

**一个明确的判断：批次 A 的三项改动收益占了总收益的 70%，且几乎不碰业务逻辑。建议先只做批次 A，验证提速后再决定是否继续。**

---

## 五、待确认事项

1. **是否立即实施批次 A？** 三项改动集中在 `browser.py` + `settings.json`，改动面小、可快速验证。
2. **直达 URL 是否需要先人工验证？** 可以先在已登录的 Edge 里手动粘贴 `budget-group-list.html` 地址，确认不被重定向再写进代码。
3. **`slow_mo_ms` 目标值**：直接归零，还是先降到 100 ms 观察？
4. **全量跑 35 家门店的排期**：优化后约 19 小时，是否仍按分批（每批 5-8 家）执行？
5. **`max_promotions_per_day = 100`** 在全量跑时是否需要调整？
