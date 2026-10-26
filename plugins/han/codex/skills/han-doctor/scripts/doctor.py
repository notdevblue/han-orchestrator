import os
import sys

shared = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "skills", "doctor", "scripts")
sys.path.insert(0, os.path.normpath(shared))

import doctor


if __name__ == "__main__":
    sys.exit(doctor.main(runtime="codex"))
