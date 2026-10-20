from datetime import datetime
from zoneinfo import ZoneInfo

from bot.usage import SqliteUsageStore, day_key, next_reset

CHI = ZoneInfo("America/Chicago")


async def test_limit_is_enforced_and_counts_down():
    store = SqliteUsageStore(":memory:")
    lefts = [await store.try_consume(2, "2026-10-20", 5) for _ in range(6)]
    assert lefts == [4, 3, 2, 1, 0, None]
    assert await store.used(2, "2026-10-20") == 5


async def test_users_and_days_are_separate():
    store = SqliteUsageStore(":memory:")
    for _ in range(5):
        await store.try_consume(2, "2026-10-20", 5)
    assert await store.try_consume(3, "2026-10-20", 5) == 4
    assert await store.try_consume(2, "2026-10-21", 5) == 4


async def test_refund_gives_a_call_back_but_not_below_zero():
    store = SqliteUsageStore(":memory:")
    for _ in range(5):
        await store.try_consume(2, "d", 5)
    await store.refund(2, "d")
    assert await store.try_consume(2, "d", 5) == 0
    await store.refund(9, "d")  # never used: no row, no error
    assert await store.used(9, "d") == 0


async def test_persists_to_disk(tmp_path):
    path = str(tmp_path / "sub" / "usage.db")
    store = SqliteUsageStore(path)
    await store.try_consume(2, "d", 5)
    store.close()
    assert await SqliteUsageStore(path).used(2, "d") == 1


async def test_purge_before():
    store = SqliteUsageStore(":memory:")
    await store.try_consume(2, "2026-10-01", 5)
    await store.try_consume(2, "2026-10-20", 5)
    await store.purge_before("2026-10-13")
    assert await store.used(2, "2026-10-01") == 0
    assert await store.used(2, "2026-10-20") == 1


def test_day_key_uses_configured_timezone():
    # 03:30 UTC on the 21st is still the 20th in Chicago.
    now = datetime(2026, 10, 21, 3, 30, tzinfo=ZoneInfo("UTC"))
    assert day_key(CHI, now) == "2026-10-20"
    assert day_key(ZoneInfo("UTC"), now) == "2026-10-21"


def test_next_reset_is_local_midnight():
    now = datetime(2026, 10, 20, 22, 0, tzinfo=CHI)
    assert next_reset(CHI, now) == datetime(2026, 10, 21, 0, 0, tzinfo=CHI)
