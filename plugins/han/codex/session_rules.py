import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))

import session_rules

if __name__ == "__main__":
    sys.exit(session_rules.main(runtime="codex"))
