# --8<-- [start:part-1]
from livery.footman import task


@task
def build(target: str, release: bool = False) -> int: ...


build("web", release=True)  # checked: parameters, names, return type
# --8<-- [end:part-1]

# --8<-- [start:part-2]
build.opts(atomic=True)("web")  # still (target: str, release: bool) -> int
# --8<-- [end:part-2]
