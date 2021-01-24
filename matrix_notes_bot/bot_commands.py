import logging
from typing import Optional, Tuple

from nio import AsyncClient, MatrixRoom
from nio.events.room_events import RoomMessageText

from matrix_notes_bot.config import CONFIG
from matrix_notes_bot.errors import CommandSyntaxError
from matrix_notes_bot.functions import command_syntax, send_text_to_room, make_pill
from matrix_notes_bot.note import NOTES, Note
from matrix_notes_bot.storage import Storage

logger = logging.getLogger(__name__)


class Command(object):
    def __init__(
        self,
        client: AsyncClient,
        store: Storage,
        command: str,
        room: MatrixRoom,
        event: RoomMessageText,
    ):
        """A command made by a user

        Args:
            client: The client to communicate to matrix with
            store: Bot storage
            command: The command and arguments
            room: The room the command was sent in
            event: The event describing the command
        """
        self.client = client
        self.store = store
        self.room = room
        self.event = event

        msg_without_prefix = command[
            len(CONFIG.command_prefix) :
        ]  # Remove the cmd prefix
        self.args = (
            msg_without_prefix.split()
        )  # Get a list of all items, split by spaces
        self.command = self.args.pop(
            0
        )  # Remove the first item and save as the command (ex. `note`)

    def _parse_note_command_args(self) -> Tuple[str, str]:
        """Processes the list of arguments and returns parsed note information

        Returns:
            A tuple containing the category of the note and the note text.

        Raises:
            CommandError: if a time specified in the user command is invalid or in the past
        """
        args_str = " ".join(self.args)
        logger.debug("Parsing command arguments: %s", args_str)

        try:
            note_text = args_str.split(";", maxsplit=1)
        except ValueError:
            raise CommandSyntaxError()

        if len(note_text) > 1:
            category_str = note_text[0]
            note_text = note_text[1]
        else:
            category_str = "general"
            note_text = note_text[0]
        logger.debug("Got category: %s", category_str)

        # Clean up the input
        category_str = category_str.strip().lower()
        note_text = note_text.strip()

        return category_str, note_text

    async def _confirm_note(self, note: Note):
        """Sends a message to the room confirming the note is set

        Args:
            note: The Note to confirm
        """

        # Build the response string
        text = f"OK, I will remember \"{note.note_text}\"!"

        # Send the message to the room
        await send_text_to_room(self.client, self.room.room_id, text)

    async def _note(self, target: Optional[str] = None):
        """Create a note with a given target

        Args:
            target: A user ID if this note will mention a single user. If None,
                the note will mention the whole room
        """
        (
            category,
            note_text,
        ) = self._parse_note_command_args()

        logger.debug(f"Creating note in room {self.room.room_id} with: {note_text}")

        if (self.room.room_id, note_text.upper()) in NOTES:
            await send_text_to_room(
                self.client,
                self.room.room_id,
                "This note already exists, not adding it again.",
            )
            return

        # Create the note
        note = Note(
            self.client,
            self.store,
            self.room.room_id,
            note_text,
            category,
            target_user=target,
        )

        # Record the note
        NOTES[(self.room.room_id, note_text.upper())] = note
        self.store.store_note(note)

        # Send a message to the room confirming the creation of the note
        await self._confirm_note(note)

    async def process(self):
        """Process the command"""
        if self.command in ["note", "n"]:
            await self._note_for_me()
        elif self.command in ["listnotes", "list", "ln"]:
            await self._list_notes()
        elif self.command in [
            "delnote",
            "deletenote",
            "delete",
            "removenote",
            "cancelnote",
            "cancel",
            "rm",
            "d",
            "c",
        ]:
            await self._delete_note()
        elif self.command in ["help", "h"]:
            await self._help()

    @command_syntax("[every <recurring time>;] <start time>; <note text>")
    async def _note_for_me(self):
        """Set a note that will remind only the user who created it"""
        await self._note(target=self.event.sender)

    # might add something like this, might not
    # @command_syntax("[every <recurring time>;] <start time>; <note text>")
    # async def _remind_room(self):
    #     """Set a note that will mention the room that the note was created in"""
    #     await self._note()

    @command_syntax("[category]")
    async def _list_notes(self):
        """Format and show known notes for the current room

        Sends a message listing them in the following format:

            ##<topic>

            <target user>: <note text>

        or if there are no notes set:

            There are no notes for this room.
        """
        output = ""

        notes_lines = {}
        
        cat = " ".join(self.args)

        # Sort the note types
        for note in NOTES.values():
            if cat and not cat == note.category:
                continue
            
            # Filter out notes that don't belong to this room
            if note.room_id != self.room.room_id:
                continue

            # Organise alarms into markdown lists
            line = "- "

            # In groups, also announce the note taker
            if len(self.room.users) > 2:
                line += f'{make_pill(note.target_user)} said:'

            # Add the note's text
            line += f'*"{note.note_text}"*'

            # Output the status of each note. We divide up the notes by type in order
            # to show them in separate sections, and display them differently
            if note.category not in notes_lines.keys():
                notes_lines[note.category] = []

            notes_lines[note.category].append(line)

        if (
            not notes_lines
        ):
            if not cat:
                m = "*There are no notes for this room.*"
            else:
                m = "*There are no notes for this category in this room.*"

            await send_text_to_room(
                self.client,
                self.room.room_id,
                m,
            )
            return

        for c, lines in notes_lines.items():
            output += "\n\n" + f"**{c}**" + "\n\n"
            output += "\n".join(lines)

        await send_text_to_room(self.client, self.room.room_id, output)

    @command_syntax("<note text>")
    async def _delete_note(self):
        """Delete a note via its note text"""
        note_text = " ".join(self.args)
        if not note_text:
            raise CommandSyntaxError()

        logger.debug("Known notes: %s", NOTES)
        logger.debug(
            "Deleting note in room %s: %s", self.room.room_id, note_text
        )

        note = NOTES.get((self.room.room_id, note_text.upper()))
        if note:
            # Cancel the note and associated alarms
            note.cancel()

            text = "Note deleted."
        else:
            text = f"Unknown note '{note_text}'."

        await send_text_to_room(self.client, self.room.room_id, text)

    @command_syntax("")
    async def _help(self):
        """Show the help text"""
        # Ensure we don't tell the user to use something other than their configured command
        # prefix
        c = CONFIG.command_prefix

        if not self.args:
            text = (
                f"Hello, I am a note bot! Use `{c}help notes` "
                f"to view available commands."
            )
            await send_text_to_room(self.client, self.room.room_id, text)
            return

        topic = self.args[0]

        # Simply way to check for plurals
        if topic.startswith("note"):
            text = f"""
**Notes**

Take a note on an optional topic:

```
{c}note|n [topic;] <note text>
```

List all notes for a room:

```
{c}listnotes|list|ln|l
```

Cancel a note:

```
{c}deletenote|delete|d <note text>
```
"""
        else:
            # Unknown help topic
            return

        await send_text_to_room(self.client, self.room.room_id, text)

    async def _unknown_command(self):
        """Computer says 'no'."""
        await send_text_to_room(
            self.client,
            self.room.room_id,
            f"Unknown help topic '{self.command}'. Try the '{CONFIG.command_prefix}help' command for more "
            f"information.",
        )
