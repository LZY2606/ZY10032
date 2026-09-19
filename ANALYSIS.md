# croniter `get_next` / `get_prev` 内部机制分析

本文从表达式构造追到字段展开、候选日期计算、DST 修正与当前状态提交。
所有行号基于仓库当前快照（`src/croniter/croniter.py`，共 1586 行），
每条断言均可由源码或 `src/croniter/tests/test_croniter_understanding.py`
中的新增测试逐条核对；无法从源码确认的推测集中列在文末「未确认的推测」一节。

## 1. 调用链总览

- `get_next` / `get_prev` 只是薄封装，统一转入 `_get_next`（`src/croniter/croniter.py:346`、`:355`、`:406`）。
- `_get_next` 先调用 `self.set_current(start_time, force=True)`（`src/croniter/croniter.py:409`）；`start_time` 为 `None` 时 `set_current` 直接跳过（`src/croniter/croniter.py:366` 起，`start_time is not None` 才赋值），因此连续调用时游标就是上一次的 `self.cur`。
- 随后 `_calc_next(is_prev)` 算出 aware datetime（`src/croniter/croniter.py:419`），
  只有 `update_current` 为真时才把结果写回 `self.cur`（`src/croniter/croniter.py:421-422`）。
  这就是「当前状态提交」的唯一入口。

## 2. 构造期：字段展开只发生一次

- `__init__` 在 `src/croniter/croniter.py:329` 调用 `_expand`，把结果固化到
  `self.expanded / self.nth_weekday_of_month / self.expressions / self.nearest_weekday`。
- `_expand`（`src/croniter/croniter.py:945`）逐字段先跑 `EXPANDERS`（`src/croniter/croniter.py:1586`，目前只有 `HashExpander`），再解析范围、步长、`l`、`W`、`#` 等。
- 之后每次 `_calc_next` 只做浅拷贝 `expanded = self.expanded[:]`（`src/croniter/croniter.py:478`），**不会重新展开**。

### hash 与 random 何时决定

- `HashExpander.do`（`src/croniter/croniter.py:1502`）：
  - `h` 用 `binascii.crc32(hash_id)`（`src/croniter/croniter.py:1511`），完全由 `hash_id` 决定，确定性；
  - `r` 用 `random.randint(0, 0xFFFFFFFF)`（`src/croniter/croniter.py:1509`），取自全局 `random` 模块，**在构造那一刻**采样。
- 两者都只在 `_expand` 期间求值一次（`HashExpander.expand`，`src/croniter/croniter.py:1539`）。

### 前后搜索应复用还是不复用

- 同一实例内：`get_next`/`get_prev` 必须复用 `self.expanded`，否则来回切换方向会落在不同的日程上。源码保证这一点（展开只在构造期，见上）。
- 跨实例：`h` 表达式只要 `hash_id` 相同，新实例展开结果一致，可以放心重建；
  `r` 表达式每次新建实例都重新随机，**不应**在迭代中途重建实例，除非重新固定相同的 seed。
  测试 `test_understanding_cursor_and_expansion_state` 固定 `random.seed(1234)` 后证明：
  同 seed 新实例首步相同（`02:47`），不同 seed（999）展开为 `02:07`。

## 3. 候选日期计算

- `_calc` 先按方向加偏移：prev 用 `relativedelta(microseconds=-1)`，next 用 `minutes=1`（含秒/年字段时 `seconds=1`）（`src/croniter/croniter.py:546-555`），再截断到分/秒（`src/croniter/croniter.py:557-561`）。
  prev 的 `-1µs` 会使恰好落在匹配点上的游标被截到前一分钟，这是「next 后立刻 prev 回不到原点」的根因（见风险点 1）。
