# 秋假团退改的原因分布与可退条款对照

旅行社按原票权益统计退改。日期为 2026 年当地日期，价格和款项为元。

## 资料交换

`data/samples.json` 是合成样册。`fields` 给出有序列名，`rows` 按该顺序记录数据，`meaning` 标明单位或来源区别。没有填写的值用 `null` 表示。

- `terms`：出票时的生效期；停场由停场记录确认。 列：`version`, `first_sale`, `free_refund_hours`, `switch_count`, `refundable`。
- `slots`：regime 是实际停场规则标识。 列：`slot`, `city`, `date`, `seats`, `weather`, `regime`。
- `orders`：root 为原购买根，ticket 是首次凭证。 列：`root`, `ticket`, `slot`, `booked`, `version`, `seats`, `paid`。
- `events`：退改链保留两个日期，空原因表示待核。 列：`ticket`, `new_ticket`, `new_slot`, `action`, `reason`, `effective`, `reported`。
- `announcements`：已发生的停场公告。 列：`slot`, `reason`, `published`。
- `payments`：退款最终出款与请求分开。 列：`ticket`, `amount`, `state`, `date`。
- `reserves`：合作方可调的共同退款暂留上限。 列：`month`, `reserve_cap`。

## 检视

使用 Python 3.11，资料读取不依赖第三方包。

```sh
python3 source_book.py data/samples.json
python3 -m unittest discover -s tests -v
```

资料读取命令列出各表记录数；测试检查交叉引用与格式，不生成分析结果。

## 截点分析

`trip_audit` 包把原始购票、改期链、成行核销、停场公告与退款批次串联成以
购买根（root）为单位的台账，并按截点生成报告。续期凭证不另立根，风险人群
只按根计一次。

```sh
# 打印完整 JSON 报告
python3 -m trip_audit.report data/samples.json --cutoff 11-21

# 分文件写出，并标注新条款适用的新购起始日（只影响该日之后新购）
python3 -m trip_audit.report data/samples.json --cutoff 11-30 \
    --new-term-sale-date 11-25 --out build/report
```

### 口径约定

- 事件的 `effective` 是业务生效日（用于路径与提前量），`reported` 是登记可见日；
  截点只纳入 `reported <= cutoff` 的事件和支付批次，被屏蔽的资料不进入任何统计。
- 支付到账日不是取消原因日：原因以事件链与停场公告为准；待出款、部分到账差额
  仍计入暂留款，不能当作退款不会发生。
- 空原因保留为待核；同一根出现两个确知原因时归为“多个原因相继发生”，不合并。
- 仍在免费退款窗口、尚无动作的订单单列为开放未决，不记成“无退款”。
- 出票条款按购票日适用并随快照保存，历史购买不会被新条款重写。

### 报告文件

| 文件 | 内容 |
| --- | --- |
| `ledger.json` | 各根截点去向、原因轨迹、资金（到账/暂留/待出款）与可见性审计 |
| `cohorts.json` | 按团期、提前量桶、条款版本的首入路径累计占比、留置资金与未决范围 |
| `comparison.json` | 新旧条款对照、严格/近似可比团期对、停场规则混淆日、无样本条款声明 |
| `scenarios.json` | 维持现条款、保留改期权益、允许更晚全退三情景的资金与席位区间，及联合饱和警示与需协商订单 |
| `pending.json` | 财务、产品、客服三视图共享的同一批未决根与金额 |
| `policy_replay.json` | 截点时的条款事实、逐根政策依据与无泄漏审计，供下一个截点重看 |

情景结果只给区间并标注不确定来源；改期存量权益先占可承接场次，当潜在改期
用途与退款暂留同时压满时，报告给出联合饱和警示，不能两边各取最好情景。
