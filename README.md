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
