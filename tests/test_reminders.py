import json
import threading
from datetime import datetime, timedelta

import pytest

from widget_reloj_clima import reminders
from widget_reloj_clima.reminders import Reminder, ReminderStore

NOW = datetime(2026, 11, 21, 12, 0)


@pytest.fixture
def store(tmp_path):
    return ReminderStore(tmp_path / "reminders.json", now=NOW)


def test_add_assigns_incremental_ids_and_persists(store, tmp_path):
    a = store.add("Uno", NOW + timedelta(hours=1), now=NOW)
    b = store.add("Dos", NOW + timedelta(hours=2), source="telegram", now=NOW)

    assert (a.id, b.id) == (1, 2)
    reloaded = ReminderStore(tmp_path / "reminders.json", now=NOW)
    assert [r.text for r in reloaded.pending()] == ["Uno", "Dos"]
    assert reloaded.pending()[1].source == "telegram"


def test_pending_is_sorted_by_due(store):
    store.add("Tarde", NOW + timedelta(hours=5), now=NOW)
    store.add("Pronto", NOW + timedelta(minutes=5), now=NOW)
    assert [r.text for r in store.pending()] == ["Pronto", "Tarde"]


def test_take_due_returns_each_reminder_once(store):
    store.add("Vencido", NOW - timedelta(minutes=1), now=NOW)
    store.add("Futuro", NOW + timedelta(hours=1), now=NOW)

    first = store.take_due(NOW)
    second = store.take_due(NOW)

    assert [r.text for r in first] == ["Vencido"]
    assert second == ()
    assert [r.text for r in store.pending()] == ["Futuro"]


def test_remove(store):
    r = store.add("Borrar", NOW + timedelta(hours=1), now=NOW)
    assert store.remove(r.id) == r
    assert store.remove(r.id) is None
    assert store.pending() == ()


def test_snooze_reschedules_and_reactivates(store):
    r = store.add("Posponer", NOW - timedelta(minutes=1), now=NOW)
    store.take_due(NOW)

    updated = store.snooze(r.id, 10, NOW)

    assert updated.due == NOW + timedelta(minutes=10)
    assert store.pending() == (updated,)
    assert store.snooze(999, 10, NOW) is None


def test_version_changes_on_every_write(store):
    v0 = store.version
    r = store.add("x", NOW + timedelta(hours=1), now=NOW)
    store.remove(r.id)
    assert store.version == v0 + 2


def test_old_notified_reminders_are_pruned_on_load(tmp_path):
    path = tmp_path / "reminders.json"
    old = Reminder(1, "viejo", NOW - timedelta(days=30), NOW - timedelta(days=31), notified=True)
    recent = Reminder(2, "reciente", NOW - timedelta(days=1), NOW - timedelta(days=2), notified=True)
    path.write_text(json.dumps([old.to_json(), recent.to_json()]), encoding="utf-8")

    store = ReminderStore(path, now=NOW)

    assert store.add("nuevo", NOW + timedelta(hours=1), now=NOW).id == 3


@pytest.mark.parametrize("content", ["{roto", '{"a": 1}', '[{"id": "x"}]'])
def test_corrupt_or_invalid_file_starts_empty(tmp_path, content):
    path = tmp_path / "reminders.json"
    path.write_text(content, encoding="utf-8")
    assert ReminderStore(path, now=NOW).pending() == ()


def test_limit_of_pending_reminders(store, monkeypatch):
    monkeypatch.setattr(reminders, "MAX_REMINDERS", 2)
    store.add("a", NOW + timedelta(hours=1), now=NOW)
    store.add("b", NOW + timedelta(hours=1), now=NOW)
    with pytest.raises(ValueError, match="demasiados"):
        store.add("c", NOW + timedelta(hours=1), now=NOW)


def test_concurrent_adds_keep_unique_ids(store):
    def worker():
        for _ in range(20):
            store.add("x", NOW + timedelta(hours=1), now=NOW)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ids = [r.id for r in store.pending()]
    assert len(ids) == 100 and len(set(ids)) == 100


def test_default_path_is_in_app_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert reminders.reminders_path().parent.parent == tmp_path
