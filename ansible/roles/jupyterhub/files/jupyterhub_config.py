"""配布済みポータルをJupyterHubへ登録する。"""

import sys
from pathlib import Path

# Traitlets does not add the config directory to Python's search path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hpc_portal.entrypoints.jupyterhub import configure_jupyterhub

configure_jupyterhub(get_config())  # noqa: F821
