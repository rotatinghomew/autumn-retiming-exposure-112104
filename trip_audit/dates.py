"""2026 年本地日期工具。

交换资料中的日期都是 ``月-日`` 字符串（如 ``11-13``），年份固定为 2026。
全部分析以 ``datetime.date`` 比较，输出再转回交换格式。
"""
from __future__ import annotations

from datetime import date

YEAR = 2026


def parse_date(value):
    """把 ``MM-DD`` 字符串解析为 2026 年的 date；None 原样返回。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    month, day = str(value).split("-")
    return date(YEAR, int(month), int(day))


def fmt(value):
    """date 转回交换资料使用的 ``MM-DD`` 字符串。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return f"{value.month:02d}-{value.day:02d}"


def parse_month(value):
    """月份字段归一化为两位字符串。"""
    return f"{int(value):02d}"


def lead_hours(booked, slot_date):
    """购票时点到团期的提前小时数（按整天折算）。"""
    return (slot_date - booked).total_seconds() / 3600.0
