"""Explicit, administrative provisioning for the dedicated JobRadar database.

Never invoked by API, worker, bot, migrations, or container startup. Credentials
are read from the operator's environment; SQL contains SCRAM verifiers, not raw
passwords. The caller must obtain deployment approval before using a live DB.
"""

import argparse
import base64
import hashlib
import hmac
import os
import secrets
import sys

import psycopg
from psycopg import sql

from jobradar.db import Base

ROLES = ("migration", "api", "worker", "bot", "backup")
MARKER = "JobRadar managed database access v1"
APPLICATION_TABLES = frozenset(
    (
        "sources",
        "source_runs",
        "opportunities",
        "listings",
        "match_evaluations",
        "notification_deliveries",
        "notification_preferences",
        "notification_scan_cursors",
        "opportunity_user_states",
        "telegram_opportunity_messages",
    )
)
API_TABLES = frozenset(
    (
        "sources",
        "source_runs",
        "opportunities",
        "listings",
        "match_evaluations",
        "opportunity_user_states",
    )
)
BOT_WRITE_TABLES = frozenset(
    ("notification_preferences", "opportunity_user_states", "telegram_opportunity_messages")
)
BOT_READ_TABLES = API_TABLES | {"notification_preferences", "telegram_opportunity_messages"}
WORKER_WRITE_TABLES = APPLICATION_TABLES - {"notification_preferences"}
WORKER_DELETE_TABLES = frozenset(
    ("opportunities", "match_evaluations", "notification_deliveries", "opportunity_user_states")
)


