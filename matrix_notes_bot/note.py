import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pytz
from apscheduler.job import Job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.util import timedelta_seconds
from nio import AsyncClient

from matrix_notes_bot.config import CONFIG
from matrix_notes_bot.functions import make_pill, send_text_to_room

logger = logging.getLogger(__name__)


class Note(object):
    """An object containing information about a note, when it should go off,
    whether it is recurring, etc.

    Args:
        client: The matrix client
        store: A Storage object
        room_id: The ID of the room the note should appear in
        note_text: The text to include in the note message
        target_user: Optional. A user ID of a specific user to mention in the room while
            reminding
    """

    def __init__(
        self,
        client: AsyncClient,
        store,
        room_id: str,
        note_text: str,
        category: str,
        target_user: Optional[str] = None,
    ):
        self.client = client
        self.store = store
        self.room_id = room_id
        self.note_text = note_text
        self.category = category
        self.target_user = target_user

    def cancel(self, cancel_alarm: bool = True):
        """Cancels a note and all recurring instances
        """
        logger.debug(
            "Removing note in room %s: %s", self.room_id, self.note_text
        )

        # Remove from the in-memory note and alarm dicts
        NOTES.pop((self.room_id, self.note_text.upper()), None)

        # Delete the note from the database
        self.store.delete_note(self.room_id, self.note_text)


# Global dictionaries
#
# (room_id, note_text) tuples as keys
#
# note_text should be accessed and stored as uppercase in order to
# allow for case-insensitive matching when carrying out user actions
NOTES: Dict[Tuple[str, str], Note] = {}
