from typing import Dict
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("pint-unit-tools")

try:
    import pint
except Exception as e:
    raise RuntimeError(f"Failed to import pint: {e}")

# Create a single UnitRegistry instance for all tools
ureg = pint.UnitRegistry()


@mcp.tool()
def convert(value: float, from_unit: str, to_unit: str) -> Dict[str, object]:
    """
    Convert a numeric value from one unit to another using Pint.

    Args:
        value: The numeric magnitude to convert.
        from_unit: The unit the value is currently expressed in (e.g., "meter", "cm", "kg").
        to_unit: The unit to convert to (e.g., "centimeter", "m", "lb").

    Returns:
        A dictionary with:
          - value: the converted numeric magnitude
          - unit: the target unit as a string
          - text: a human-readable string representation like "123.0 centimeter"
    """
    try:
        quantity = value * ureg(from_unit)
    except Exception as e:
        raise ValueError(f"Invalid source unit '{from_unit}': {e}")

    try:
        converted = quantity.to(to_unit)
    except Exception as e:
        raise ValueError(f"Cannot convert from '{from_unit}' to '{to_unit}': {e}")

    return {
        "value": float(converted.magnitude),
        "unit": f"{converted.units}",
        "text": str(converted),
    }


@mcp.tool()
def conversion_factor(from_unit: str, to_unit: str) -> float:
    """
    Get the multiplicative conversion factor between two units.

    Args:
        from_unit: Source unit (e.g., "meter").
        to_unit: Target unit (e.g., "centimeter").

    Returns:
        The numeric factor f such that (x * from_unit).to(to_unit) == x * f * to_unit.
    """
    try:
        q = 1 * ureg(from_unit)
    except Exception as e:
        raise ValueError(f"Invalid source unit '{from_unit}': {e}")

    try:
        converted = q.to(to_unit)
    except Exception as e:
        raise ValueError(f"Cannot convert from '{from_unit}' to '{to_unit}': {e}")

    # Ensure a plain float is returned for JSON-serializability
    return float(converted.magnitude)


if __name__ == "__main__":
    # Offline self-test: perform a simple, documented quantity conversion.
    # Using the README's spirit: converting 1 meter to centimeters.
    result = convert(1.0, "meter", "centimeter")
    if not result or "value" not in result or "text" not in result:
        raise SystemExit("Self-test failed: missing expected fields in result.")
    if not str(result["text"]):
        raise SystemExit("Self-test failed: empty text representation.")

    # Also verify conversion_factor returns a positive number for a common conversion
    factor = conversion_factor("meter", "centimeter")
    if not (factor > 0):
        raise SystemExit("Self-test failed: conversion factor not positive.")

    print("Self-test passed.")