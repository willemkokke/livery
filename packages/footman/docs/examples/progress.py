# --8<-- [start:part-1]
from pathlib import Path
from livery.footman import task, track, progress


def load_records() -> list: ...  # your own work, whatever shape it takes
def apply(record): ...
def build_index(path): ...


@task
def migrate():
    "Apply pending migrations."
    for record in track(load_records()):  # total from len()
        apply(record)


@task
def index(path: Path):
    "Rebuild the search index."
    for done, total in build_index(path):
        progress(done, total)  # the explicit form
# --8<-- [end:part-1]
