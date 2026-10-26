import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))

import korean_reply

if __name__ == "__main__":
    sys.exit(korean_reply.main(runtime="codex"))
