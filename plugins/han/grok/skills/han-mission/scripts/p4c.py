import os
import sys

shared = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "skills", "mission", "scripts")
sys.path.insert(0, os.path.normpath(shared))

import p4c


if __name__ == "__main__":
    sys.exit(p4c.main(runtime="grok"))
