"""Deterministic golden observations for the Python→Rust state writer replay.

The replay contract deliberately compares *observations*, not implementation details:
operation input/result plus the explicitly affected SQLite tables.  A future Rust
runner can emit the same JSON without importing Python or sending SQL through the
writer IPC.
"""

from __future__ import annotations

import base64
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


CONTRACT = "haos.rust_writer.golden_replay"
SCHEMA_VERSION = 1


class ReplayContractError(ValueError):
    """The observation is not valid for the frozen replay contract."""


class ReplayDivergenceError(AssertionError):
    """Raised by :meth:`ReplayDiff.raise_if_divergent`."""


@dataclass(frozen=True)
class TableSpec:
    """The stable projection and identity for one affected table.

    ``key_columns`` must identify a row when possible.  A table without a key is
    still supported: its rows are compared as a sorted multiset.
    """

    name: str
    columns: tuple[str, ...]
    key_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name or not self.columns:
            raise ReplayContractError("table name and columns are required")
        if len(set(self.columns)) != len(self.columns):
            raise ReplayContractError(f"duplicate columns in {self.name}")
        if not set(self.key_columns).issubset(self.columns):
            raise ReplayContractError(f"key column is not projected by {self.name}")


@dataclass(frozen=True)
class OperationSpec:
    """A typed replay operation and the tables whose outcome it can affect."""

    name: str
    request: Any
    affected_tables: tuple[TableSpec, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise ReplayContractError("operation name is required")
        names = [table.name for table in self.affected_tables]
        if len(set(names)) != len(names):
            raise ReplayContractError("affected tables must be unique")


@dataclass(frozen=True)
class TableSnapshot:
    name: str
    columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "key_columns": list(self.key_columns),
            "rows": [dict(row) for row in self.rows],
        }


