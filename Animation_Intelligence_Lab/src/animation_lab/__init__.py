"""Animation Intelligence Lab.

Standalone character motion planning, simulation and validation toolkit
for short animated scenes.  Normalized 2D scene space, deterministic
trajectories, kinematic rigs, prop interactions and machine-readable
motion plans.
"""

__version__ = "1.0.0"

from .models import SCHEMA_VERSION

__all__ = ["__version__", "SCHEMA_VERSION"]
