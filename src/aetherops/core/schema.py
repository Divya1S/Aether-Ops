"""JSON-Schema-subset validator, pure stdlib (docs/02 §2, docs/17 M8).

Deliberate subset: type (string or union list), required, properties,
additionalProperties (boolean), enum, items. Production uses the
`jsonschema` package; the subset keeps the zero-dependency guarantee while
enforcing the same contract shape. Numeric/string constraints (minimum,
maxLength, …) are intentionally out of scope.
"""
from __future__ import annotations

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "null": type(None),
}


def _type_ok(value, type_name: str) -> bool:
    # bool subclasses int in Python — handle the numeric types explicitly
    # so True never validates as an integer.
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return (isinstance(value, (int, float))
                and not isinstance(value, bool))
    if type_name == "boolean":
        return isinstance(value, bool)
    expected = _TYPES.get(type_name)
    if expected is None:
        raise ValueError(f"unsupported schema type {type_name!r}")
    return isinstance(value, expected)


def validate(instance, schema: dict, path: str = "$") -> list[str]:
    """Returns a list of human-readable violations; empty means valid."""
    errors: list[str] = []

    declared = schema.get("type")
    if declared is not None:
        types = declared if isinstance(declared, list) else [declared]
        if not any(_type_ok(instance, t) for t in types):
            return [f"{path}: expected type {declared}, "
                    f"got {type(instance).__name__}"]

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, subschema in properties.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema,
                                       f"{path}.{key}"))

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate(item, schema["items"],
                                   f"{path}[{index}]"))

    return errors
