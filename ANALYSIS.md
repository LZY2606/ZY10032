# croniter get_next / get_prev 行为分析

本文基于当前仓库源码 `src/croniter/croniter.py`（版本 6.3.0.dev0）撰写。所有带行号的断言均可逐条对照源码；带“新增测试”标记的断言可由
`src/croniter/tests/test_croniter_understanding.py` 核对；无法从源码或测试直接确认的内容统一放在第 8 节“推测与未验证项”，不作为既定事实。

行号均指向 `src/croniter/croniter.py`，测试行号指向 `src/croniter/tests/test_croniter_understanding.py`。

## 1. 从构造表达式到展开

`croniter.__init__`（`src/croniter/croniter.py:289-337`）按固定顺序做四件事：

1. 保存参数（`day_or`、`implement_cron_bug`、`second_at_beginning`、`expand_from_start_time`），并把 `hash_id` 统一编码成 UTF-8 bytes
   （`src/croniter/croniter.py:302-312`）。
2. 解析 `max_years_between_matches`，缺省 50，最小钳到 1（`src/croniter/croniter.py:314-317`）。
3. 调 `set_current(start_time, force=True)`：aware datetime 在此被转成 UNIX 时间戳，同时 `self.tzinfo` 记录下起始时间的时区；`start_time`、
   `dst_start_time`、`cur` 三个游标初值相同（`src/croniter/croniter.py:366-377`）。
4. 调一次 `_expand(...)`，把表达式永久展开为 `self.expanded`，并同时产出 `self.nth_weekday_of_month`（`a#b`/`l`）、
   `self.expressions`（原始分词，供 cron-bug 的星号判断使用）和 `self.nearest_weekday`（`W`），见 `src/croniter/croniter.py:329-335`。

`_expand`（`src/croniter/croniter.py:945-1268`）的关键步骤：

- `@daily` 等别名先做替换；当且仅当传入 `hash_id` 时，别名才选用含 `h` 的变体（`src/croniter/croniter.py:949-964`）。
- 只接受 5/6/7 段（`src/croniter/croniter.py:968-971`）；`second_at_beginning` 会把前导秒字段挪到第 6 列
  （`src/croniter/croniter.py:973-975`）。
- 每个字段在常规解析前先经过 `EXPANDERS` 里的 expander，目前只有 `HashExpander`（`src/croniter/croniter.py:981-985`、
  `src/croniter/croniter.py:1586`）。
- `*/n` 先归一化成 `min-max/n`，`x/n` 归一化成 `x-max/n`；相等区间 `Jan-Jan` 被刻意展开成整个循环，而落在字段最大值上的 `x/n`
  （如 `59/15`）被识别为单点，二者在 `src/croniter/croniter.py:1130` 的 `start_at_field_max` 处分流。
- 展开结果按序排序（`src/croniter/croniter.py:1207-1208`），当某字段恰好覆盖整个合法值域时压缩回 `["*"]`
  （`src/croniter/croniter.py:1209-1218`）。

注意：展开只发生在构造时一次，`get_next/get_prev` 从不重新展开（搜索函数内只复制 `self.expanded`，见第 2 节）。

### 1.1 hash 值在何时决定

`HashExpander.do`（`src/croniter/croniter.py:1502-1512`）对 `h` 类型做的是 `binascii.crc32(hash_id)`，再 `((crc >> idx) % 宽度) + 下界`：

- 决定因素只有 `hash_id`、字段下标 `idx`、显式范围和除数；**与起始时间、调用方向、调用次数都无关**。
- 纯 `h` 而无 `hash_id` 直接报错（`src/croniter/croniter.py:1547-1548`）。
- 带除数时先在“第一个周期” `[begin, begin+divisor-1]`（且不超过 `range_end`）里抽一个偏移 `x`，再发成 `x-end/divisor`；`x` 恰好等于
  `range_end` 时只发单点字符串（`src/croniter/croniter.py:1517-1537`）。

因为整段展开发生在 `__init__` 里（`src/croniter/croniter.py:329`），同一个 `hash_id` 任意时刻构造出的 `expanded` 都相同。新增测试
`test_understanding_next_next_prev_next_cursor_and_expansion`（`src/croniter/tests/test_croniter_understanding.py:70-72`）对“同 seed
重建即同展开”的随机情形也做了对称验证。

### 1.2 random 值在何时决定

