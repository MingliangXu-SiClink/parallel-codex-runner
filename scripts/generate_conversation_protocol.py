#!/usr/bin/env python3
"""Generate PCR conversation-domain types for Python, Kotlin, and Swift."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "remote/protocol/pcr-conversation-v1.schema.json"
OUTPUTS = {
    "python": ROOT / "src/parallel_codex_runner_core/conversation_protocol.py",
    "kotlin": ROOT
    / "apps/android/app/src/main/java/com/parallelcodexrunner/remote/data/generated/ConversationProtocol.kt",
    "swift": ROOT
    / "apps/macos/Sources/PCRRemoteMac/GeneratedConversationProtocol.swift",
}
HEADER = "Generated from remote/protocol/pcr-conversation-v1.schema.json. Do not edit."


def snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def enum_case(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if result[:1].isdigit():
        result = "VALUE_" + result
    return result or "UNKNOWN"


def schema_type(spec: dict[str, Any]) -> tuple[str, bool]:
    raw = spec.get("type")
    if isinstance(raw, list):
        values = [str(item) for item in raw]
        return next(item for item in values if item != "null"), "null" in values
    return str(raw or "object"), False


def ref_name(spec: dict[str, Any]) -> str | None:
    if "anyOf" in spec:
        return next((ref_name(item) for item in spec["anyOf"] if "$ref" in item), None)
    value = str(spec.get("$ref") or "")
    return value.rsplit("/", 1)[-1] if value else None


def nullable_ref(spec: dict[str, Any]) -> bool:
    return any(item.get("type") == "null" for item in spec.get("anyOf", []))


def model_definitions(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, value)
        for name, value in schema["$defs"].items()
        if "enum" not in value
    ]


def enum_definitions(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (name, value)
        for name, value in schema["$defs"].items()
        if "enum" in value
    ]


def python_type(spec: dict[str, Any]) -> str:
    reference = ref_name(spec)
    if reference:
        return f"{reference} | None" if nullable_ref(spec) else reference
    value_type, nullable = schema_type(spec)
    if value_type == "string":
        result = "str"
    elif value_type == "integer":
        result = "int"
    elif value_type == "number":
        result = "float"
    elif value_type == "boolean":
        result = "bool"
    elif value_type == "array":
        result = f"list[{python_type(spec.get('items', {}))}]"
    elif value_type == "object" and isinstance(spec.get("additionalProperties"), dict):
        result = f"dict[str, {python_type(spec['additionalProperties'])}]"
    else:
        result = "dict[str, object]"
    return f"{result} | None" if nullable else result


def python_default(spec: dict[str, Any], enums: set[str]) -> str:
    if nullable_ref(spec):
        return "None"
    reference = ref_name(spec)
    if reference in enums:
        return f"{reference}.{enum_case('unknown' if reference != 'CandidateRole' else 'candidate')}"
    if reference:
        return f"field(default_factory={reference})"
    value_type, nullable = schema_type(spec)
    if nullable:
        return "None"
    return {
        "string": '""',
        "integer": "0",
        "number": "0.0",
        "boolean": "False",
        "array": "field(default_factory=list)",
        "object": "field(default_factory=dict)",
    }.get(value_type, "field(default_factory=dict)")


def python_decode(spec: dict[str, Any], expression: str, enums: set[str]) -> str:
    reference = ref_name(spec)
    if reference in enums:
        return f"{reference}.from_value({expression})"
    if reference:
        value = f"{reference}.from_mapping({expression})"
        return f"None if {expression} is None else {value}" if nullable_ref(spec) else value
    value_type, nullable = schema_type(spec)
    if nullable:
        base = dict(spec)
        base["type"] = value_type
        return f"None if {expression} is None else {python_decode(base, expression, enums)}"
    if value_type == "string":
        return f"str({expression} or \"\")"
    if value_type == "integer":
        return f"_as_int({expression})"
    if value_type == "number":
        return f"_as_float({expression})"
    if value_type == "boolean":
        return f"bool({expression})"
    if value_type == "array":
        item = python_decode(spec.get("items", {}), "item", enums)
        return f"[{item} for item in _as_list({expression})]"
    if value_type == "object" and isinstance(spec.get("additionalProperties"), dict):
        item = python_decode(spec["additionalProperties"], "item", enums)
        return f"{{str(key): {item} for key, item in _as_dict({expression}).items()}}"
    return f"_as_dict({expression})"


def generate_python(schema: dict[str, Any]) -> str:
    enums = enum_definitions(schema)
    enum_names = {name for name, _ in enums}
    models = model_definitions(schema)
    lines = [
        "from __future__ import annotations",
        "",
        f'\"\"\"{HEADER}\"\"\"',
        "",
        "from dataclasses import dataclass, field",
        "from enum import Enum",
        "from typing import Any, Mapping",
        "",
        "CONVERSATION_SCHEMA_VERSION = 1",
        "",
        "",
        "def _as_dict(value: object) -> dict[str, Any]:",
        "    return dict(value) if isinstance(value, Mapping) else {}",
        "",
        "",
        "def _as_list(value: object) -> list[Any]:",
        "    return list(value) if isinstance(value, (list, tuple)) else []",
        "",
        "",
        "def _as_int(value: object) -> int:",
        "    if isinstance(value, bool):",
        "        return 0",
        "    try:",
        "        return int(value)  # type: ignore[arg-type]",
        "    except (TypeError, ValueError, OverflowError):",
        "        return 0",
        "",
        "",
        "def _as_float(value: object) -> float:",
        "    if isinstance(value, bool):",
        "        return 0.0",
        "    try:",
        "        return float(value)  # type: ignore[arg-type]",
        "    except (TypeError, ValueError, OverflowError):",
        "        return 0.0",
        "",
        "",
        "def _encode(value: object) -> object:",
        "    if isinstance(value, Enum):",
        "        return value.value",
        "    if hasattr(value, \"to_mapping\"):",
        "        return value.to_mapping()  # type: ignore[union-attr]",
        "    if isinstance(value, list):",
        "        return [_encode(item) for item in value]",
        "    if isinstance(value, dict):",
        "        return {str(key): _encode(item) for key, item in value.items()}",
        "    return value",
    ]
    for name, definition in enums:
        lines.extend(["", "", f"class {name}(str, Enum):"])
        for value in definition["enum"]:
            lines.append(f'    {enum_case(value)} = "{value}"')
        fallback = "UNKNOWN" if "unknown" in definition["enum"] else enum_case(definition["enum"][0])
        lines.extend(
            [
                "",
                "    @classmethod",
                f'    def from_value(cls, value: object) -> "{name}":',
                "        try:",
                "            return cls(str(value or \"\"))",
                "        except ValueError:",
                f"            return cls.{fallback}",
            ]
        )
    for name, definition in models:
        properties = definition.get("properties", {})
        lines.extend(["", "", "@dataclass", f"class {name}:"])
        for field_name, spec in properties.items():
            lines.append(
                f"    {field_name}: {python_type(spec)} = {python_default(spec, enum_names)}"
            )
        if not properties:
            lines.append("    pass")
        lines.extend(
            [
                "",
                "    @classmethod",
                f'    def from_mapping(cls, value: object) -> "{name}":',
                "        payload = _as_dict(value)",
                "        return cls(",
            ]
        )
        for field_name, spec in properties.items():
            expression = f'payload.get("{field_name}")'
            lines.append(
                f"            {field_name}={python_decode(spec, expression, enum_names)},"
            )
        lines.extend(
            [
                "        )",
                "",
                "    def to_mapping(self) -> dict[str, Any]:",
                "        return {",
            ]
        )
        for field_name in properties:
            lines.append(f'            "{field_name}": _encode(self.{field_name}),')
        lines.extend(["        }"])

    root_properties = schema["properties"]
    root_definition = {"properties": root_properties}
    lines.extend(["", "", "@dataclass", "class ConversationSnapshot:"])
    for field_name, spec in root_properties.items():
        lines.append(
            f"    {field_name}: {python_type(spec)} = {python_default(spec, enum_names)}"
        )
    lines.extend(
        [
            "",
            "    @classmethod",
            '    def from_mapping(cls, value: object) -> "ConversationSnapshot":',
            "        payload = _as_dict(value)",
            "        return cls(",
        ]
    )
    for field_name, spec in root_definition["properties"].items():
        expression = f'payload.get("{field_name}")'
        lines.append(
            f"            {field_name}={python_decode(spec, expression, enum_names)},"
        )
    lines.extend(["        )", "", "    def to_mapping(self) -> dict[str, Any]:", "        return {"])
    for field_name in root_properties:
        lines.append(f'            "{field_name}": _encode(self.{field_name}),')
    lines.extend(["        }", ""])
    return "\n".join(lines)


def kotlin_type(spec: dict[str, Any]) -> str:
    reference = ref_name(spec)
    if reference:
        return f"{reference}?" if nullable_ref(spec) else reference
    value_type, nullable = schema_type(spec)
    if value_type == "string":
        result = "String"
    elif value_type == "integer":
        result = "Long" if spec.get("format") == "int64" else "Int"
    elif value_type == "number":
        result = "Double"
    elif value_type == "boolean":
        result = "Boolean"
    elif value_type == "array":
        result = f"List<{kotlin_type(spec.get('items', {}))}>"
    elif value_type == "object" and isinstance(spec.get("additionalProperties"), dict):
        result = f"Map<String, {kotlin_type(spec['additionalProperties'])}>"
    else:
        result = "JsonObject"
    return f"{result}?" if nullable else result


def kotlin_default(spec: dict[str, Any], enums: dict[str, list[str]]) -> str:
    if nullable_ref(spec):
        return "null"
    reference = ref_name(spec)
    if reference in enums:
        values = enums[reference]
        fallback = "unknown" if "unknown" in values else values[0]
        return f"{reference}.{enum_case(fallback)}"
    if reference:
        return f"{reference}()"
    value_type, nullable = schema_type(spec)
    if nullable:
        return "null"
    return {
        "string": '""',
        "integer": "0L" if spec.get("format") == "int64" else "0",
        "number": "0.0",
        "boolean": "false",
        "array": "emptyList()",
        "object": "emptyMap()" if isinstance(spec.get("additionalProperties"), dict) else "JsonObject(emptyMap())",
    }.get(value_type, "JsonObject(emptyMap())")


def generate_kotlin(schema: dict[str, Any]) -> str:
    enum_defs = enum_definitions(schema)
    enums = {name: list(definition["enum"]) for name, definition in enum_defs}
    lines = [
        "package com.parallelcodexrunner.remote.data.generated",
        "",
        f"// {HEADER}",
        "",
        "import kotlinx.serialization.SerialName",
        "import kotlinx.serialization.Serializable",
        "import kotlinx.serialization.json.JsonObject",
        "",
        "const val CONVERSATION_SCHEMA_VERSION = 1",
    ]
    for name, definition in enum_defs:
        lines.extend(["", "@Serializable", f"enum class {name} {{"])
        values = list(definition["enum"])
        for index, value in enumerate(values):
            suffix = "," if index < len(values) - 1 else ";"
            lines.extend(
                [f'    @SerialName("{value}")', f"    {enum_case(value)}{suffix}"]
            )
        lines.append("}")

    all_models = model_definitions(schema) + [
        ("ConversationSnapshot", {"properties": schema["properties"]})
    ]
    for name, definition in all_models:
        properties = definition.get("properties", {})
        lines.extend(["", "@Serializable", f"data class {name}("])
        for field_name, spec in properties.items():
            property_name = snake_to_camel(field_name)
            if property_name != field_name:
                lines.append(f'    @SerialName("{field_name}")')
            lines.append(
                f"    val {property_name}: {kotlin_type(spec)} = {kotlin_default(spec, enums)},"
            )
        lines.append(")")
    lines.append("")
    return "\n".join(lines)


def swift_type(spec: dict[str, Any]) -> str:
    reference = ref_name(spec)
    if reference:
        return f"{reference}?" if nullable_ref(spec) else reference
    value_type, nullable = schema_type(spec)
    if value_type == "string":
        result = "String"
    elif value_type == "integer":
        result = "Int64" if spec.get("format") == "int64" else "Int"
    elif value_type == "number":
        result = "Double"
    elif value_type == "boolean":
        result = "Bool"
    elif value_type == "array":
        result = f"[{swift_type(spec.get('items', {}))}]"
    elif value_type == "object" and isinstance(spec.get("additionalProperties"), dict):
        result = f"[String: {swift_type(spec['additionalProperties'])}]"
    else:
        result = "[String: String]"
    return f"{result}?" if nullable else result


def swift_default(spec: dict[str, Any], enums: dict[str, list[str]]) -> str:
    if nullable_ref(spec):
        return "nil"
    reference = ref_name(spec)
    if reference in enums:
        values = enums[reference]
        fallback = "unknown" if "unknown" in values else values[0]
        return f".{snake_to_camel(fallback)}"
    if reference:
        return f"{reference}()"
    value_type, nullable = schema_type(spec)
    if nullable:
        return "nil"
    return {
        "string": '""',
        "integer": "0",
        "number": "0",
        "boolean": "false",
        "array": "[]",
        "object": "[:]",
    }.get(value_type, "[:]")


def generate_swift(schema: dict[str, Any]) -> str:
    enum_defs = enum_definitions(schema)
    enums = {name: list(definition["enum"]) for name, definition in enum_defs}
    lines = [
        "import Foundation",
        "",
        f"// {HEADER}",
        "",
        "let generatedConversationSchemaVersion = 1",
    ]
    for name, definition in enum_defs:
        lines.extend(["", f"enum {name}: String, Codable, CaseIterable, Sendable {{"])
        for value in definition["enum"]:
            case_name = snake_to_camel(value)
            if case_name == value:
                lines.append(f"    case {case_name}")
            else:
                lines.append(f'    case {case_name} = "{value}"')
        lines.append("}")

    all_models = model_definitions(schema) + [
        ("ConversationSnapshot", {"properties": schema["properties"]})
    ]
    for name, definition in all_models:
        properties = definition.get("properties", {})
        parsed_timestamps = definition.get("x-swift-parsed-timestamps", [])
        equality = "Hashable" if definition.get("x-swift-hashable") else "Equatable"
        lines.extend(["", f"struct {name}: Codable, {equality}, Sendable {{"])
        for field_name, spec in properties.items():
            lines.append(f"    let {snake_to_camel(field_name)}: {swift_type(spec)}")
        for field_name in parsed_timestamps:
            lines.append(f"    let {snake_to_camel(field_name)}Date: Date?")
        lines.extend(["", "    init("])
        property_items = list(properties.items())
        for index, (field_name, spec) in enumerate(property_items):
            comma = "," if index < len(property_items) - 1 else ""
            lines.append(
                f"        {snake_to_camel(field_name)}: {swift_type(spec)} = {swift_default(spec, enums)}{comma}"
            )
        lines.append("    ) {")
        for field_name in properties:
            property_name = snake_to_camel(field_name)
            lines.append(f"        self.{property_name} = {property_name}")
        for field_name in parsed_timestamps:
            property_name = snake_to_camel(field_name)
            lines.append(f"        self.{property_name}Date = {property_name}.flatMap(parseActivityDate)")
        lines.append("    }")
        if parsed_timestamps:
            # Derived dates stay local; initialize them at the decode boundary,
            # never inside SwiftUI equality or while publishing reader state.
            lines.extend(["", "    private enum CodingKeys: String, CodingKey {"])
            for field_name in properties:
                lines.append(f"        case {snake_to_camel(field_name)}")
            lines.extend(["    }", "", "    init(from decoder: Decoder) throws {",
                          "        let values = try decoder.container(keyedBy: CodingKeys.self)",
                          "        self.init("])
            for index, (field_name, spec) in enumerate(property_items):
                property_name = snake_to_camel(field_name)
                value_type = swift_type(spec)
                optional = value_type.endswith("?")
                method = "decodeIfPresent" if optional else "decode"
                comma = "," if index < len(property_items) - 1 else ""
                lines.append(f"            {property_name}: try values.{method}({value_type.rstrip('?')}.self, forKey: .{property_name}){comma}")
            lines.extend(["        )", "    }"])
        # PcrdClient applies JSONDecoder.convertFromSnakeCase to the complete
        # response graph. Explicit snake_case CodingKeys are transformed a
        # second time by that strategy and therefore fail to match. Keep the
        # generated properties in camelCase and let the shared decoder perform
        # the one protocol-level conversion.
        lines.append("}")
    lines.append("")
    return "\n".join(lines)


def generated_outputs() -> dict[str, str]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return {
        "python": generate_python(schema),
        "kotlin": generate_kotlin(schema),
        "swift": generate_swift(schema),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when checked-in generated files differ from the schema",
    )
    args = parser.parse_args()
    stale: list[Path] = []
    for language, content in generated_outputs().items():
        path = OUTPUTS[language]
        if args.check:
            try:
                current = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                current = ""
            if current != content:
                stale.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    if stale:
        for path in stale:
            print(f"stale generated protocol file: {path.relative_to(ROOT)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
