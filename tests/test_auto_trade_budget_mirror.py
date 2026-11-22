import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.app.integrations.database import DatabaseHub
from backend.app.models import AutoTradeDailyBudget, ReplicationOutbox


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Result:
    def scalar_one_or_none(self):
        return "user-1"


class _Session:
    def __init__(self, *, fail_execute=False, budget=None):
        self.fail_execute = fail_execute
        self.budget = budget
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction()

    async def execute(self, statement):
        if self.fail_execute:
            raise OSError("backup unavailable")
        return _Result()

    async def get(self, model, key):
        assert model is AutoTradeDailyBudget
        assert key == ("user-1", "2026-11-24")
        return self.budget

    def add(self, record):
        self.added.append(record)


class _SessionFactory:
    def __init__(self, factory):
        self.factory = factory

    def __call__(self):
        return self.factory()


def test_partial_budget_settlement_queues_repair_snapshot_for_backup():
    budget = AutoTradeDailyBudget(
        user_id="user-1",
        trading_day="2026-11-24",
        budget_inr=Decimal("1000"),
        reserved_inr=Decimal("0"),
        used_inr=Decimal("200"),
        updated_at=datetime.now(timezone.utc),
    )
    primary_sessions = []

    def primary_factory():
        session = _Session(budget=budget)
        primary_sessions.append(session)
        return session

    hub = DatabaseHub.__new__(DatabaseHub)
    hub.sessions = _SessionFactory(primary_factory)
    hub.backup_sessions = _SessionFactory(
        lambda: _Session(fail_execute=True)
    )

    async def settle():
        with pytest.raises(RuntimeError, match="settlement failed"):
            await hub.settle_auto_trade_budget(
                "user-1", "2026-11-24", 200, commit=True
            )

    asyncio.run(settle())

    queued = [
        record
        for session in primary_sessions
        for record in session.added
        if isinstance(record, ReplicationOutbox)
    ]
    assert len(queued) == 1
    assert queued[0].target == "backup"
    assert queued[0].status == "pending"
    assert queued[0].aggregate_type == "AutoTradeDailyBudget"
    assert queued[0].payload["reserved_inr"] == "0"
    assert queued[0].payload["used_inr"] == "200"
