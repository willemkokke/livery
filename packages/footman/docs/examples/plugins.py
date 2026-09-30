# --8<-- [start:part-1]
from livery.footman.testing import Runner


def test_the_option_reaches_the_task(tmp_path):
    (tmp_path / "tasks.py").write_text(
        'from livery.footman.compose import plugin\nplugin("acme.devkit")\n'
    )
    result = Runner().invoke("--region=us deploy", tasks=tmp_path / "tasks.py")
    assert result.ok
# --8<-- [end:part-1]
