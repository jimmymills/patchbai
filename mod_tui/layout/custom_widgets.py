from textual.widget import Widget

from mod_tui.layout.registry import WidgetRegistry


class CustomWidgetError(Exception):
    """Raised when a custom-widget source can't be exec'd or doesn't yield
    a usable Widget subclass."""


def register_custom_widget(
    registry: WidgetRegistry,
    name: str,
    source: str,
    *,
    description: str = "",
    props_schema: dict | None = None,
) -> None:
    """Exec `source` in an isolated namespace and register the resulting
    Widget subclass under `name`.

    Class detection precedence:
      1. `WIDGET_CLASS = SomeClass` sentinel in the namespace.
      2. A class named exactly `name`.
      3. A single Widget subclass defined in the source.
      Otherwise CustomWidgetError.

    The namespace is empty — the source is expected to import what it
    needs from `textual.*` and stdlib.
    """
    namespace: dict = {}
    try:
        exec(source, namespace)  # noqa: S102 - intentional, in-process trust model
    except Exception as e:
        raise CustomWidgetError(f"failed to exec source for {name!r}: {e}") from e

    cls = _find_widget_class(namespace, name)
    if cls is None:
        raise CustomWidgetError(
            f"no Widget subclass found in source for {name!r}"
        )

    # Drop any prior registration so the new class is the live one.
    registry.unregister(name)
    registry.register(
        name, cls,
        description=description,
        props_schema=props_schema or {},
    )


def _find_widget_class(namespace: dict, name: str) -> type[Widget] | None:
    sentinel = namespace.get("WIDGET_CLASS")
    if isinstance(sentinel, type) and issubclass(sentinel, Widget):
        return sentinel

    by_name = namespace.get(name)
    if isinstance(by_name, type) and issubclass(by_name, Widget):
        return by_name

    # Find Widget subclasses DEFINED in this exec (not imported).
    # exec'd classes get __module__ == "builtins" by default since the
    # exec namespace has no __name__.
    candidates = [
        v for v in namespace.values()
        if isinstance(v, type)
        and issubclass(v, Widget)
        and v is not Widget
        and v.__module__ == "builtins"
    ]
    # Deduplicate by id (same class can appear under multiple names).
    unique = list({id(c): c for c in candidates}.values())

    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise CustomWidgetError(
            f"ambiguous: source defined {len(unique)} Widget subclasses; "
            f"set WIDGET_CLASS = ... or name one class {name!r} to disambiguate"
        )
    return None
