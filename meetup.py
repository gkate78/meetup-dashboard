import os
import sys

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT_DIR, "src"))

from meetup_dashboard.app import *  # noqa: F401,F403

if __name__ == "__main__":
    main()
