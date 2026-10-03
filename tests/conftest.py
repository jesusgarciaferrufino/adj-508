import sys
from pathlib import Path

# Lets tests import capture/, scanners/ and agents/ no matter where pytest is started.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
