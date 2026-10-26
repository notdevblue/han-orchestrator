import os
import sys

shared = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "skills", "distill", "scripts")
sys.path.insert(0, os.path.normpath(shared))

import distill


if __name__ == "__main__":
    sys.exit(distill.main(runtime="codex"))