`r` 类型在同一个 `do` 里改走 `random.randint(0, 0xFFFFFFFF)`（`src/croniter/croniter.py:1508-1509`），用的是全局 `random` 模块状态。
因此每个含 `r` 的字段在**构造期间**消耗一次全局 PRNG（`H R * * *` 这类每字段一次），此后 `expanded` 冻结，搜索阶段不再抽任何随机数。
固定随机种子后，同一表达式在同一构造时点的展开可复现；这正是新增测试的前提（`src/croniter/tests/test_croniter_understanding.py:36-39`）。

## 2. 候选日期的逐字段计算

`get_next` 与 `get_prev` 都只是给 `_get_next(..., is_prev=...)` 传不同方向（`src/croniter/croniter.py:346-358`）。`_get_next` 的顺序是
（`src/croniter/croniter.py:406-425`）：

1. `set_current(start_time, force=True)`（`src/croniter/croniter.py:409`）：只要本次显式传了 `start_time`（非 None），搜索开始前游标就
   先被重置（细节见第 5 节）。
2. 记录方向 `self._is_prev`（`src/croniter/croniter.py:410-412`）。
3. `_calc_next(is_prev)` 算出 aware 结果（`src/croniter/croniter.py:419`）。
4. 把结果转成时间戳；仅当 `update_current=True`（默认）才写回 `self.cur`（`src/croniter/croniter.py:420-422`）。

`_calc_next`（`src/croniter/croniter.py:476-538`）首先把游标时间戳转回带时区的 `current`，然后**浅拷贝展开表**（`expanded =
self.expanded[:]`，`src/croniter/croniter.py:477-479`）。这个拷贝很关键：`day_or` 的并集搜索会临时把一侧字段改成 `["*"]`
（`src/croniter/croniter.py:508-517`），但改的只是本次拷贝，实例上的 `self.expanded` 永不被搜索过程修改。

### 2.1 day_or：并集与交集

只有 DOM 与 DOW **都不是** `*` 且 `day_or=True` 时才走并集分支（`src/croniter/croniter.py:482`）。做法是分别求
“只按 DOM（DOW 置 `*`）”的 `t1` 和“只按 DOW（DOM 置 `*`）”的 `t2`，一侧抛 `CroniterBadDateError` 时只让该侧变 `None`
（`src/croniter/croniter.py:510-524`），两侧都失败才整体失败（`src/croniter/croniter.py:526-530`），否则按方向取更近者
（`src/croniter/croniter.py:534-536`）。`day_or=False` 时跳过该分支，直接进入 `_calc`，DOM 与 DOW 在其中形成交集。
`implement_cron_bug` 只影响“其中一侧是星号时该用并集还是交集”的 vixie-cron 兼容行为（`src/croniter/croniter.py:486-493`）。

### 2.2 字段循环

`_calc`（`src/croniter/croniter.py:540-823`）先把方向体现成两个东西（`src/croniter/croniter.py:547-561`）：

- 向后：最近差函数 `_get_prev_nearest_diff`，起始偏移 `-1 微秒`；
- 向前：`_get_next_nearest_diff`，起始偏移 `+1 秒`（6/7 段表达式）或 `+1 分钟`（5 段），并把秒/微秒清零。

这就是“命中点本身不算”的来源：严格大于/严格小于游标。字段处理器链固定为
年 → 月 → 日（有 `W` 时走最近工作日）→ 星期（有 `#`/`l` 时走第 n 个）→ 时 → 分 → 秒
（`src/croniter/croniter.py:751-759`）。主循环（`src/croniter/croniter.py:761-778`）每次从年到秒跑一遍，任一字段发生跳变就从头再跑，
直到所有字段都落在允许集合上；年份跨度超过 `_max_years_between_matches` 就抛 `CroniterBadDateError`
（`src/croniter/croniter.py:821-823`）。最近差在“本周期内找不到”时会加上/减去一个完整周期长度，于是低字段溢出自然驱动高字段进位
（前向 `src/croniter/croniter.py:833-847`，后向 `src/croniter/croniter.py:857-883`；年字段无周期、直接返回 `None` 终止搜索）。

## 3. DST 修正

所有字段都匹配时，得到的还是一个 naive 的本地墙钟时间 `unaware_time`（`src/croniter/croniter.py:780`）。若 `now` 本身 naive，则直接
返回（`src/croniter/croniter.py:781-782`）；否则进入 `_add_tzinfo`（`src/croniter/croniter.py:179-233`）补时区，分两条路径：

