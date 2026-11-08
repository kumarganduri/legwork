import os
import json
from typing import List, Dict, Optional, Any

import pandas as pd
import numpy as np

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pandas-tools")


def _check_local_file(path: str) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("file_path must be a non-empty string")
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"File not found: {abs_path}")
    if not os.path.isfile(abs_path):
        raise ValueError(f"Not a file: {abs_path}")
    return abs_path


def _to_builtin(value: Any) -> Any:
    # None for missing values
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    # Pandas / NumPy scalars
    if isinstance(value, (np.generic,)):
        try:
            return value.item()
        except Exception:
            pass

    # Pandas timestamps/timedelta
    if isinstance(value, pd.Timestamp):
        # Use ISO8601 with timezone if available
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, pd.Timedelta):
        return str(value)

    # Containers
    if isinstance(value, dict):
        return {k: _to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(v) for v in value]

    # Builtins or fallback to string for unknowns
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _df_records(df: pd.DataFrame, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    if limit is not None:
        df = df.head(limit)
    # Replace NaNs with None for JSON compatibility
    df2 = df.where(pd.notna(df), None)
    recs = df2.to_dict(orient="records")
    return [_to_builtin(r) for r in recs]


@mcp.tool()
def csv_columns(
    file_path: str,
    sep: str = ",",
    encoding: Optional[str] = None
) -> Dict[str, Any]:
    """
    Return column names and dtypes of a CSV file.
    """
    abs_path = _check_local_file(file_path)
    df = pd.read_csv(abs_path, sep=sep, encoding=encoding, nrows=0)
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.to_dict().items()}
    return {
        "path": abs_path,
        "columns": list(df.columns),
        "dtypes": dtypes,
    }


@mcp.tool()
def csv_head(
    file_path: str,
    nrows: int = 5,
    sep: str = ",",
    encoding: Optional[str] = None
) -> Dict[str, Any]:
    """
    Preview the first nrows of a CSV file.
    """
    if nrows <= 0:
        raise ValueError("nrows must be > 0")
    abs_path = _check_local_file(file_path)
    df = pd.read_csv(abs_path, sep=sep, encoding=encoding, nrows=nrows)
    return {
        "path": abs_path,
        "columns": list(df.columns),
        "rows": _df_records(df),
    }


@mcp.tool()
def csv_describe(
    file_path: str,
    sep: str = ",",
    encoding: Optional[str] = None
) -> Dict[str, Any]:
    """
    Compute pandas describe(include='all') summary for a CSV file.
    """
    abs_path = _check_local_file(file_path)
    df = pd.read_csv(abs_path, sep=sep, encoding=encoding)
    # describe across all columns; may include NaN which we sanitize
    desc = df.describe(include="all").transpose().reset_index().rename(columns={"index": "column"})
    summary = _df_records(desc)
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.to_dict().items()}
    non_null = {col: int(df[col].notna().sum()) for col in df.columns}
    return {
        "path": abs_path,
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "dtypes": dtypes,
        "non_null": non_null,
        "summary": summary,
    }


@mcp.tool()
def csv_query(
    file_path: str,
    query_expr: str,
    sep: str = ",",
    encoding: Optional[str] = None,
    limit: int = 100,
    save_as: Optional[str] = None
) -> Dict[str, Any]:
    """
    Filter rows using pandas.DataFrame.query (engine='python').
    Returns a sample of matching rows and optionally saves all matches to a CSV in ./outputs.
    """
    if not query_expr or not isinstance(query_expr, str):
        raise ValueError("query_expr must be a non-empty string")
    if limit <= 0:
        raise ValueError("limit must be > 0")
    abs_path = _check_local_file(file_path)
    df = pd.read_csv(abs_path, sep=sep, encoding=encoding)
    try:
        filtered = df.query(query_expr, engine="python")
    except Exception as e:
        raise ValueError(f"Query failed: {e}")

    result: Dict[str, Any] = {
        "path": abs_path,
        "matched_count": int(len(filtered)),
        "sample": _df_records(filtered, limit=limit),
    }

    if save_as:
        # Ensure output is within current working directory
        os.makedirs("outputs", exist_ok=True)
        out_name = os.path.basename(save_as) or "filtered.csv"
        out_path = os.path.abspath(os.path.join("outputs", out_name))
        filtered.to_csv(out_path, index=False)
        result["saved_to"] = out_path

    return result


if __name__ == "__main__":
    # Self-test: verify pandas installs and basic tools work offline
    # 1) Check pandas import and version string
    v = getattr(pd, "__version__", "")
    if not v:
        raise RuntimeError("pandas version not found")

    # 2) Create a tiny CSV and exercise tools
    df_test = pd.DataFrame({"a": [1, 2, 3, 4], "b": ["x", "y", "y", "z"]})
    test_path = os.path.abspath("selftest_sample.csv")
    df_test.to_csv(test_path, index=False)

    # Run csv_head
    preview = csv_head(test_path, nrows=2)
    if not preview.get("rows"):
        raise RuntimeError("csv_head returned no rows")

    # Run csv_describe
    desc = csv_describe(test_path)
    if not desc.get("summary"):
        raise RuntimeError("csv_describe returned empty summary")

    # Run csv_query
    q = csv_query(test_path, "a > 2", limit=2, save_as="selftest_filtered.csv")
    if q.get("matched_count", 0) == 0:
        raise RuntimeError("csv_query returned zero matches unexpectedly")

    print("Self-test completed OK with pandas", v)