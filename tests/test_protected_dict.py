"""Unit tests for :mod:`protected_dict` — the process-wide key/value store."""

import threading

from protected_dict import protected_dict


def test_instance_is_singleton():
    assert protected_dict.instance() is protected_dict.instance()


def test_missing_key_returns_none():
    assert protected_dict.instance().get_value("does-not-exist") is None


def test_set_and_get_value():
    store = protected_dict.instance()
    store.set_value("a", 1)
    assert store.get_value("a") == 1


def test_set_values_and_get_values_preserve_order():
    store = protected_dict.instance()
    store.set_values({"x": 1, "y": 2, "z": 3})
    assert store.get_values(["z", "missing", "x"]) == [3, None, 1]


def test_set_value_stores_a_deep_copy():
    store = protected_dict.instance()
    original = {"nested": [1, 2]}
    store.set_value("k", original)
    original["nested"].append(3)
    assert store.get_value("k") == {"nested": [1, 2]}


def test_get_value_returns_a_deep_copy():
    store = protected_dict.instance()
    store.set_value("k", {"nested": [1]})
    copy = store.get_value("k")
    copy["nested"].append(2)
    assert store.get_value("k") == {"nested": [1]}


def test_get_all_returns_a_copy_of_everything():
    store = protected_dict.instance()
    store.set_values({"a": 1, "b": [1]})
    snapshot = store.get_all()
    assert snapshot == {"a": 1, "b": [1]}
    snapshot["b"].append(2)
    assert store.get_value("b") == [1]


def test_reset_for_testing_clears_values():
    store = protected_dict.instance()
    store.set_value("a", 1)
    protected_dict.reset_for_testing()
    assert store.get_all() == {}


def test_concurrent_writes_are_all_applied():
    store = protected_dict.instance()

    def writer(prefix):
        for i in range(200):
            store.set_value(f"{prefix}-{i}", i)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(store.get_all()) == 5 * 200
