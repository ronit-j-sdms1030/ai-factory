"""Put the platform modules on the path for the test run.

`new/` is deliberately flat while it is small. A package layout can be imposed
when there is enough here to warrant one; imposing it now would be guessing at
a shape the architecture has not yet forced.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
