"""Apply additive, versioned PostgreSQL migrations."""

from pathlib import Path

from oslo_energy.database.connection import create_connection, load_config


def apply_migrations() -> list[str]:
    migration_dir = Path(__file__).with_name("migrations")
    migrations = sorted(migration_dir.glob("*.sql"))
    applied_now = []

    with create_connection(load_config()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", (614782193,))
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS oslo_energy_schema_migrations ("
                "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            for migration in migrations:
                version = migration.name
                cursor.execute(
                    "SELECT 1 FROM oslo_energy_schema_migrations WHERE version = %s",
                    (version,),
                )
                if cursor.fetchone() is not None:
                    continue
                cursor.execute(migration.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO oslo_energy_schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied_now.append(version)
    return applied_now


def main() -> int:
    applied = apply_migrations()
    if applied:
        print("Applied migrations: " + ", ".join(applied))
    else:
        print("Database schema is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())