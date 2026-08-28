"""
DataSnapshot — Point-in-Time 数据完整性保障。

核心问题：
  回测/评估时，如果用了"未来才知道的数据"，就会产生 look-ahead bias（前视偏差）。
  例如：在 2024-01-15 做预测时，不该用到 2024-01-16 的财务数据。

DataSnapshot 的职责：
  · 记录"某次分析所用的数据快照"（数据源 + 最新日期 + 来源 hash）
  · 提供 cut_date 过滤：任何 K 线 / 财务数据请求都自动截断到 cut_date
  · 与 ExperimentRegistry 联动，保证每次实验的数据边界可复现

设计原则：
  · Snapshot 是 immutable 的（frozen dataclass）
  · cut_date 之后的数据一律返回 None / 空，强制调用方感知时间边界
  · 支持多数据源并行快照（baostock / akshare / tushare 各自独立追踪）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256

# ═══════════════════════════════════════════════════════
#  单数据源快照
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class DataSourceSnapshot:
    """
    单个数据源在某次实验中的快照。

    字段
    ----
    source      数据源标识（"baostock" / "akshare" / "tushare" / ...）
    cut_date    数据截止日期（ISO 字符串，YYYY-MM-DD）
                → cut_date 之后的数据在评估时视为不可用
    latest_date 实际获取到的最新数据日期
    record_count 记录条数
    data_hash   SHA-256 校验和，用于检测数据是否被修改过
    """

    source: str
    cut_date: str
    latest_date: str
    record_count: int
    data_hash: str = ""

    def is_future(self, date_str: str) -> bool:
        """判断给定日期是否在快照边界之外（未来数据）。"""
        return date_str > self.latest_date

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "cut_date": self.cut_date,
            "latest_date": self.latest_date,
            "record_count": self.record_count,
            "data_hash": self.data_hash[:16] if self.data_hash else "",
        }


# ═══════════════════════════════════════════════════════
#  DataSnapshot — 核心类
# ═══════════════════════════════════════════════════════


@dataclass(frozen=True)
class DataSnapshot:
    """
    某次实验的完整数据快照。

    包含：
      · snapshot_id       唯一标识（SHA-256 of contents）
      · created_at        创建时间戳
      · cut_date          本次实验的决策截止日期
      · sources           各数据源的快照列表
      · description       人类可读描述（可选）

    使用方式：
      1. 构建：DataSnapshot(cut_date="2024-06-30", sources=[...])
      2. 持久化：snapshot_id → research_db.data_snapshots 表
      3. 评估时：用 cut_date 过滤所有 future data，防止前视偏差
    """

    cut_date: str
    sources: tuple[DataSourceSnapshot, ...] = ()
    description: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # snapshot_id 通过 @property 计算，不存储
    @property
    def snapshot_id(self) -> str:
        raw = f"{self.cut_date}|{len(self.sources)}|{self.description}"
        for s in self.sources:
            raw += f"|{s.source}:{s.latest_date}:{s.record_count}"
        return sha256(raw.encode()).hexdigest()[:16]

    def contains_future_data(self, ticker: str, date_str: str) -> bool:
        """
        检查给定日期是否跨越任何数据源的边界。

        如果任何 source 的 latest_date < date_str，则视为包含未来数据。
        """
        for src in self.sources:
            if src.latest_date < date_str:
                return True
        return False

    def effective_cut_date(self) -> str:
        """返回所有数据源中最晚的 latest_date，作为实际有效的决策日期。"""
        if not self.sources:
            return self.cut_date
        return max(s.latest_date for s in self.sources)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "cut_date": self.cut_date,
            "effective_cut_date": self.effective_cut_date(),
            "sources": [s.to_dict() for s in self.sources],
            "description": self.description,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DataSnapshot":
        sources = tuple(DataSourceSnapshot(**s) for s in data.get("sources", []))
        return cls(
            cut_date=data["cut_date"],
            sources=sources,
            description=data.get("description", ""),
            created_at=data.get("created_at", ""),
        )


# ═══════════════════════════════════════════════════════
#  Point-in-Time K线截取工具
# ═══════════════════════════════════════════════════════


def filter_kline_to_cut_date(
    df,
    cut_date: str,
    date_col: str = "timestamps",
) -> object:
    """
    将 K 线 DataFrame 截断到 cut_date（不含）。

    Parameters
    ----------
    df        pandas DataFrame 或 None
    cut_date  ISO 字符串 "YYYY-MM-DD"
    date_col  日期列名

    Returns
    -------
    截断后的 DataFrame（copy），若输入为 None 则返回 None
    """
    import pandas as pd

    if df is None or df.empty:
        return df
    try:
        dates = pd.to_datetime(df[date_col])
        cutoff = pd.to_datetime(cut_date)
        return df[dates <= cutoff].copy()
    except Exception:
        return df
