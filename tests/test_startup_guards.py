"""Проверка имён регистрации команд в классах Cog на уровне исходников."""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _is_decorated_command(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
            if decorator.func.attr in {"command", "listener", "context_menu"}:
                return True
    return False


def test_cog_commands_do_not_use_reserved_names():
    errors = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            if not any(isinstance(base, ast.Attribute) and base.attr == "Cog" for base in cls.bases):
                continue
            for entry in cls.body:
                name = getattr(entry, "name", None)
                if name and name.startswith(("bot_", "cog_")) and isinstance(entry, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if _is_decorated_command(entry):
                        errors.append(f"{path.name}:{entry.lineno}:{cls.name}.{name}")
                if isinstance(entry, (ast.Assign, ast.AnnAssign)):
                    targets = entry.targets if isinstance(entry, ast.Assign) else [entry.target]
                    if any(isinstance(t, ast.Name) and t.id.startswith(("bot_", "cog_")) for t in targets):
                        value = entry.value
                        if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "Group":
                            errors.append(f"{path.name}:{entry.lineno}:{cls.name}:reserved Group attribute")
    assert not errors, "Зарезервированные имена команд: " + ", ".join(errors)