@dataclass(frozen=True)
class ReplayObservation:
    operation: OperationSpec
    result: Any
    tables: tuple[TableSnapshot, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "schema_version": SCHEMA_VERSION,
            "operation": {
                "name": self.operation.name,
                "request": _canonicalize(self.operation.request),
            },
            "result": _canonicalize(self.result),
            "tables": [table.as_dict() for table in self.tables],
        }

    def canonical_json(self) -> str:
        """Return UTF-8-safe, whitespace-free JSON with recursively sorted keys."""
        return canonical_json(self.as_dict())

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ReplayObservation":
        if value.get("contract") != CONTRACT:
            raise ReplayContractError("unsupported replay contract")
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ReplayContractError("unsupported replay schema_version")
        operation_value = value.get("operation")
        if not isinstance(operation_value, Mapping):
            raise ReplayContractError("operation must be an object")
        raw_tables = value.get("tables")
        if not isinstance(raw_tables, list):
            raise ReplayContractError("tables must be an array")

        tables: list[TableSnapshot] = []
        names: set[str] = set()
        for raw in raw_tables:
            if not isinstance(raw, Mapping):
                raise ReplayContractError("table snapshot must be an object")
            name = raw.get("name")
            columns = raw.get("columns")
            key_columns = raw.get("key_columns", [])
            rows = raw.get("rows")
            if (
                not isinstance(name, str)
                or not isinstance(columns, list)
                or not isinstance(key_columns, list)
                or not isinstance(rows, list)
                or not all(isinstance(column, str) for column in columns)
                or not all(isinstance(column, str) for column in key_columns)
                or not all(isinstance(row, Mapping) for row in rows)
            ):
                raise ReplayContractError("malformed table snapshot")
            spec = TableSpec(name, tuple(columns), tuple(key_columns))
            if name in names:
                raise ReplayContractError(f"duplicate table snapshot: {name}")
            names.add(name)
            normalized_rows = tuple(
                {column: _canonicalize(row.get(column)) for column in spec.columns}
                for row in rows
            )
            normalized_rows = tuple(
                sorted(normalized_rows, key=lambda row: _row_sort_key(row, spec.key_columns))
            )
            tables.append(TableSnapshot(spec.name, spec.columns, spec.key_columns, normalized_rows))

        operation = OperationSpec(str(operation_value.get("name", "")), operation_value.get("request"))
        # Rust emits table metadata; affected_tables is reconstructed from it.
        operation = OperationSpec(
            operation.name,
            operation.request,
            tuple(TableSpec(table.name, table.columns, table.key_columns) for table in tables),
        )
        return cls(operation, _canonicalize(value.get("result")), tuple(tables))

    @classmethod
    def from_json(cls, payload: str) -> "ReplayObservation":
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ReplayContractError(f"invalid observation JSON: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ReplayContractError("observation must be an object")
        return cls.from_mapping(value)


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value using the replay's canonical encoding."""
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ReplayContractError("JSON object keys must be strings")
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, bytes):
        return {"$bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float) and not math.isfinite(value):
        raise ReplayContractError("NaN and infinity are not canonical JSON")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ReplayContractError(f"unsupported JSON value: {type(value).__name__}")


def _quote_identifier(identifier: str) -> str:
    if not identifier:
        raise ReplayContractError("empty SQLite identifier")
    return '"' + identifier.replace('"', '""') + '"'


def _row_sort_key(row: Mapping[str, Any], key_columns: Sequence[str]) -> str:
    if key_columns:
        return canonical_json([row[column] for column in key_columns]) + "\0" + canonical_json(row)
    return canonical_json(row)


def snapshot_table(connection: sqlite3.Connection, spec: TableSpec) -> TableSnapshot:
    """Read only the declared projection and sort it independently of SQLite row order."""
    columns_sql = ", ".join(_quote_identifier(column) for column in spec.columns)
    table_sql = _quote_identifier(spec.name)
    cursor = connection.execute(f"SELECT {columns_sql} FROM {table_sql}")
    rows = [
        {column: _canonicalize(value) for column, value in zip(spec.columns, row)}
        for row in cursor.fetchall()
    ]
    rows.sort(key=lambda row: _row_sort_key(row, spec.key_columns))
    return TableSnapshot(spec.name, spec.columns, spec.key_columns, tuple(rows))


def capture_observation(
    connection: sqlite3.Connection,
    operation: OperationSpec,
    result: Any,
) -> ReplayObservation:
    """Capture a Python result and affected-table projections from a fixture DB."""
    return ReplayObservation(
        operation=operation,
        result=_canonicalize(result),
        tables=tuple(
            sorted(
                (snapshot_table(connection, spec) for spec in operation.affected_tables),
                key=lambda table: table.name,
            )
        ),
    )


@dataclass(frozen=True)
class ReplayDiff:
    differences: tuple[dict[str, Any], ...]

    @property
    def equal(self) -> bool:
        return not self.differences

    def as_dict(self) -> dict[str, Any]:
        return {"equal": self.equal, "differences": list(self.differences)}

    def raise_if_divergent(self) -> None:
        if not self.equal:
            raise ReplayDivergenceError(canonical_json(self.as_dict()))


def _difference(path: str, kind: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "expected": _canonicalize(expected),
        "actual": _canonicalize(actual),
    }


def _table_rows_by_key(table: TableSnapshot) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    keyed: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for row in table.rows:
        key = canonical_json([row[column] for column in table.key_columns])
        if key in keyed:
            duplicates.append(row)
        else:
            keyed[key] = row
    return keyed, duplicates


def diff_observations(expected: ReplayObservation, actual: ReplayObservation) -> ReplayDiff:
    """Compare two observations and identify the first-class divergence locations."""
    differences: list[dict[str, Any]] = []

    if expected.operation.name != actual.operation.name:
        differences.append(_difference("/operation/name", "operation_name", expected.operation.name, actual.operation.name))
    if canonical_json(expected.operation.request) != canonical_json(actual.operation.request):
        differences.append(_difference("/operation/request", "request_changed", expected.operation.request, actual.operation.request))
    if canonical_json(expected.result) != canonical_json(actual.result):
        differences.append(_difference("/result", "result_changed", expected.result, actual.result))

    expected_tables = {table.name: table for table in expected.tables}
    actual_tables = {table.name: table for table in actual.tables}
    for name in sorted(set(expected_tables) | set(actual_tables)):
        left = expected_tables.get(name)
        right = actual_tables.get(name)
        path = f"/tables/{name}"
        if left is None:
            differences.append(_difference(path, "unexpected_table", None, right.as_dict()))
            continue
        if right is None:
            differences.append(_difference(path, "missing_table", left.as_dict(), None))
            continue
        if left.columns != right.columns:
            differences.append(_difference(path + "/columns", "columns_changed", list(left.columns), list(right.columns)))
        if left.key_columns != right.key_columns:
            differences.append(_difference(path + "/key_columns", "key_columns_changed", list(left.key_columns), list(right.key_columns)))

        if left.key_columns and right.key_columns == left.key_columns:
            expected_rows, expected_dupes = _table_rows_by_key(left)
            actual_rows, actual_dupes = _table_rows_by_key(right)
            if expected_dupes:
                differences.append(_difference(path + "/rows", "duplicate_expected_key", [], expected_dupes))
            if actual_dupes:
                differences.append(_difference(path + "/rows", "duplicate_actual_key", [], actual_dupes))
            for key in sorted(set(expected_rows) | set(actual_rows)):
                row_path = f"{path}/rows/{key}"
                if key not in actual_rows:
                    differences.append(_difference(row_path, "missing_row", expected_rows[key], None))
                elif key not in expected_rows:
                    differences.append(_difference(row_path, "unexpected_row", None, actual_rows[key]))
                elif canonical_json(expected_rows[key]) != canonical_json(actual_rows[key]):
                    differences.append(_difference(row_path, "row_changed", expected_rows[key], actual_rows[key]))
        elif left.key_columns == right.key_columns:
            expected_counter = Counter(canonical_json(row) for row in left.rows)
            actual_counter = Counter(canonical_json(row) for row in right.rows)
            if expected_counter != actual_counter:
                differences.append(_difference(path + "/rows", "rows_changed", list(left.rows), list(right.rows)))

    # Keep output deterministic even when callers construct observations manually.
    return ReplayDiff(tuple(sorted(differences, key=lambda item: item["path"] + "\0" + item["kind"])))


__all__ = [
    "CONTRACT",
    "SCHEMA_VERSION",
    "OperationSpec",
    "ReplayContractError",
    "ReplayDiff",
    "ReplayDivergenceError",
    "ReplayObservation",
    "TableSnapshot",
    "TableSpec",
    "canonical_json",
    "capture_observation",
    "diff_observations",
    "snapshot_table",
]
