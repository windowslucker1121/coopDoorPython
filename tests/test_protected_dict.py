"""Tests for protected_dict singleton."""

from __future__ import annotations

import threading
from copy import deepcopy

import pytest

from protected_dict import protected_dict


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_instance_is_same_object(self) -> None:
        a = protected_dict.instance()
        b = protected_dict.instance()
        assert a is b

    def test_instance_returns_protected_dict(self) -> None:
        inst = protected_dict.instance()
        assert isinstance(inst, protected_dict)


# ---------------------------------------------------------------------------
# set_value / get_value
# ---------------------------------------------------------------------------


class TestSetGetValue:
    def test_roundtrip_string(self, gv) -> None:
        gv.set_value("key", "hello")
        assert gv.get_value("key") == "hello"

    def test_roundtrip_none(self, gv) -> None:
        gv.set_value("key", None)
        assert gv.get_value("key") is None

    def test_get_missing_key_returns_none(self, gv) -> None:
        assert gv.get_value("missing_key") is None

    def test_overwrite_value(self, gv) -> None:
        gv.set_value("x", 1)
        gv.set_value("x", 2)
        assert gv.get_value("x") == 2

    def test_mutable_value_is_deep_copied_on_write(self, gv) -> None:
        original = [1, 2, 3]
        gv.set_value("lst", original)
        original.append(4)  # mutate original
        assert gv.get_value("lst") == [1, 2, 3]  # stored copy unaffected

    def test_mutable_value_is_deep_copied_on_read(self, gv) -> None:
        gv.set_value("lst", [1, 2, 3])
        fetched = gv.get_value("lst")
        fetched.append(99)
        assert gv.get_value("lst") == [1, 2, 3]


# ---------------------------------------------------------------------------
# set_values / get_values
# ---------------------------------------------------------------------------


class TestSetGetValues:
    def test_set_values_writes_multiple_keys(self, gv) -> None:
        gv.set_values({"a": 1, "b": 2, "c": 3})
        assert gv.get_value("a") == 1
        assert gv.get_value("b") == 2
        assert gv.get_value("c") == 3

    def test_get_values_returns_list(self, gv) -> None:
        gv.set_values({"x": 10, "y": 20})
        result = gv.get_values(["x", "y"])
        assert result == [10, 20]

    def test_get_values_missing_keys_return_none(self, gv) -> None:
        result = gv.get_values(["missing1", "missing2"])
        assert result == [None, None]

    def test_get_values_preserves_order(self, gv) -> None:
        gv.set_values({"alpha": "a", "beta": "b", "gamma": "g"})
        result = gv.get_values(["gamma", "alpha", "beta"])
        assert result == ["g", "a", "b"]


# ---------------------------------------------------------------------------
# get_all()
# ---------------------------------------------------------------------------


class TestGetAll:
    def test_get_all_returns_all_keys(self, gv) -> None:
        gv.set_values({"k1": 1, "k2": 2})
        d = gv.get_all()
        assert "k1" in d
        assert "k2" in d

    def test_get_all_returns_deep_copy(self, gv) -> None:
        gv.set_value("nested", {"inner": [1, 2]})
        d = gv.get_all()
        d["nested"]["inner"].append(3)
        # Stored value must be unchanged
        assert gv.get_value("nested") == {"inner": [1, 2]}


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_writes_do_not_corrupt(self, gv) -> None:
        errors: list[Exception] = []
        COUNT = 200

        def writer(key: str, values: range) -> None:
            try:
                for v in values:
                    gv.set_value(key, v)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(f"key_{i}", range(COUNT)))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []

    def test_concurrent_reads_and_writes_do_not_raise(self, gv) -> None:
        errors: list[Exception] = []
        gv.set_value("shared", 0)

        def reader() -> None:
            try:
                for _ in range(100):
                    gv.get_value("shared")
            except Exception as exc:
                errors.append(exc)

        def writer() -> None:
            try:
                for i in range(100):
                    gv.set_value("shared", i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=reader) for _ in range(5)] + [
            threading.Thread(target=writer) for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
