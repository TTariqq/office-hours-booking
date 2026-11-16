"""Business rules for office hours. Standard library only, so any layer can import it."""

# A booking must start at least this many minutes after the student books it.
MIN_LEAD_MIN = 60

# A block may not be scheduled further ahead than this.
MAX_AHEAD_DAYS = 14

# One instructor may have at most this many active bookings per slot.
MAX_ACTIVE_PER_INSTRUCTOR = 2

# A student cannot cancel a booking closer than this to its start.
CANCEL_CUTOFF_MIN = 120

# Allowed slot lengths, in minutes.
ALLOWED_SLOT_LENGTHS = {10, 15, 20, 30}

# Allowed block length, in minutes.
BLOCK_MIN_MIN = 30
BLOCK_MAX_MIN = 240

# Longest allowed booking topic.
TOPIC_MAX_CHARS = 200
