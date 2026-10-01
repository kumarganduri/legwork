from __future__ import annotations

import base64
import datetime
import decimal
from typing import Any, Dict, List, Optional

import duckdb
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("duckdb-mcp")


def _to_jsonable(value: Any) -> Any:
    # Convert values to JSON-serializable types
    if isinstance(value, decimal.Decimal):
        # Prefer float when safe; fallback to string if NaN/Inf or too large
        try:
            f = float(value)
            if not (f == f) or f in (float("inf"), float("-inf")):  # NaN/Inf checks
                return str(value)
            return f
        except Exception:
            return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {
            "type": "bytes",
            "base64": base64.b64encode(bytes(value)).decode("ascii"),
        }
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    return value


@mcp.tool()
def duckdb_query(
    sql: str,
    database_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute a SQL query using DuckDB.
    - sql: The SQL query to run. Can reference local files directly (e.g., SELECT * FROM 'data.csv';).
    - database_path: Optional path to a DuckDB database file. If not provided, uses an in-memory database.

    Returns:
        {
          "columns": [col_name, ...],
          "rows": [ {col_name: value, ...}, ... ]
        }
    """
    # Connect to DuckDB (in-memory by default)
    db = database_path if database_path else ":memory:"
    con = duckdb.connect(database=db, read_only=False)
    try:
        cur = con.execute(sql)
        # For statements without result sets, description may be None
        desc = getattr(cur, "description", None)
        col_names: List[str] = [d[0] for d in desc] if desc else []

        data = cur.fetchall() if desc else []
        # Convert to list of dicts for readability
        rows_out: List[Dict[str, Any]] = []
        for row in data:
            # DuckDB may return tuples
            if col_names:
                row_dict = {col_names[i]: _to_jsonable(val) for i, val in enumerate(row)}
            else:
                # Fallback to positional keys if no columns
                row_dict = {str(i): _to_jsonable(val) for i, val in enumerate(row)}
            rows_out.append(row_dict)

        return {"columns": col_names, "rows": rows_out}
    finally:
        con.close()


if __name__ == "__main__":
    # Offline self-test: run a simple in-memory query and ensure we get a non-empty result
    result = duckdb_query("SELECT 42 AS answer")
    if not result or not isinstance(result, dict):
        raise RuntimeError("Self-test failed: no result returned")
    rows = result.get("rows", [])
    if not rows:
        raise RuntimeError("Self-test failed: no rows returned")
    print("Self-test OK: got", len(rows), "row(s)")