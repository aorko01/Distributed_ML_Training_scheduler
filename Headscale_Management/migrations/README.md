# Schema migrations

Version 1 is applied explicitly by `python -m headscale_management.database`.
No import or web startup creates tables. `PRAGMA user_version` fences unknown
schemas. Before an existing file changes, SQLite's backup API copies committed
state including WAL into a private `.pre-migration` backup. Future migrations
must be sequential and compatible with the maintenance/restart contract; never
reset a database to resolve a version error. Backups contain secrets: retain them
on protected state storage and never upload them as CI diagnostics.
