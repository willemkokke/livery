# --8<-- [start:part-1]
from livery.footman.api import run, task


@task
def proto():
    "Generate protobuf stubs."
    run("buf generate")


@task(pre=[proto])
def build():
    "Compile the service."
    run("cargo build --release")


@task(pre=[proto])
def docs():
    "Render the API docs."
    run("./render-docs.sh")


@task
def notify():
    "Announce the finished train."
    run("./notify.sh done")


@task(pre=[build, docs], post=[notify])
def release():
    "The whole train."
# --8<-- [end:part-1]
