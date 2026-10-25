from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    checkout_configuration = Path(__file__).resolve().parent.parent / "alembic.ini"
    if checkout_configuration.is_file():
        command.upgrade(Config(str(checkout_configuration)), "head")
        return
    migration_root = files("pcr_relay").joinpath("_alembic")
    with as_file(migration_root) as root:
        configuration = Config(str(root / "alembic.ini"))
        command.upgrade(configuration, "head")


if __name__ == "__main__":
    main()
