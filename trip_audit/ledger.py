"""原始购票、改期链、核销、停场公告与退款批次的串联台账。

关键口径（与 README 及样册 meaning 一致）：

- 每个订单以 ``root`` 为原购买根，``ticket`` 是首次凭证；改期产生新凭证，
  续期订单不另立根，风险人群只按根计一次。
- 事件有两个日期：``effective`` 是业务生效日（用于排序、提前量与首入路径），
  ``reported`` 是登记/可见日（决定某截点能否看到该条资料）。
- 退款到账日期（payments.date）只是出款时间，不是取消原因发生日期；
  原因以事件链和停场公告为准，待出款仍是期末未决，不能当作不会发生。
- 空原因表示待核，不能补写臆测原因。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .dates import fmt, parse_date

# 事件动作
RESCHEDULE = "改期"
CONFIRM_RESCHEDULE = "确认改期"
REFUND_REQUEST = "请求退款"
REASON_MEMO = "原因补记"
CHECK_IN = "核销"

RESCHEDULE_ACTIONS = frozenset({RESCHEDULE, CONFIRM_RESCHEDULE})

# 根订单去向（首入路径）
PATH_RESCHEDULE = "改期"
PATH_REFUND = "退款"
PATH_CHECK_IN = "成行核销"
PATH_WAITING = "仍在等待"

# 原因确定性分桶
CERT_KNOWN = "确知原因"
CERT_SEQUENCE = "多个原因相继发生"
CERT_PENDING = "仍在等待（原因待核）"

# 支付批次状态
PAY_PAID_STATES = frozenset({"到账", "部分到账"})
PAY_OPEN_STATES = frozenset({"待出款"})


@dataclass(frozen=True)
class Term:
    version: str
    first_sale: object
    free_refund_hours: float
    switch_count: int
    refundable: float


@dataclass(frozen=True)
class Slot:
    slot: str
    city: str
    date: object
    seats: int
    weather: str
    regime: str


@dataclass(frozen=True)
class Order:
    root: str
    ticket: str
    slot: str
    booked: object
    version: str
    seats: int
    paid: float


@dataclass(frozen=True)
class Event:
    ticket: str
    new_ticket: str | None
    new_slot: str | None
    action: str
    reason: str | None
    effective: object
    reported: object


@dataclass(frozen=True)
class Payment:
    ticket: str
    amount: float
    state: str
    date: object


@dataclass(frozen=True)
class Announcement:
    slot: str
    reason: str
    published: object


@dataclass
class Book:
    """已解析并建立索引的交换资料。"""

    terms: dict
    slots: dict
    orders: dict
    events_by_ticket: dict
    payments_by_ticket: dict
    announcements_by_slot: dict
    ticket_to_root: dict
    ticket_to_order: dict
    roots: dict
    reserves: dict
    raw: dict = field(repr=False, default_factory=dict)


def _index(rows, key, model):
    return {row[key]: model(**row) for row in rows}


def build_book(index: dict) -> Book:
    """把 :func:`source_book.load` 的行式资料解析成带索引的台账。"""
    terms = {}
    for row in index["terms"]:
        terms[row["version"]] = Term(
            version=row["version"],
            first_sale=parse_date(row["first_sale"]),
            free_refund_hours=float(row["free_refund_hours"]),
            switch_count=int(row["switch_count"]),
            refundable=float(row["refundable"]),
        )

    slots = _index(
        (
            {
                **row,
                "date": parse_date(row["date"]),
                "seats": int(row["seats"]),
            }
            for row in index["slots"]
        ),
        "slot",
        Slot,
    )
    orders = _index(
        ({**row, "booked": parse_date(row["booked"])} for row in index["orders"]),
        "ticket",
        Order,
    )

    events_by_ticket: dict[str, list[Event]] = {}
    for row in index["events"]:
        event = Event(
            ticket=row["ticket"],
            new_ticket=row["new_ticket"],
            new_slot=row["new_slot"],
            action=row["action"],
            reason=row["reason"],
            effective=parse_date(row["effective"]),
            reported=parse_date(row["reported"]),
        )
        events_by_ticket.setdefault(event.ticket, []).append(event)
    for bucket in events_by_ticket.values():
        bucket.sort(key=lambda e: (e.effective, e.reported, e.action))

    payments_by_ticket: dict[str, list[Payment]] = {}
    for row in index["payments"]:
        payment = Payment(
            ticket=row["ticket"],
            amount=float(row["amount"]),
            state=row["state"],
            date=parse_date(row["date"]),
        )
        payments_by_ticket.setdefault(payment.ticket, []).append(payment)
    for bucket in payments_by_ticket.values():
        bucket.sort(key=lambda p: (p.date, p.state))

    announcements_by_slot: dict[str, list[Announcement]] = {}
    for row in index["announcements"]:
        announcement = Announcement(
            slot=row["slot"],
            reason=row["reason"],
            published=parse_date(row["published"]),
        )
        announcements_by_slot.setdefault(announcement.slot, []).append(
            announcement
        )

    # 凭证 -> 根、凭证 -> 订单
    ticket_to_root = {order.ticket: order.root for order in orders.values()}
    ticket_to_order = dict(orders)
    for events in events_by_ticket.values():
        for event in events:
            if event.new_ticket is not None:
                ticket_to_root.setdefault(event.new_ticket, ticket_to_root.get(event.ticket))
                if event.ticket in ticket_to_order:
                    ticket_to_order.setdefault(
                        event.new_ticket, ticket_to_order[event.ticket]
                    )

    roots: dict[str, list[str]] = {}
    for ticket, root in ticket_to_root.items():
        roots.setdefault(root, []).append(ticket)
    for root in roots:
        roots[root].sort()

    reserves = {
        str(int(row["month"])): float(row["reserve_cap"]) for row in index["reserves"]
    }

    return Book(
        terms=terms,
        slots=slots,
        orders={order.ticket: order for order in orders.values()},
        events_by_ticket=events_by_ticket,
        payments_by_ticket=payments_by_ticket,
        announcements_by_slot=announcements_by_slot,
        ticket_to_root=ticket_to_root,
        ticket_to_order=ticket_to_order,
        roots=roots,
        reserves=reserves,
        raw=index,
    )


def walk_chain(book: Book, root: str):
    """按改期动作顺序返回根下的凭证链。"""
    first = next(
        ticket
        for ticket in book.roots[root]
        if ticket in book.orders and book.orders[ticket].root == root
    )
    chain = [first]
    current = first
    seen = {first}
    while True:
        next_ticket = None
        for event in book.events_by_ticket.get(current, []):
            if event.action in RESCHEDULE_ACTIONS and event.new_ticket is not None:
                next_ticket = event.new_ticket
                break
        if next_ticket is None or next_ticket in seen:
            break
        chain.append(next_ticket)
        seen.add(next_ticket)
        current = next_ticket
    return chain


def slot_announcement(book: Book, slot_id: str):
    """该团期最早的已登记停场公告；没有则 None。"""
    bucket = book.announcements_by_slot.get(slot_id)
    if not bucket:
        return None
    return sorted(bucket, key=lambda a: a.published)[0]


@dataclass
class RootRecord:
    """单个购买根截至某截点的去向快照。"""

    root: str
    ticket_chain: list
    version: str
    booked: object
    origin_slot: str
    current_slot: str | None
    seats: int
    paid: float
    first_path: str
    first_action: str | None
    first_effective: object | None
    first_reported: object | None
    reason_trail: list
    certainty: str
    is_terminal: bool
    has_refund_request: bool
    reschedule_count: int
    confirmed_reschedule_count: int
    reschedule_events: list
    current_ticket: str
    # 退款资金（元）
    requested_amount: float
    arrived_amount: float
    held_amount: float
    open_payment_amount: float
    payment_states: list
    # 资料可见性
    hidden_event_count: int
    hidden_payment_count: int
    slot_announced_at_cutoff: bool
    announcement_reason: str | None
    announcement_published: object | None
    cutoff: object

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        for key in ("booked", "first_effective", "first_reported", "announcement_published", "cutoff"):
            data[key] = fmt(data[key])
        data["reason_trail"] = [
            {"reason": reason, "effective": fmt(effective), "reported": fmt(reported)}
            for reason, effective, reported in self.reason_trail
        ]
        data["reschedule_events"] = [
            {
                "ticket": e.ticket,
                "new_ticket": e.new_ticket,
                "new_slot": e.new_slot,
                "action": e.action,
                "reason": e.reason,
                "effective": fmt(e.effective),
                "reported": fmt(e.reported),
            }
            for e in self.reschedule_events
        ]
        data["payment_states"] = list(self.payment_states)
        return data


def as_of(book: Book, cutoff) -> dict:
    """生成截点台账：只纳入 ``reported <= cutoff`` 的事件和支付批次。

    返回到根记录字典，以及被截点屏蔽的资料计数（用于无泄漏自检）。
    """
    records = {}
    hidden_events = 0
    hidden_payments = 0

    for root, tickets in book.roots.items():
        chain = walk_chain(book, root)
        order = book.orders[chain[0]]
        visible_events: list[Event] = []
        hidden = 0
        for ticket in chain:
            for event in book.events_by_ticket.get(ticket, []):
                if event.reported <= cutoff:
                    visible_events.append(event)
                else:
                    hidden += 1
        hidden_events += hidden
        visible_events.sort(key=lambda e: (e.effective, e.reported, e.action))

        visible_payments: list[Payment] = []
        hidden_pay = 0
        for ticket in chain:
            for payment in book.payments_by_ticket.get(ticket, []):
                if payment.date <= cutoff:
                    visible_payments.append(payment)
                else:
                    hidden_pay += 1
        hidden_payments += hidden_pay

        substantive = [e for e in visible_events if e.action != REASON_MEMO]
        first_event = substantive[0] if substantive else None
        if first_event is None:
            first_path = PATH_WAITING
        elif first_event.action in RESCHEDULE_ACTIONS:
            first_path = PATH_RESCHEDULE
        elif first_event.action == REFUND_REQUEST:
            first_path = PATH_REFUND
        elif first_event.action == CHECK_IN:
            first_path = PATH_CHECK_IN
        else:  # pragma: no cover - 防御性分支
            first_path = PATH_WAITING

        # 原因轨迹：业务动作上的原因 + 原因补记，按生效日排序，保留空原因待核
        reason_trail = []
        for event in visible_events:
            if event.action == CHECK_IN:
                continue
            reason_trail.append((event.reason, event.effective, event.reported))

        distinct_reasons = [reason for reason, _, _ in reason_trail if reason is not None]
        has_blank = any(reason is None for reason, _, _ in reason_trail)
        refund_requested = any(e.action == REFUND_REQUEST for e in visible_events)
        checked_in = any(e.action == CHECK_IN for e in visible_events)

        if checked_in:
            certainty = CERT_KNOWN
        elif first_event is None:
            certainty = CERT_PENDING
        elif len(set(distinct_reasons)) >= 2:
            certainty = CERT_SEQUENCE
        elif distinct_reasons:
            certainty = CERT_KNOWN
        else:
            certainty = CERT_PENDING

        reschedule_events = [
            e for e in visible_events if e.action in RESCHEDULE_ACTIONS
        ]
        confirmed_reschedule = [
            e for e in reschedule_events if e.action == CONFIRM_RESCHEDULE
        ]
        is_terminal = checked_in

        # 当前凭证与当前团期：顺着已可见的改期链推进
        current_ticket = chain[0]
        current_slot = order.slot
        for event in reschedule_events:
            if event.new_ticket is not None:
                current_ticket = event.new_ticket
                current_slot = event.new_slot

        arrived = sum(
            p.amount for p in visible_payments if p.state in PAY_PAID_STATES
        )
        open_amount = sum(
            p.amount for p in visible_payments if p.state in PAY_OPEN_STATES
        )
        # 请求退款后，尚未到账的票面金额是合作方仍占着的暂留款
        requested_amount = order.paid if refund_requested else 0.0
        held_amount = max(requested_amount - arrived, 0.0)

        announcement = slot_announcement(book, current_slot) if current_slot else None
        slot_announced = bool(announcement and announcement.published <= cutoff)

        records[root] = RootRecord(
            root=root,
            ticket_chain=chain,
            version=order.version,
            booked=order.booked,
            origin_slot=order.slot,
            current_slot=current_slot,
            seats=order.seats,
            paid=order.paid,
            first_path=first_path,
            first_action=first_event.action if first_event else None,
            first_effective=first_event.effective if first_event else None,
            first_reported=first_event.reported if first_event else None,
            reason_trail=reason_trail,
            certainty=certainty,
            is_terminal=is_terminal,
            has_refund_request=refund_requested,
            reschedule_count=len(reschedule_events),
            confirmed_reschedule_count=len(confirmed_reschedule),
            reschedule_events=reschedule_events,
            current_ticket=current_ticket,
            requested_amount=requested_amount,
            arrived_amount=arrived,
            held_amount=held_amount,
            open_payment_amount=open_amount,
            payment_states=[p.state for p in visible_payments],
            hidden_event_count=hidden,
            hidden_payment_count=hidden_pay,
            slot_announced_at_cutoff=slot_announced,
            announcement_reason=announcement.reason if slot_announced else None,
            announcement_published=announcement.published if slot_announced else None,
            cutoff=cutoff,
        )

    return {
        "cutoff": cutoff,
        "records": records,
        "hidden_event_count": hidden_events,
        "hidden_payment_count": hidden_payments,
    }
