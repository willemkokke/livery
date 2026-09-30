# --8<-- [start:part-1]
# acme/cli.py
from livery.footman import App

app = App(
    name="Acme",  # long / display name  → the --version banner
    prog="acme",  # short / command name → "acme: ..." errors and hints
    version="1.4.0",  # YOUR version, not footman's
)


def main() -> None:
    raise SystemExit(app.run())
# --8<-- [end:part-1]

# --8<-- [start:part-2]
app = App(name="Acme", prog="acme", version="1.4.0", dist="acme-cli")
# --8<-- [end:part-2]

# --8<-- [start:part-3]
from pathlib import Path

app = App(
    name="Acme",
    prog="acme",
    cache_dir=Path.home() / ".acme" / "cache",
    data_dir=Path.home() / ".acme" / "data",
)
# --8<-- [end:part-3]

# --8<-- [start:part-4]
import os

acme_home = Path(os.environ.get("ACME_HOME", Path.home() / ".acme"))
app = App(
    name="Acme",
    prog="acme",
    cache_dir=acme_home / ".cache" / "acme-cli",
    data_dir=acme_home / "acme-cli",
)
# --8<-- [end:part-4]

# --8<-- [start:part-5]
from livery import footman
from livery.footman import task


@task
def login(token: str):
    (footman.data_dir() / "credentials.json").write_text(token)


@task
def index():
    (footman.cache_dir() / "index.json").write_text("{}")
# --8<-- [end:part-5]

# --8<-- [start:part-6]
app = App(name="Acme", prog="acme", env_prefix="ACME_RUNNER")
# --8<-- [end:part-6]

# --8<-- [start:part-7]
app = App(name="Acme", prog="acme")  # config_name defaults to prog: `acme`
# --8<-- [end:part-7]

# --8<-- [start:part-8]
app = App(name="Acme", prog="acme", dist="acme-cli", builtin=["acme.global"])
# --8<-- [end:part-8]

# --8<-- [start:part-9]
@task(expose="always")
def whoami():
    """Who am I logged in as? Answerable from anywhere."""
# --8<-- [end:part-9]
