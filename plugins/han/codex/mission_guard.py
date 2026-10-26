import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))

import mission_guard


if __name__ == "__main__":
    sys.exit(mission_guard.main(runtime="codex"))
