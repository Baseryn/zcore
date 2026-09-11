"""Unified Schema and Response Pruning Hub.

This module provides the core `Zchema` base class, which integrates Pydantic V2
dynamic JSON schema generation, input validation, and response serialization
filtering based on domain-isolated context restriction definitions.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, model_serializer, model_validator

from zcore.context.context import ctx


class Zchema(BaseModel):
    """Unified, domain-aware security schema base class.

    Subclasses specify their unique database domain mapping via the `__model__`
    class attribute. This enables contextual, recursive pruning across schema generation,
    input validation, and response serialization.
    """

    __model__: ClassVar[str | None] = None

    @classmethod
    def _get_relative_restricted_paths(cls) -> set[str]:
        """Extract and normalize restricted field paths mapped to this schema's domain."""
        model_name = getattr(cls, "__model__", None)
        if not model_name:
            return set()

        action = ctx.get("action")
        restricted = ctx.restricted_fields
        relative_paths = set()

        action_prefix = f"{model_name}.{action}." if action else None
        global_prefix = f"{model_name}."

        for path in restricted:
            if action_prefix and path.startswith(action_prefix):
                relative_paths.add(path[len(action_prefix) :])
            elif action and path == f"{model_name}.{action}":
                relative_paths.add("*")
            elif path.startswith(global_prefix):
                remaining = path[len(global_prefix) :]
                parts = remaining.split(".", 1)
                if action and parts[0] == action:
                    continue
                relative_paths.add(remaining)
            elif path == model_name:
                relative_paths.add("*")

        return relative_paths

    @classmethod
    def _prune_data(
        cls,
        data: Any,
        relative_paths: set[str],
    ) -> Any:
        """Recursively strip restricted attributes from dictionary representations."""
        if not isinstance(data, dict):
            return data

        if "*" in relative_paths:
            data.clear()
            return data

        nested_restrictions: dict[str, set[str]] = {}
        for path in relative_paths:
            parts = path.split(".", 1)
            if len(parts) == 1:
                data.pop(parts[0], None)
            else:
                key, remaining = parts
                if key not in nested_restrictions:
                    nested_restrictions[key] = set()
                nested_restrictions[key].add(remaining)

        for key in list(data.keys()):
            val = data[key]
            if key in nested_restrictions:
                rem_paths = nested_restrictions[key]
                if isinstance(val, dict):
                    data[key] = cls._prune_data(val, rem_paths)
                elif isinstance(val, list):
                    data[key] = [
                        cls._prune_data(item, rem_paths)
                        if isinstance(item, dict)
                        else item
                        for item in val
                    ]
            elif isinstance(val, dict):
                data[key] = cls._prune_data(val, set())
            elif isinstance(val, list):
                data[key] = [
                    cls._prune_data(item, set())
                    if isinstance(item, dict)
                    else item
                    for item in val
                ]
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: Any
    ) -> dict[str, Any]:
        """Customize dynamic JSON schema generation under context boundaries."""
        json_schema = handler(core_schema)
        relative_paths = cls._get_relative_restricted_paths()
        if not relative_paths:
            return json_schema

        def prune_schema(schema: dict[str, Any], paths: set[str]) -> None:
            if not isinstance(schema, dict):
                return

            if "*" in paths:
                schema.clear()
                return

            properties = schema.get("properties")
            required = schema.get("required")

            if isinstance(properties, dict):
                nested_restrictions: dict[str, set[str]] = {}
                for path in paths:
                    parts = path.split(".", 1)
                    if len(parts) == 1:
                        properties.pop(parts[0], None)
                        if isinstance(required, list) and parts[0] in required:
                            required.remove(parts[0])
                    else:
                        key, remaining = parts
                        if key not in nested_restrictions:
                            nested_restrictions[key] = set()
                        nested_restrictions[key].add(remaining)

                for key, remaining_paths in nested_restrictions.items():
                    if key in properties:
                        prune_schema(properties[key], remaining_paths)

        prune_schema(json_schema, relative_paths)
        return json_schema

    @model_validator(mode="before")
    @classmethod
    def filter_restricted_inputs(cls, data: Any) -> Any:
        """Silently strip restricted fields from input payloads to prevent Mass Assignment."""
        relative_paths = cls._get_relative_restricted_paths()
        if not relative_paths or data is None:
            return data

        if isinstance(data, dict):
            data_copy = dict(data)
            return cls._prune_data(data_copy, relative_paths)
        return data

    @model_serializer(mode="wrap")
    def secure_serializer(self, handler: Any) -> Any:
        """Securely intercept serialization to prune restricted attributes."""
        serialized = handler(self)
        relative_paths = self._get_relative_restricted_paths()
        if serialized is None:
            return serialized

        if isinstance(serialized, dict) and relative_paths:
            serialized_copy = dict(serialized)
            return self._prune_data(serialized_copy, relative_paths)
        return serialized