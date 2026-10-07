# --8<-- [start:part-1]
from pathlib import Path
from typing import Annotated
from livery.footman import matching, task


@task
def load(env_file: Annotated[Path, matching(".env*")] = Path(".env")): ...
# --8<-- [end:part-1]