- **pytz 路径**（tzinfo 带 `localize`，`src/croniter/croniter.py:190-216`）：用 `localize(date, is_dst=None)` 严格定位。不存在的时间
  捕获 `NonExistentTimeError`，逐分钟向后推到第一个存在的时刻并返回 `exists=False`（`src/croniter/croniter.py:197-205`）；歧义时间
  捕获 `AmbiguousTimeError`，构造“更近/更远”两个实例（`is_dst=not is_prev` 与 `is_dst=is_prev`），保留仍然是游标后继/前驱的那个
  （`src/croniter/croniter.py:206-215`）。
- **PEP 495 路径**（zoneinfo/dateutil，`src/croniter/croniter.py:218-233`）：先用 `fold=1 if is_prev else 0` 构造 `result`
  （`src/croniter/croniter.py:218`）；不存在则逐分钟 `+=1` 直到 `datetime_exists` 为真，返回 `exists=False`
  （`src/croniter/croniter.py:219-222`）；存在时再构造相反 fold 的 `farther`，两个实例 UTC offset 不同说明处于歧义小时，同样用
  `_is_successor`（`src/croniter/croniter.py:161-167`，统一转 UTC 严格比较）选出仍在正确方向上的那一个（`src/croniter/croniter.py:224-232`）。

补完时区后 `_calc` 还有两层兜底（`src/croniter/croniter.py:787-819`）：

1. **不存在时间**：若候选本身不存在，且“向后推到存在”后已不是后继（固定小时的任务常见），或者小时字段是 `*`，则递归调 `_calc` 找下一个
   匹配且存在的墙钟时间（`src/croniter/croniter.py:787-797`）。
2. **offset 发生变化**：`_timezone_delta(now, aware_time)` 非零（`src/croniter/croniter.py:170-176`、`src/croniter/croniter.py:799-802`），
   说明跨了 DST 边界；于是把 `now` 的墙钟时间直接加上这个 offset delta，再递归求一遍“另一个 UTC offset 下”的候选
   （`src/croniter/croniter.py:804-810`）。另一个候选若不再是游标后继就丢弃（`src/croniter/croniter.py:812-814`），否则两个候选按
   方向取更近者（`src/croniter/croniter.py:816-819`）。秋季重复小时能被发出两次，靠的就是这条 alternative 递归（见第 7 节风险点 2）。

### 3.1 America/New_York 春季缺失小时

2026-03-08 02:00 本地钟跳到 03:00（-05:00 → -04:00），02:00-02:59 不存在。对 `30 2 * * *`（固定 2:30）：

- `_add_tzinfo` 把不存在的 02:30 逐分钟推到 **03:00-04:00**，并报告 `exists=False`（`src/croniter/croniter.py:219-222`）。
- 因为小时字段不是 `*` 且 03:00-04:00 仍是游标后继，第 1 层兜底不触发；第 2 层的 alternative 候选（前向时对应次日 02:30）不再是
  后继，被丢弃（`src/croniter/croniter.py:812-814`），最终接受 03:00-04:00。
- 实测（第 7 节可复现）：2026-03-08 00:00-05:00 起 `get_next` 得 `2026-03-08T03:00:00-04:00`；从 06:00-04:00 起 `get_prev` 同样得
  `2026-03-08T03:00:00-04:00`（向后被推到 gap 终点且它仍是前驱），再前一次才是 `2026-03-07T02:30:00-05:00`。

### 3.2 America/New_York 秋季重复小时

2026-11-01 02:00 本地钟倒回 01:00（-04:00 → -05:00），01:00-01:59 出现两次。对 `30 1 * * *`，zoneinfo 下实测连续两次 `get_next`
依次得到 `2026-11-01T01:30:00-04:00 fold=0` 与 `2026-11-01T01:30:00-05:00 fold=1`；连续 `get_prev` 顺序相反
（`fold=1` 先到，`fold=0` 后到）。机制是：第一次搜索把 fold 选成离游标近的一侧；跨 offset 后第 2 层 alternative 递归以“加完 offset
delta 的墙钟时间”为新起点，第二次匹配到同一墙钟的另一 fold（`src/croniter/croniter.py:804-819`）。pytz 路径下两个实例由两次
`localize(..., is_dst=...)` 区分（`src/croniter/croniter.py:206-215`）；实测 pytz 返回的两个 datetime `fold` 属性都为 0，但 UTC offset
不同——pytz 不靠 fold 编码歧义。

