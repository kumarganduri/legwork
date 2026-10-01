from __future__ import annotations

from typing import Any, Dict, List, Optional, Union
from mcp.server.fastmcp import FastMCP

import sqlite_utils

mcp = FastMCP("sqlite-utils-mcp")


@mcp.tool()
def query_db(db_path: str, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Execute a read-only SQL query against a SQLite database using sqlite-utils and return rows as a list of dicts.

    Args:
        db_path: Path to a SQLite database file (it will be created if it does not exist).
        sql: The SQL query to execute.
        params: Optional dictionary of named parameters for the SQL query.

    Returns:
        A list of dictionaries, one per row.
    """
    db = sqlite_utils.Database(db_path)
    rows = db.query(sql, params or {})
    return [dict(r) for r in rows]


@mcp.tool()
def execute_db(db_path: str, sql: str, params: Optional[Dict[str, Any]] = None) -> int:
    """
    Execute a non-SELECT SQL statement (e.g., CREATE TABLE, INSERT, UPDATE, DELETE) against a SQLite database.

    Args:
        db_path: Path to a SQLite database file (it will be created if it does not exist).
        sql: The SQL statement to execute.
        params: Optional dictionary of named parameters for the SQL statement.

    Returns:
        The number of rows modified if available, otherwise 0.
    """
    db = sqlite_utils.Database(db_path)
    # sqlite-utils doesn't expose rowcount for arbitrary SQL; use underlying connection cursor.
    cur = db.conn.execute(sql, params or {})
    db.conn.commit()
    # rowcount can be -1 for statements where it does not apply
    return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0


@mcp.tool()
def insert_rows(
    db_path: str,
    table: str,
    rows: List[Dict[str, Any]],
    pk: Optional[str] = None,
    replace: bool = False,
    upsert: bool = False,
    ignore: bool = False,
) -> Dict[str, Any]:
    """
    Insert multiple rows into a table using sqlite-utils' insert_all, creating the table if needed.

    Args:
        db_path: Path to the SQLite database file (created if it does not exist).
        table: Table name to insert into (created if it does not exist).
        rows: List of dictionaries representing rows to insert.
        pk: Optional primary key column name to set on table creation.
        replace: If True, use OR REPLACE behavior for conflicts.
        upsert: If True, update existing rows when primary key conflicts occur.
        ignore: If True, use OR IGNORE behavior for conflicts.

    Returns:
        A summary dict with table name and number of rows inserted.
    """
    if not isinstance(rows, list) or (rows and not isinstance(rows[0], dict)):
        raise TypeError("rows must be a list of dictionaries")

    db = sqlite_utils.Database(db_path)
    t = db[table]
    t.insert_all(rows, pk=pk, replace=replace, upsert=upsert, ignore=ignore)
    db.conn.commit()
    return {"table": table, "inserted": len(rows)}


@mcp.tool()
def list_tables(db_path: str, counts: bool = False) -> List[Union[str, Dict[str, Any]]]:
    """
    List tables in the database, optionally including row counts.

    Args:
        db_path: Path to the SQLite database file.
        counts: If True, return list of dicts with table and count; otherwise return list of table names.

    Returns:
        A list of table names (strings) or a list of dicts with 'table' and 'count'.
    """
    db = sqlite_utils.Database(db_path)
    names = db.table_names()
    if not counts:
        return names
    result: List[Dict[str, Any]] = []
    for name in names:
        result.append({"table": name, "count": db[name].count})
    return result


if __name__ == "__main__":
    # Self-test: use sqlite-utils as a library with an in-memory database.
    # Create an in-memory DB, insert rows, and query them to ensure non-empty result.
    memdb = sqlite_utils.Database(memory=True)
    memdb["dogs"].insert_all(
        [
            {"id": 1, "age": 4, "name": "Cleo"},
            {"id": 2, "age": 2, "name": "Pancakes"},
        ],
        pk="id",
    )
    res = list(memdb.query("select name from dogs order by id"))
    if not res:
        raise RuntimeError("sqlite-utils self-test: query returned no rows")

    # Also exercise one wrapper function minimally using the same in-memory DB by writing it to a temp file.
    # Use a temporary on-disk file to invoke query_db and list_tables with a real path.
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmpd:
        path = os.path.join(tmpd, "test.db")
        # Copy in-memory DB to file by iterating rows
        filedb = sqlite_utils.Database(path)
        filedb["dogs"].insert_all([dict(r) for r in memdb.query("select * from dogs")], pk="id")
        filedb.conn.commit()

        # Call wrapper tools
        tables_out = list_tables(path, counts=True)
        if not tables_out:
            raise RuntimeError("list_tables returned empty")

        q = query_db(path, "select count(*) as c from dogs")
        if not q:
            raise RuntimeError("query_db returned empty")

    # If we reach here, everything worked.