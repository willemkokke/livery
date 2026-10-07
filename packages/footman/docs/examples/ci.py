# --8<-- [start:part-1]
from livery.footman import requires_env, task


@task
@requires_env("CI")
def publish_coverage(): ...
# --8<-- [end:part-1]