## 4. 当前状态的提交

- 游标有三个：`start_time`（构造起点）、`dst_start_time`、`cur`（搜索位置）。它们只在 `set_current` 中被一起赋值
  （`src/croniter/croniter.py:374-376`）；构造后迭代过程中没有任何代码再写 `start_time`/`dst_start_time`（构造器只在初始化时把它们清零，
  `src/croniter/croniter.py:324-327`）。
- 每次 `_get_next` **开始时**先执行 `set_current(start_time, force=True)`（`src/croniter/croniter.py:409`）。由于 `force=True`，只要调用
  时显式给了非 None 的 `start_time`，三个游标会在搜索前被重置；不传 `start_time` 则参数为 None，`set_current` 不改动游标
  （`src/croniter/croniter.py:369-377`），搜索从当前 `cur` 继续。
- 搜索**成功后**才把结果时间戳提交到 `self.cur`，且受 `update_current` 控制（`src/croniter/croniter.py:420-422`）；`get_next/get_prev`
  默认 `update_current=True`（`src/croniter/croniter.py:346`、`src/croniter/croniter.py:355`）。
- 搜索失败抛 `CroniterBadDateError` 时，异常在 `_calc`/`_calc_next` 内部产生（`src/croniter/croniter.py:528-530`、
  `src/croniter/croniter.py:821-823`），`_get_next` 中第 421-422 行的赋值根本不会执行。因此“失败不推进游标”，但要分清两种入口：
  - 不传 `start_time` 的失败：`cur` 完全不变（第 7 节风险点 4、5 实测）。
  - 显式传 `start_time` 的失败：游标在搜索前已被重置到传入值，失败后停在传入值上——这不是“失败推进”，而是“搜索前重置”先发生。
    实测：`0 0 1 1 * 0 2020` 从 2026-06-01 构造，传 `start_time=2026-07-01` 搜索失败后，`get_current()` 是 2026-07-01。
- `update_current=False` 是纯“窥视”：结果照常返回，`cur` 不动（实测：`0 * * * *` 从 00:30 窥视得 01:00，cur 仍为 00:30；
  下一次正常 next 仍从同一游标得到 01:00）。

## 5. 前向/后向搜索应复用什么、不复用什么

- **复用（且必须复用）**：`self.expanded`、`nth_weekday_of_month`、`nearest_weekday`、`self.tzinfo`。方向切换不重新解析表达式，否则
  `H` 会得到另一份调度、`R` 会被重新掷骰子、`#`/`W` 的辅助结构会丢失。搜索内对展开表只用副本（`src/croniter/croniter.py:478-479`），
  并集分支对副本的临时改写不泄漏（`src/croniter/croniter.py:508-517`）。
- **不复用**：每轮搜索的候选计算 `_calc(...)` 是无副作用的纯函数式递归，入参是“这一轮的 now”和展开表副本；DST 兜底里的递归
  （`src/croniter/croniter.py:794-797`、`src/croniter/croniter.py:807-810`）也都不碰 `self.cur`。方向本身只在调用入口写入
  `self._is_prev`（`src/croniter/croniter.py:412`），并作为参数贯穿计算。
- 后向搜索不是“前向搜索再回退”：它有自己的起始偏移（-1 微秒）、最近差函数和 fold 偏好（`src/croniter/croniter.py:547-549`、
  `src/croniter/croniter.py:218`），并集合并时取“更大”的候选（`src/croniter/croniter.py:534-535`）。
- `expand_from_start_time=True` 是唯一让展开依赖时间的模式：构造时把 `dst_start_time` 作为 `from_timestamp` 传入
  （`src/croniter/croniter.py:332-333`），步进表达式的循环下界按起始时间的墙钟分量重定相（`src/croniter/croniter.py:1136-1139`、
  `src/croniter/croniter.py:1337-1360`）；该相位同样在构造时冻结，且此模式下 `get_next(start_time=...)` 被显式禁止
  （`src/croniter/croniter.py:347-350`）。

## 6. 新增测试：固定 seed 的 next、next、prev、next

测试：`src/croniter/tests/test_croniter_understanding.py:28-80`，类
`CroniterUnderstandingCursorExpansionTest.test_understanding_next_next_prev_next_cursor_and_expansion`。

