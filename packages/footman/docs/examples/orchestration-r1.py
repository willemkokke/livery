# The page shows the part below. The lines above it give the part the
# names the page's earlier examples defined.
from livery.footman import task


@task
def fmt(): ...


@task
def lint(): ...


# --8<-- [start:part-1]
from livery.footman import Forward


@task(pre=[fmt.opts(atomic=True), lint])  # protect fmt's writes here, not everywhere
def check(fix: Forward[bool] = False): ...
# --8<-- [end:part-1]
