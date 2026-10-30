# JSON files as the source of truth, no database

Project state lives in plain JSON files inside the project folder: a Request record written the moment it is submitted, and a metadata file next to every downloaded Generation. There is no database, so a project stays self-contained, readable by hand and safe to copy or move; a request id is never lost on a dropped connection. Listing scans the files; if that gets slow (e.g. for the stage 2 web UI), an index such as SQLite can be built on top, but it must stay derived from the files, never the other way round.
