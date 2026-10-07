# --8<-- [start:part-1]
from livery.footman import run, task


@task
def build() -> str:
    ...
    return "dist/app.tar"


@task(pre=[build])
def publish():
    artifact = build()  # the build that already ran, not a second one
    run(f"./upload {artifact}")
# --8<-- [end:part-1]
