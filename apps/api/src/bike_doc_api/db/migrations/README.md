# Alembic Migrations

Migration revisions live in `versions/`.

Revision `0010` permits an unbound (null ADK ID) app-owned phase reference for
queue-selected turn acceptance. Apply it before enabling the profile canary.
The diagnostic background host deterministically initializes and binds the ADK
session after acceptance. Downgrade requires every retained reference to be
bound; it intentionally fails if unbound accepted work remains.