- seed `20260920`、表达式 `R R * * *`、base `2026-01-01T00:00:00+00:00`、显式 UTC（不依赖系统时区，不读私有字段：只用构造器、
  `get_next/get_prev`、`get_current` 和公开文档属性 `expanded`）。
- 构造后展开冻结为 minute=[6]、hour=[12]（`src/croniter/tests/test_croniter_understanding.py:36-39`），即每天 12:06。
- 逐步断言：next→`2026-01-01T12:06+00:00`，next→`2026-01-02T12:06+00:00`，prev→`2026-01-01T12:06+00:00`（**fold=1**，见下），
  next→`2026-01-02T12:06+00:00`；每步之后 `get_current()` 必须等于刚返回的值
  （`src/croniter/tests/test_croniter_understanding.py:49-62`）。
- 四次调用结束后 `expanded` 与构造时逐位相同（`src/croniter/tests/test_croniter_understanding.py:65`），证明方向切换不触发重新展开。
- 重新 `random.seed(20260920)` 后新建第二个实例，展开相同且首个 next 也是 12:06
  （`src/croniter/tests/test_croniter_understanding.py:70-75`），证明随机值在构造时抽取、与已有实例游标无关。
- 每步 ISO 时间、fold、offset 通过 `warnings.warn` 打到 pytest warnings summary
  （`src/croniter/tests/test_croniter_understanding.py:77-80`），因此验收命令（默认捕获警告）的输出里即可看到完整时间线，无需 `-s`。

关于 prev 那一步的 **fold=1**：这是源码决定的——PEP 495 路径无条件用 `fold=1 if is_prev else 0`
（`src/croniter/croniter.py:218`），即使在 UTC 这种没有歧义的时刻，fold 位也保留为 1；UTC 下 fold 不影响 offset 与相等性，故 ISO、
`utcoffset()` 和时间戳完全一致。

### 6.1 状态时间线

| 时刻 | 调用 | 返回（ISO, fold, utcoffset） | 调用后 cur | expanded |
| --- | --- | --- | --- | --- |
| t0 | 构造（base） | — | 2026-01-01T00:00:00+00:00 | `[[6],[12],["*"],["*"],["*"]]` |
| t1 | next | 2026-01-01T12:06:00+00:00, fold=0, 0:00:00 | 同返回值 | 不变 |
| t2 | next | 2026-01-02T12:06:00+00:00, fold=0, 0:00:00 | 同返回值 | 不变 |
| t3 | prev | 2026-01-01T12:06:00+00:00, fold=1, 0:00:00 | 同返回值（fold 位不影响时间戳） | 不变 |
| t4 | next | 2026-01-02T12:06:00+00:00, fold=0, 0:00:00 | 同返回值 | 不变 |

## 7. 五个可复现风险点

下列每条均由源码逻辑与本机实测双重核对；时间均为 zoneinfo 的 `America/New_York`（风险点 3 用 UTC）。“预期时间”是当前版本实际行为，
即使用者需要意识到的反直觉点。

### 风险点 1：春季 gap 上固定小时任务被“平移”，且 next/prev 落到同一瞬间

- 表达式：`30 2 * * *`
- base/timezone：`2026-03-08T00:00:00` / `America/New_York`（另用 06:00 做反向）
- 调用序列：`get_next(datetime)`；或从 06:00-04:00 起 `get_prev(datetime)` 两次
- 预期：next = `2026-03-08T03:00:00-04:00`（02:30 不存在，被逐分钟推到 gap 终点）；prev 第一次也是 `2026-03-08T03:00:00-04:00`，
  第二次才是 `2026-03-07T02:30:00-05:00`。
- 风险含义：croniter 对不存在的调度时间选择“平移到存在时刻”而非“跳过当天”；gap 当天的同一瞬间既是 next 又是 prev，方向来回切换会反复
  命中它。依据：`src/croniter/croniter.py:219-222`、`src/croniter/croniter.py:787-814`。

### 风险点 2：秋季重复小时发出两次，fold/offset 依赖 tz 后端，prev 的 fold 偏好固定

- 表达式：`30 1 * * *`
- base/timezone：`2026-11-01T00:00:00` / `America/New_York`
- 调用序列：连续 `get_next(datetime)` 两次；或从 05:00-05:00 起连续 `get_prev(datetime)` 两次
- 预期（zoneinfo）：next 依次 `2026-11-01T01:30:00-04:00 fold=0`、`2026-11-01T01:30:00-05:00 fold=1`；prev 顺序相反。pytz 下两条
  都显示 `fold=0`，靠 offset 区分。
