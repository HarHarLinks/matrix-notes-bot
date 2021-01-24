import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple

import pytz
from apscheduler.util import timedelta_seconds
from nio import AsyncClient

from matrix_notes_bot.config import CONFIG
from matrix_notes_bot.note import NOTES, Note

latest_migration_version = 3

logger = logging.getLogger(__name__)


class Storage(object):
    def __init__(self, client: AsyncClient):
        """Setup the database

        Runs an initial setup or migrations depending on whether a database file has already
        been created

        Args:
            client: The matrix client
        """
        # Check which type of database has been configured
        self.client = client
        self.conn = self._get_database_connection(
            CONFIG.database.type, CONFIG.database.connection_string
        )
        self.cursor = self.conn.cursor()
        self.db_type = CONFIG.database.type

        # Try to check the current migration version
        migration_level = 0
        try:
            self._execute("SELECT version FROM migration_version")
            row = self.cursor.fetchone()
            migration_level = row[0]
        except Exception:
            self._initial_db_setup()
        finally:
            if migration_level < latest_migration_version:
                self._run_db_migrations(migration_level)

        # Load notes from the db
        NOTES.update(self._load_notes())

        logger.info(f"Database initialization of type '{self.db_type}' complete")

    def _get_database_connection(self, database_type: str, connection_string: str):
        if database_type == "sqlite":
            import sqlite3

            # Initialize a connection to the database, with autocommit on
            return sqlite3.connect(connection_string, isolation_level=None)
        elif database_type == "postgres":
            import psycopg2

            conn = psycopg2.connect(connection_string)

            # Autocommit on
            conn.set_isolation_level(0)

            return conn

    def _execute(self, *args):
        """A wrapper around cursor.execute that transforms ?'s to %s for postgres"""
        if self.db_type == "postgres":
            self.cursor.execute(args[0].replace("?", "%s"), *args[1:])
        else:
            self.cursor.execute(*args)

    def _initial_db_setup(self):
        """Initial setup of the database"""
        logger.info("Performing initial database setup...")

        # Set up the migration_version table
        self._execute(
            """
            CREATE TABLE migration_version (
                version INTEGER PRIMARY KEY
            )
        """
        )

        # Initially set the migration version to 0
        self._execute(
            """
            INSERT INTO migration_version (
                version
            ) VALUES (?)
        """,
            (0,),
        )

        # Set up the notes table
        self._execute(
            """
            CREATE TABLE note (
                text TEXT,
                category TEXT,
                room_id TEXT NOT NULL,
                target_user TEXT
            )
        """
        )

        # Create a unique index on room_id, note text as no two notes in the same
        # room can have the same note text
        self._execute(
            """
            CREATE UNIQUE INDEX note_room_id_text
            ON note(room_id, text)
        """
        )

    def _run_db_migrations(self, current_migration_version: int):
        """Execute database migrations. Migrates the database to the
        `latest_migration_version`

        Args:
            current_migration_version: The migration version that the database is
                currently at
        """
        logger.debug("Checking for necessary database migrations...")

        if current_migration_version < 1:
            logger.info("Migrating the database from v0 to v1...")

            # Add cron_tab column, prevent start_time from being required
            #
            # As SQLite3 is quite limited, we need to create a new table and populate it
            # with existing data
            self._execute("ALTER TABLE note RENAME TO note_temp")

            self._execute(
                """
                CREATE TABLE note (
                    text TEXT,
                    category TEXT,
                    room_id TEXT NOT NULL,
                    target_user TEXT
                )
           """
            )
            self._execute(
                """
                INSERT INTO note (
                    text,
                    category,
                    room_id,
                    target_user
                )
                SELECT
                    text,
                    category,
                    room_id,
                    target_user
                FROM note_temp;
           """
            )

            self._execute(
                """
                 DROP INDEX note_room_id_text
           """
            )
            self._execute(
                """
                CREATE UNIQUE INDEX note_room_id_text
                ON note(room_id, text)
           """
            )

            self._execute(
                """
                DROP TABLE note_temp
           """
            )

            self._execute(
                """
                 UPDATE migration_version SET version = 1
            """
            )

            logger.info("Database migrated to v1")

    def _load_notes(self) -> Dict[Tuple[str, str], Note]:
        """Load notes from the database

        Returns:
            A dictionary from (room_id, note text) to Note object
        """
        self._execute(
            """
            SELECT
                text,
                category,
                room_id,
                target_user
            FROM note
        """
        )
        rows = self.cursor.fetchall()
        logger.debug("Loaded note rows: %s", rows)
        notes = {}

        for row in rows:
            # Extract note data
            note_text = row[0]
            category = row[1] if row[1] else None
            room_id = row[5]
            target_user = row[6]

            # Create and record the note
            notes[(room_id, note_text.upper())] = Note(
                client=self.client,
                store=self,
                note_text=note_text,
                room_id=room_id,
                target_user=target_user,
            )

        return notes

    def store_note(self, note: Note):
        """Store a new note in the database"""
        # timedelta.seconds does NOT give you the timedelta converted to seconds
        # Use a method from apscheduler instead

        self._execute(
            """
            INSERT INTO note (
                text,
                category,
                room_id,
                target_user
            ) VALUES (
                ?, ?, ?, ?
            )
        """,
            (
                note.note_text,
                note.category,
                note.room_id,
                note.target_user,
            ),
        )

    def delete_note(self, room_id: str, note_text: str):
        """Delete a note via its note text and the room it was sent in"""
        self._execute(
            """
            DELETE FROM note WHERE room_id = ? AND text = ?
        """,
            (room_id, note_text),
        )
