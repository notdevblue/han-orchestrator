import os
import sys

shared = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "skills", "adapter-init", "scripts")
sys.path.insert(0, os.path.normpath(shared))

import check_guard


if __name__ == "__main__":
    sys.exit(check_guard.main(runtime="codex"))