def scram_verifier(password: str) -> str:
    """Build PostgreSQL's SCRAM verifier without putting plaintext in SQL."""
    salt = secrets.token_bytes(16)
    iterations = 4096
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client = hmac.digest(salted, b"Client Key", "sha256")
    stored = hashlib.sha256(client).digest()
    server = hmac.digest(salted, b"Server Key", "sha256")

    def encode(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return f"SCRAM-SHA-256${iterations}:{encode(salt)}${encode(stored)}:{encode(server)}"


def validate_passwords(passwords: dict[str, str]) -> None:
    if set(passwords) != set(ROLES):
        raise ValueError("All five role passwords are required.")
    if any(
        len(value) < 32
        or not value.isascii()
        or not value.isprintable()
        or "your_secure_password" in value
        for value in passwords.values()
    ):
        raise ValueError(
            "Role passwords must be distinct printable ASCII strings of 32+ characters."
        )
    if len(set(passwords.values())) != len(ROLES):
        raise ValueError("Role passwords must be distinct.")


def provision_access(
    connection: psycopg.Connection, database: str, passwords: dict[str, str]
) -> None:
    """Apply one transactional, repeatable policy to a dedicated application DB."""
    validate_passwords(passwords)
    if set(Base.metadata.tables) != APPLICATION_TABLES:
        raise ValueError("Changed application models require an explicit access policy review.")
    if database not in {"jobradar", "jobradar_test"}:
        raise ValueError("Only the dedicated JobRadar database is supported.")
    with connection.transaction():
        current = connection.execute(
            "SELECT current_database(), rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if current != (database, True):
            raise ValueError("Provisioning requires the matching database and an administrator.")
        owners = connection.execute(
            "SELECT pg_get_userbyid(d.datdba), pg_get_userbyid(n.nspowner) "
            "FROM pg_database d CROSS JOIN pg_namespace n "
            "WHERE d.datname=current_database() AND n.nspname='public'"
        ).fetchone()
        managed = {f"jobradar_{role}" for role in ROLES}
        if owners is None or owners[0] in managed or owners[1] in managed:
            raise ValueError("Database and public schema must remain administratively owned.")
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute("SELECT pg_advisory_xact_lock(534643333141)")
        relations = connection.execute(
            "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n "
            "ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND c.relkind IN ('r','p','v','m','f')"
        ).fetchall()
        allowed = APPLICATION_TABLES | {"alembic_version"}
        if any(name not in allowed or kind != "r" for name, kind in relations):
            raise ValueError("Unexpected public relations; review the dedicated database first.")
        routines = connection.execute(
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND NOT EXISTS (SELECT 1 FROM pg_depend d "
            "WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') LIMIT 1"
        ).fetchone()
        types = connection.execute(
            "SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
            "WHERE n.nspname='public' AND t.typtype IN ('e','d') AND NOT EXISTS "
            "(SELECT 1 FROM pg_depend d WHERE d.classid='pg_type'::regclass "
            "AND d.objid=t.oid AND d.deptype='e') LIMIT 1"
        ).fetchone()
        if routines is not None or types is not None:
            raise ValueError(
                "Custom public routines/types require an explicit access policy review."
            )
        column_acl = connection.execute(
            "SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND a.attacl IS NOT NULL LIMIT 1"
        ).fetchone()
        orphan_sequence = connection.execute(
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relkind='S' AND NOT EXISTS "
            "(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass "
            "AND d.objid=c.oid AND d.deptype IN ('a','i')) LIMIT 1"
        ).fetchone()
        if column_acl is not None or orphan_sequence is not None:
            raise ValueError(
                "Column grants or unattached sequences require an access policy review."
            )
        for suffix in ROLES:
            name = f"jobradar_{suffix}"
            existing = connection.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, "
                "shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname=%s",
                (name,),
            ).fetchone()
            if existing is not None and existing != (False, False, False, False, False, MARKER):
                raise ValueError("An existing role is not owned by this provisioning policy.")
            membership = connection.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles r "
                "ON r.oid=m.member OR r.oid=m.roleid WHERE r.rolname=%s LIMIT 1",
                (name,),
            ).fetchone()
            if membership is not None:
                raise ValueError("Managed roles must not have role memberships.")
            role = sql.Identifier(name)
            if existing is None:
                connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
            connection.execute(
                sql.SQL(
                    "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(role, sql.Literal(scram_verifier(passwords[suffix])))
            )
            connection.execute(
                sql.SQL("COMMENT ON ROLE {} IS {}").format(role, sql.Literal(MARKER))
            )
            connection.execute(sql.SQL("ALTER ROLE {} SET search_path = public").format(role))
            connection.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '30s'").format(role))
            connection.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = {}").format(
                    role, sql.Literal("on" if suffix in {"api", "backup"} else "off")
                )
            )
        db = sql.Identifier(database)
        connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
        connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        for suffix in ROLES:
            role = sql.Identifier(f"jobradar_{suffix}")
            connection.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM {}").format(db, role))
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, role))
            connection.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(role))
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        connection.execute("GRANT CREATE ON SCHEMA public TO jobradar_migration")
        # Install the trusted search extension administratively, not through the HTTP service.
        connection.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        for name, _ in relations:
            table = sql.Identifier("public", name)
            connection.execute(sql.SQL("ALTER TABLE {} OWNER TO jobradar_migration").format(table))
            connection.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM PUBLIC").format(table))
            for suffix in ROLES[1:]:
                role = sql.Identifier(f"jobradar_{suffix}")
                connection.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(table, role))
                permissions = ""
                if (
                    suffix == "backup"
                    or name in APPLICATION_TABLES
                    and suffix == "worker"
                    or name in BOT_READ_TABLES
                    and suffix == "bot"
                ):
                    permissions = "SELECT"
                if suffix == "api" and name in API_TABLES:
                    permissions = "SELECT"
                if suffix == "worker" and name in WORKER_WRITE_TABLES:
                    permissions += ", INSERT, UPDATE"
                    if name in WORKER_DELETE_TABLES:
                        permissions += ", DELETE"
                if suffix == "bot" and name in BOT_WRITE_TABLES:
                    permissions += ", INSERT, UPDATE"
                if permissions:
                    connection.execute(
                        sql.SQL("GRANT {} ON TABLE {} TO {}").format(
                            sql.SQL(permissions), table, role
                        )
                    )
        sequences = connection.execute(
            "SELECT s.relname, t.relname FROM pg_class s "
            "JOIN pg_namespace n ON n.oid=s.relnamespace "
            "JOIN pg_depend d ON d.objid=s.oid AND d.classid='pg_class'::regclass "
            "AND d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i') "
            "JOIN pg_class t ON t.oid=d.refobjid "
            "WHERE n.nspname='public' AND s.relkind='S'"
        ).fetchall()
        for name, table_name in sequences:
            sequence = sql.Identifier("public", name)
            connection.execute(
                sql.SQL("ALTER SEQUENCE {} OWNER TO jobradar_migration").format(sequence)
            )
            connection.execute(sql.SQL("REVOKE ALL ON SEQUENCE {} FROM PUBLIC").format(sequence))
            for suffix in ROLES[1:]:
                role = sql.Identifier(f"jobradar_{suffix}")
                connection.execute(
                    sql.SQL("REVOKE ALL ON SEQUENCE {} FROM {}").format(sequence, role)
                )
                permission = "SELECT" if suffix == "backup" else "USAGE"
                if (
                    suffix == "backup"
                    or suffix == "worker"
                    and table_name in WORKER_WRITE_TABLES
                    or suffix == "bot"
                    and table_name in BOT_WRITE_TABLES
                ):
                    connection.execute(
                        sql.SQL("GRANT {} ON SEQUENCE {} TO {}").format(
                            sql.SQL(permission), sequence, role
                        )
                    )
        # New objects are denied to runtime roles until the reviewed allowlist is reapplied.
        # Global revocation is required: schema-local REVOKE cannot undo PUBLIC defaults.
        for category, privilege in (
            ("TABLES", "ALL"),
            ("SEQUENCES", "ALL"),
            ("FUNCTIONS", "EXECUTE"),
            ("TYPES", "USAGE"),
        ):
            for scope in ("", " IN SCHEMA public"):
                for recipient in ("PUBLIC", *(f"jobradar_{role}" for role in ROLES[1:])):
                    connection.execute(
                        sql.SQL(
                            "ALTER DEFAULT PRIVILEGES FOR ROLE jobradar_migration{} "
                            "REVOKE {} ON {} FROM {}"
                        ).format(
                            sql.SQL(scope),
                            sql.SQL(privilege),
                            sql.SQL(category),
                            sql.SQL("PUBLIC")
                            if recipient == "PUBLIC"
                            else sql.Identifier(recipient),
                        )
                    )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE jobradar_migration IN SCHEMA public "
            "GRANT SELECT ON TABLES TO jobradar_backup"
        )
        connection.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE jobradar_migration IN SCHEMA public "
            "GRANT SELECT ON SEQUENCES TO jobradar_backup"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, choices=("jobradar", "jobradar_test"))
    arguments = parser.parse_args()
    try:
        passwords = {role: os.environ[f"DB_{role.upper()}_PASSWORD"] for role in ROLES}
        with psycopg.connect(os.environ["DB_ADMIN_URL"], connect_timeout=10) as connection:
            provision_access(connection, arguments.database, passwords)
    except (KeyError, ValueError, psycopg.Error):
        # Driver diagnostics can contain connection strings or SQL credential verifiers.
        print("Database access provisioning failed; no policy changes committed.", file=sys.stderr)
        return 1
    print("Database access policy applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