- 风险含义：墙钟字符串相同的相邻两次触发，若下游只记录 naive 时间或只比 ISO 前缀会误判为重复/丢触发；fold 的编码还随 pytz/zoneinfo
  不同。依据：`src/croniter/croniter.py:206-232`、`src/croniter/croniter.py:799-819`。

### 风险点 3：`R` 随机展开只在构造时掷一次，无法靠“同一个对象继续迭代”换调度

- 表达式：`R R * * *`（或任意含 `r/R` 的字段）
- base/timezone：`2026-01-01T00:00:00` / UTC
- 调用序列：`random.seed(20260920)` → 构造对象 → 多次 `get_next/get_prev`；再 reseed 构造第二个对象对比
- 预期：构造时展开冻结为 minute=6、hour=12（每天 12:06），方向切换、继续迭代都不再变化；同 seed 新对象展开完全相同；不同 base 的
  新对象展开仍相同（因为 `do` 不读时间）。
- 风险含义：想重新随机调度必须重新构造（并自行管理全局 `random` 种子）；长期持有的对象其“随机”时刻其实是固定的。依据：
  `src/croniter/croniter.py:1508-1509`、`src/croniter/croniter.py:981-985`、`src/croniter/croniter.py:329-335`；
  新增测试 `src/croniter/tests/test_croniter_understanding.py:36-75`。

### 风险点 4：`day_or=False` 让“单侧不可满足”变成整体无结果；`day_or=True` 则照常触发

- 表达式：`0 0 13 1,4,7,10 5 0 2026`（2026 年的 1/4/7/10 月都没有“13 号且星期五”）
- base/timezone：`2026-01-02T00:00:00` / naive（无 DST 干扰），`max_years_between_matches=1`
- 调用序列：`day_or=False` 构造后 `get_next(datetime)`；再用 `day_or=True` 构造后 `get_next(datetime)` / 从年底 `get_prev(datetime)`
- 预期：交集模式抛 `CroniterBadDateError: failed to find next date`，且不传 start_time 时游标保持 2026-01-02 不变；并集模式 next =
  `2026-01-09T00:00:00`（1 月第一个周五），prev（从 2026-12-31）= `2026-10-30T00:00:00`（10 月最后一个周五）。
- 风险含义：同样的表达式，仅 `day_or` 不同，一个整年无触发且抛异常，一个按周五稳定触发；失败分支还分 `#`/`W` 的“干净分裂”与否
  （`src/croniter/croniter.py:500-524`）。依据：`src/croniter/croniter.py:482-536`、`src/croniter/croniter.py:821-823`。

### 风险点 5：失败搜索是否动游标，取决于有没有显式传 start_time；年字段越界即终止

- 表达式：`0 0 1 1 * 0 2020`（7 段，年字段固定 2020）
- base/timezone：`2026-06-01T00:00:00` / naive，`max_years_between_matches=1`
- 调用序列 A：不传 start_time 直接 `get_next()`（抛错），随后 `get_current()`，再以 `start_time=2019-12-31` 调 `get_next(datetime)`。
  调用序列 B：以 `start_time=2026-07-01` 调 `get_next(datetime)`（抛错），随后 `get_current()`。
- 预期：A 中抛错后 cur 仍是 2026-06-01；改从 2019-12-31 搜索成功得 `2020-01-01T00:00:00`。B 中抛错后 cur 停在 **2026-07-01**
  （搜索前的 `set_current` 已生效）。
- 风险含义：“失败不推进游标”只在省略 start_time 时成立；把 start_time 作为每次调用参数传入的用法，会让失败也把锚点挪走。年字段在
  域外时最近差返回 `None` 直接终止（`src/croniter/croniter.py:843-846`、`src/croniter/croniter.py:864-867`），再叠加
  `max_years_between_matches` 的年限（`src/croniter/croniter.py:761`、`src/croniter/croniter.py:821-823`）。依据：
  `src/croniter/croniter.py:366-377`、`src/croniter/croniter.py:406-425`。

### 附：命中点的严格不等价边界（方向不对称的基础事实）

