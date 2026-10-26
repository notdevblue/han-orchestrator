import os
import sys

shared = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "skills", "slots", "scripts")
sys.path.insert(0, os.path.normpath(shared))

import slots


if __name__ == "__main__":
    sys.exit(slots.main(runtime="codex"))
