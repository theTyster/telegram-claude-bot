"""Docker HEALTHCHECK: healthy if the bot touched its health file recently.

In polling mode the file is touched after every successful ``getUpdates``,
so this also notices when Telegram can't be reached.
"""

import os
import sys
import time

HEALTH_FILE = os.environ.get("HEALTH_FILE", "/tmp/bot-health")
MAX_AGE_SECONDS = 180

try:
    with open(HEALTH_FILE) as f:
        age = time.time() - int(f.read().strip() or 0)
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if age < MAX_AGE_SECONDS else 1)
