from typing import Any, Dict, List, Optional, Union
from mcp.server.fastmcp import FastMCP

import jsonschema

mcp = FastMCP("jsonschema-mcp")


def _normalize_draft_name(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    n = name.strip().lower()
    # Normalize common variants
    n = n.replace("_", "-")
    if n.startswith("draft"):
        n = n.replace("draft", "", 1)
    n = n.strip("-")
    return n


def _get_validator_class(schema: Dict[str, Any], draft: Optional[str]):
    """
    Return an appropriate Validator class from jsonschema for the given draft.
    If draft is None or "auto", use validator_for(schema).
    """
    if draft is None or draft == "" or draft == "auto":
        return jsonschema.validators.validator_for(schema)

    n = _normalize_draft_name(draft)

    mapping = {
        "2020-12": jsonschema.validators.Draft202012Validator,
        "202012": jsonschema.validators.Draft202012Validator,
        "2019-09": jsonschema.validators.Draft201909Validator,
        "201909": jsonschema.validators.Draft201909Validator,
        "7": jsonschema.validators.Draft7Validator,
        "6": jsonschema.validators.Draft6Validator,
        "4": jsonschema.validators.Draft4Validator,
        "3": jsonschema.validators.Draft3Validator,
    }

    cls = mapping.get(n)
    if cls is None:
        raise ValueError(
            f"Unsupported or unknown JSON Schema draft '{draft}'. "
            "Use one of: auto, 2020-12, 2019-09, 7, 6, 4, 3."
        )
    return cls


def _format_error(err: jsonschema.ValidationError) -> Dict[str, Any]:
    return {
        "message": err.message,
        "path": list(err.path),
        "schema_path": list(err.schema_path),
        "validator": err.validator,
        "validator_value": err.validator_value,
    }


@mcp.tool()
def validate_json(
    instance: Any,
    schema: Dict[str, Any],
    draft: Optional[str] = "auto",
    format_checks: bool = False,
    max_errors: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Validate a JSON instance against a JSON Schema.

    Args:
      instance: The JSON data to validate (any JSON-serializable value).
      schema: The JSON Schema to validate against.
      draft: Which JSON Schema draft to use. One of: "auto" (default), "2020-12",
             "2019-09", "7", "6", "4", "3".
      format_checks: If True, enable format checking (requires extras installed).
      max_errors: If set, stop collecting further errors after this many.

    Returns:
      A dict with:
        - valid: bool indicating whether the instance is valid.
        - error_count: number of validation errors found.
        - errors: list of error details (empty if valid).
    """
    ValidatorClass = _get_validator_class(schema, draft if draft else "auto")
    # Validate the schema itself first (raises jsonschema.SchemaError if bad).
    ValidatorClass.check_schema(schema)

    fmt = jsonschema.FormatChecker() if format_checks else None
    validator = ValidatorClass(schema=schema, format_checker=fmt)

    errors: List[Dict[str, Any]] = []
    for idx, e in enumerate(validator.iter_errors(instance)):
        errors.append(_format_error(e))
        if max_errors is not None and (idx + 1) >= max_errors:
            break

    return {
        "valid": len(errors) == 0,
        "error_count": len(errors),
        "errors": errors,
    }


@mcp.tool()
def jsonschema_version() -> str:
    """
    Return the installed jsonschema library version string.
    """
    return getattr(jsonschema, "__version__", "unknown")


if __name__ == "__main__":
    # Self-test: run a tiny offline validation and ensure non-empty results.
    sample_schema = {
        "type": "object",
        "properties": {
            "price": {"type": "number"},
            "name": {"type": "string"},
        },
        "required": ["name", "price"],
        "additionalProperties": False,
    }
    sample_instance = {"name": "Eggs", "price": 34.99}

    result = validate_json(instance=sample_instance, schema=sample_schema)
    if not result:
        raise SystemExit("validate_json returned an empty result")

    ver = jsonschema_version()
    if not ver:
        raise SystemExit("jsonschema_version returned an empty result")

    print("Self-test completed. jsonschema version:", ver)