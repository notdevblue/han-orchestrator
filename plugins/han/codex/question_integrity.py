import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks"))

import question_integrity


if __name__ == "__main__":
    sys.exit(question_integrity.main(runtime="codex"))
