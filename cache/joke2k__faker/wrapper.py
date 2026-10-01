from __future__ import annotations

import difflib
import importlib
import json
import os
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union
from uuid import UUID
from enum import Enum

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("faker_mcp")


def _to_jsonable(obj: Any) -> Any:
    # Recursively convert to JSON-serializable types
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, (Decimal, UUID, Enum)):
        return str(obj)
    if isinstance(obj, bytes):
        # Represent bytes as hex for readability
        return obj.hex()
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(_to_jsonable(k)): _to_jsonable(v) for k, v in obj.items()}
    # Fallback to string representation
    try:
        json.dumps(obj)
        return obj
    except Exception:
        return str(obj)


def _build_faker(
    locales: Optional[Union[str, List[str]]] = None,
    use_weighting: bool = True,
    seed: Optional[int] = None,
    providers: Optional[List[str]] = None,
):
    from faker import Faker

    # Normalize locales input
    loc: Optional[Union[str, List[str]]]
    if locales is None or locales == "" or locales == []:
        loc = None
    elif isinstance(locales, str):
        loc = locales
    elif isinstance(locales, list):
        # filter out empty strings
        loc = [l for l in locales if l]
        if not loc:
            loc = None
    else:
        raise ValueError("locales must be a string, list of strings, or None")

    fake = Faker(loc, use_weighting=use_weighting) if loc is not None else Faker(use_weighting=use_weighting)

    if seed is not None:
        fake.seed_instance(int(seed))

    # Attach any custom providers (module import paths)
    if providers:
        for modpath in providers:
            if not modpath:
                continue
            try:
                mod = importlib.import_module(modpath)
            except Exception as e:
                raise ValueError(f"Failed to import provider module '{modpath}': {e}") from e
            try:
                fake.add_provider(mod)
            except Exception as e:
                raise ValueError(f"Failed to add provider from module '{modpath}': {e}") from e

    return fake


@mcp.tool()
def list_fakes(
    locales: Optional[Union[str, List[str]]] = None,
    use_weighting: bool = True,
    providers: Optional[List[str]] = None,
) -> List[str]:
    """
    List available fake provider method names for the given locales and optional custom providers.
    """
    fake = _build_faker(locales=locales, use_weighting=use_weighting, providers=providers)
    names: List[str] = []
    for name in dir(fake):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(fake, name)
        except Exception:
            continue
        if callable(attr):
            names.append(name)
    return sorted(set(names))


@mcp.tool()
def generate(
    fake_name: str,
    locales: Optional[Union[str, List[str]]] = None,
    repeat: int = 1,
    args: Optional[List[Any]] = None,
    kwargs: Optional[Dict[str, Any]] = None,
    use_weighting: bool = True,
    seed: Optional[int] = None,
    unique: bool = False,
    unique_locale: Optional[str] = None,
    providers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate fake data using Faker by provider method name.

    - fake_name: provider method to call (e.g., "name", "address", "profile").
    - locales: optional locale string or list of locales (e.g., "en_US" or ["it_IT", "en_US"]).
    - repeat: number of values to generate.
    - args: positional arguments for the provider method.
    - kwargs: keyword arguments for the provider method.
    - use_weighting: whether to use weighted real-world frequencies (default True).
    - seed: optional seed to make results deterministic per instance.
    - unique: ensure uniqueness for generated values in this call.
    - unique_locale: if using multiple locales and unique=True, specify which locale to use (e.g., "en_US").
    - providers: list of import paths to custom provider modules to add.
    """
    if not fake_name or not isinstance(fake_name, str):
        raise ValueError("fake_name must be a non-empty string")
    if repeat < 1:
        raise ValueError("repeat must be >= 1")

    fake = _build_faker(locales=locales, use_weighting=use_weighting, seed=seed, providers=providers)

    target = fake.unique if unique else fake
    if unique and unique_locale:
        try:
            target = target[unique_locale]
        except Exception as e:
            raise ValueError(f"Failed to select unique locale '{unique_locale}': {e}") from e

    available = list_fakes(locales=locales, use_weighting=use_weighting, providers=providers)
    if fake_name not in available:
        # Try to get attribute anyway (may be a dynamically added provider)
        try:
            getattr(target, fake_name)
        except Exception:
            suggestions = difflib.get_close_matches(fake_name, available, n=5, cutoff=0.6)
            hint = f" Unknown fake '{fake_name}'."
            if suggestions:
                hint += f" Did you mean: {', '.join(suggestions)}?"
            raise ValueError("Provider method not found." + hint)

    try:
        func = getattr(target, fake_name)
    except Exception as e:
        raise ValueError(f"Could not access provider method '{fake_name}': {e}") from e

    pos_args = args or []
    kw_args = kwargs or {}

    try:
        if repeat == 1:
            value = func(*pos_args, **kw_args)
            result = _to_jsonable(value)
        else:
            values = [func(*pos_args, **kw_args) for _ in range(repeat)]
            result = _to_jsonable(values)
    except Exception as e:
        raise ValueError(f"Error while generating '{fake_name}': {e}") from e

    return {
        "fake": fake_name,
        "locales": locales,
        "repeat": repeat,
        "result": result,
        "unique": unique,
    }


if __name__ == "__main__":
    # Self-test: simple offline checks
    out1 = generate(fake_name="name")
    if not out1 or not out1.get("result"):
        raise SystemExit("Self-test failed: empty result for name()")

    out2 = generate(fake_name="address", locales="en_US", repeat=2, seed=1234)
    if not isinstance(out2.get("result"), list) or len(out2["result"]) != 2:
        raise SystemExit("Self-test failed: repeat=2 did not return two results")

    names_list = list_fakes(locales="en_US")
    if not names_list or "name" not in names_list:
        raise SystemExit("Self-test failed: 'name' not found in available fakes")

    print("Self-test passed.")