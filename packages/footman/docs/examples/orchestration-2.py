# --8<-- [start:part-1]
from livery.footman.api import group, run, task
from livery.footman.params import Forward
from livery.toolroom.tools.api import ruff, markdownlint, cspell

lint = group("lint")


@lint.task
def python(fix: bool = False):
    ruff("check", "src", fix=fix)


@lint.task
def markdown(fix: bool = False):
    markdownlint("**/*.md", fix=fix)


@lint.task
def spelling():
    cspell("lint", "**/*")  # no --fix


@lint.default
def lint_all(fix: Forward[bool] = False):
    "Lint everything; --fix reaches the members that support it."
# --8<-- [end:part-1]

# --8<-- [start:part-2]
@task
def check(fix: bool = False):
    lint(fix=fix)  # runs lint's default — fans out, or runs its body
    if fix:
        run("./stamp-version.sh")
# --8<-- [end:part-2]
