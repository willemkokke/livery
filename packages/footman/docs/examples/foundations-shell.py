# --8<-- [start:part-1]
from livery.footman.api import run

run(r"build.exe --out C:\dist", shell="native")  # cmd, not git-bash
# --8<-- [end:part-1]