- 然后按 year → month → day（`day_of_month`/`nearest_weekday` 与 `day_of_week`/`nth`）→ hour → minute → second 的顺序调用各 `proc_*`（`src/croniter/croniter.py:566-758`），任一字段改变就从头再来，直到收敛或超出 `_max_years_between_matches`。
- `day_or`：当 DOM 与 DOW 都受限且 `day_or=True`（默认）时，`_calc_next` 把两边分别置 `*` 各算一次再取并集（`src/croniter/croniter.py:481-535`）；单边失败在「干净分裂」（无 `#`/`W`）时被吞掉，双边都失败才抛 `CroniterBadDateError`（`src/croniter/croniter.py:526-530`）。

## 4. DST 修正

- 候选时间先在**去时区**的本地墙上时间算出，再由 `_add_tzinfo`（`src/croniter/croniter.py:179`）装回时区：
  - zoneinfo 路径直接 `date.replace(fold=1 if is_prev else 0, ...)`（`src/croniter/croniter.py:218`）——**搜索方向决定 fold**；
  - 不存在的本地时间（春季缺口）逐分钟向前跳到下一个存在的时刻（`src/croniter/croniter.py:219-223`）；
  - 歧义时间（秋季重复）比较两个 fold 的 UTC 偏移，取「仍是后继且离前值更近」的那个（`src/croniter/croniter.py:225-232`）。
- 回到 `_calc` 后，若 offset 发生变化（`_timezone_delta`，`src/croniter/croniter.py:799`），还会用 `now + offset_delta` 重算一个「另一偏移下的备选时间」（`src/croniter/croniter.py:806-817`），取更靠近当前游标的合法后继。秋季重复小时能被产出两次（fold=0 与 fold=1 各一次）正是这段逻辑的效果。

### America/New_York 两个过渡的表示

- 春季缺失小时（2026-03-08 02:00–03:00 不存在）：不存在的墙上时间被顺移，
  `30 2 * * *` 在该日命中 `2026-03-08T03:00:00-04:00`（fold=0）。
- 秋季重复小时（2026-11-01 01:00–02:00 出现两次）：两次发生用**同一墙上时间 + 不同 fold/offset** 表示：
  `2026-11-01T01:30:00-04:00`（fold=0）与 `2026-11-01T01:30:00-05:00`（fold=1）。
  注意反向搜索时 `fold=1` 是机械设置的（`src/croniter/croniter.py:218`），
  在非歧义时间上也会出现 fold=1（例如 prev 得到 `2026-11-02T01:30:00-05:00 fold=1`），
  此时 fold 不影响 `utcoffset()`，只是标志位。

## 5. 当前状态提交与失败搜索

- 游标提交只发生在 `_calc_next` 正常返回之后（`src/croniter/croniter.py:419-422`）。
- 搜索失败时 `CroniterBadDateError` 在 `_calc` 末尾抛出（`src/croniter/croniter.py:822-823`），
  异常穿过 `_get_next`，`self.cur = timestamp` 不会执行——**一次失败的搜索不会推进内部游标**。
  由 `test_understanding_failed_search_keeps_cursor` 验证：`0 0 30 2 *`（2 月 30 日）
  抛异常后 `get_current()` 仍是 base time。
- 注意 `_get_next` 会记录方向：`self._is_prev = is_prev`（`src/croniter/croniter.py:412`），
  影响后续 `iter()`/`next()` 的默认方向，但不影响已固化的展开结果。

## 6. 五个可复现风险点

以下预期值均已在本仓库实际运行确认（Python 3，dateutil `gettz("America/New_York")`）。

1. **方向切换回退一步**
   - 表达式 `*/5 * * * *`；base `2024-01-25 04:46`（naive）；调用 `get_next` → `get_prev`
   - 预期：next=`2024-01-25T04:50:00`，prev=`2024-01-25T04:45:00`（不是 04:50，也不是 base）。
2. **春季缺失小时被顺移**
   - 表达式 `30 2 * * *`；base `2026-03-07T12:00` @ America/New_York；调用 `get_next`
   - 预期：`2026-03-08T03:00:00-04:00`（02:30 不存在，顺移到 03:00，而非次日凌晨）。