`0 0 * * *` 从恰好 `2026-01-01T00:00` 出发：next 得 `2026-01-02T00:00`，prev 得 `2025-12-31T00:00`。当前命中点对两个方向都不可见，
源于 `src/croniter/croniter.py:547-557` 的 ±1 偏移。

## 8. 推测与未验证项

- `_add_tzinfo` 两处 `# TODO: Check negative DST`（`src/croniter/croniter.py:209`、`src/croniter/croniter.py:228`）表明负 DST/特殊
  过渡的情形作者自己标注了未充分验证；本文没有实测这类时区，不断言其行为正确。
- `assert (closer ... > farther ...) == is_prev` 等断言依赖“常规 DST 只跳 1 小时且方向固定”；跳 30 分钟（如 Australia/Lord_Howe）或
  非单调过渡时断言是否总成立，本文未实测。
- `R` 使用进程级全局 `random`：本文证明“固定 seed 可复现、构造后冻结”，但没有分析多线程/第三方库同时消费全局 PRNG 时的分布影响，
  这属于使用建议而非源码事实。
- 本文所有时间基于 2026 年的 IANA 时区规则实测；若未来 tzdata 改写规则，风险点 1/2 的具体日期需按当时数据重算（机制结论不依赖具体年份）。

## 9. 可直接复制的复现命令

准备阶段（不计入演示，只需一次；需要 python-dateutil/pytz/zoneinfo 数据，无需外部服务、无需改环境变量）：

```bash
python3 -m pip install -e . pytest python-dateutil pytz
```

验收（在仓库根目录直接运行，退出码为 0，并打印新增用例名与逐步 ISO/fold/offset 时间线）：

```bash
python3 -m pytest -q -k "understanding"
```

五个风险点的一键脚本（同样从仓库根目录运行；不依赖系统 TZ，时区全部显式指定）：

```bash
python3 - <<"PY"
import datetime, random, zoneinfo
from croniter import croniter, CroniterBadDateError
NY = zoneinfo.ZoneInfo("America/New_York")
UTC = datetime.timezone.utc

def line(tag, d):
    print(tag, d.isoformat(), "fold=" + str(d.fold), "utcoffset=" + str(d.utcoffset()))

# 风险点 1：春季缺失小时
line("R1 next    :", croniter("30 2 * * *", datetime.datetime(2026,3,8,0,0,tzinfo=NY)).get_next(datetime.datetime))
c = croniter("30 2 * * *", datetime.datetime(2026,3,8,6,0,tzinfo=NY))
line("R1 prev(1) :", c.get_prev(datetime.datetime))
line("R1 prev(2) :", c.get_prev(datetime.datetime))

# 风险点 2：秋季重复小时（两个 fold）
c = croniter("30 1 * * *", datetime.datetime(2026,11,1,0,0,tzinfo=NY))
line("R2 next(1) :", c.get_next(datetime.datetime))
line("R2 next(2) :", c.get_next(datetime.datetime))

# 风险点 3：R 只在构造时抽取
random.seed(20260920)
r = croniter("R R * * *", datetime.datetime(2026,1,1,tzinfo=UTC), ret_type=datetime.datetime)
print("R3 expanded:", r.expanded)
line("R3 next    :", r.get_next(datetime.datetime))

# 风险点 4：day_or 交集不可满足 vs 并集
expr = "0 0 13 1,4,7,10 5 0 2026"
try:
    croniter(expr, datetime.datetime(2026,1,2), day_or=False, max_years_between_matches=1).get_next(datetime.datetime)
except CroniterBadDateError as exc:
    print("R4 AND     : raises", exc)
line("R4 OR next :", croniter(expr, datetime.datetime(2026,1,2), day_or=True, max_years_between_matches=1).get_next(datetime.datetime))

# 风险点 5：失败搜索与游标
c5 = croniter("0 0 1 1 * 0 2020", datetime.datetime(2026,6,1), max_years_between_matches=1)
try:
    c5.get_next()
except CroniterBadDateError:
    print("R5 cur after failed next (no start_time):", c5.get_current(datetime.datetime).isoformat())
line("R5 success :", c5.get_next(datetime.datetime, start_time=datetime.datetime(2019,12,31)))
try:
    c5.get_next(datetime.datetime, start_time=datetime.datetime(2026,7,1))
except CroniterBadDateError:
    print("R5 cur after failed next (start_time=07-01):", c5.get_current(datetime.datetime).isoformat())
PY
```