3. **秋季重复小时靠 fold 区分**
   - 表达式 `30 1 * * *`；base `2026-10-31T12:00` @ America/New_York；调用 `get_next` ×3
   - 预期：`2026-11-01T01:30:00-04:00`(fold=0) → `2026-11-01T01:30:00-05:00`(fold=1) → `2026-11-02T01:30:00-05:00`。
4. **`R` 表达式跨实例重新随机**
   - 表达式 `R 2 * * *`；base `2026-03-01T00:00` @ America/New_York；
     调用序列：seed(1234) 后新建实例取 `get_next`，再 seed(999) 新建实例取 `get_next`
   - 预期：前者 `2026-03-01T02:47:00-05:00`，后者 `2026-03-01T02:07:00-05:00`；
     同一实例内 next/prev 则始终复用 47 分这一展开结果。
5. **失败搜索不推进游标**
   - 表达式 `0 0 30 2 *`；base `2026-03-01T00:00` @ America/New_York；调用 `get_next`（抛 `CroniterBadDateError`）→ `get_current`
   - 预期：异常后 `get_current()` 仍为 `2026-03-01T00:00:00-05:00`。

## 7. 状态时间线（固定 seed 的 next/next/prev/next）

对应 `test_understanding_cursor_and_expansion_state`，表达式 `R 2 * * *`，
base `2026-03-01T00:00:00-05:00`，`random.seed(1234)`：

| 步骤 | 调用 | 返回 (ISO) | fold | offset | 游标 cur 随后指向 |
|---|---|---|---|---|---|
| 0 | 构造（展开在此刻定型为 47 分） | — | — | — | 2026-03-01T00:00:00-05:00 |
| 1 | get_next | 2026-03-01T02:47:00-05:00 | 0 | -05:00 | 同左 |
| 2 | get_next | 2026-03-02T02:47:00-05:00 | 0 | -05:00 | 同左 |
| 3 | get_prev | 2026-03-01T02:47:00-05:00 | 1 | -05:00 | 同左 |
| 4 | get_next | 2026-03-02T02:47:00-05:00 | 0 | -05:00 | 同左 |

要点：展开（47 分）在步骤 0 一次性决定，步骤 1–4 只移动游标；
步骤 3 的 fold=1 来自 `is_prev` 的机械设置（`src/croniter/croniter.py:218`），
该时刻非歧义，offset 不受影响。

## 8. 复现命令

```bash
# 准备（一次性，不计入演示）
python3 -m pip install -e . pytest python-dateutil pytz

# 验收：从仓库根目录运行，退出码为 0，输出含各步骤 ISO 时间/fold/offset
python3 -m pytest -q -k 'understanding'

# 手工复现风险点 4/5 的最小片段
python3 - <<'PY'
import random
from datetime import datetime
from dateutil.tz import gettz
from croniter import croniter, CroniterBadDateError
NY = gettz("America/New_York")
random.seed(1234)
it = croniter("R 2 * * *", datetime(2026, 3, 1, tzinfo=NY), ret_type=datetime)
for f in (it.get_next, it.get_next, it.get_prev, it.get_next):
    d = f(); print(d.isoformat(), "fold=", d.fold, "offset=", d.utcoffset())
bad = croniter("0 0 30 2 *", datetime(2026, 3, 1, tzinfo=NY), ret_type=datetime)
try:
    bad.get_next()
except CroniterBadDateError:
    print("cursor kept:", bad.get_current(datetime).isoformat())
PY
```

## 9. 未确认的推测（非既定事实）

- `_add_tzinfo` 内有两处 `# TODO: Check negative DST` 注释（`src/croniter/croniter.py:209`、`:228`）。
  据此推测：南半球式「偏移变大」的负 DST 过渡可能未被充分覆盖，但本文未构造用例验证，仅列为疑点。
- `get_prev` 在非歧义时间上留下 `fold=1` 是否有下游消费者依赖，源码中未见相关读取，推测无实际影响，未逐一核实所有调用方。
