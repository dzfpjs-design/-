"""Add lossless storage without guessing or rewriting an old JSONB payload.

Existing rows receive only a legacy marker. New SQLAlchemy writers supply a
paired envelope; raw/old writers cannot silently introduce another legacy row.
Run during the controlled writer upgrade, before accepting application work.
"""
from sqlalchemy import inspect, text
from sqlalchemy.schema import AddConstraint

from .typed_payload_codec_v131 import LEGACY_MARKER
from .typed_payload_orm_v131 import FORMAT, CANONICAL, constraint_name


_TRIGGER_FUNCTION = '''
CREATE OR REPLACE FUNCTION ga_typed_payload_no_downgrade_v131()
RETURNS trigger LANGUAGE plpgsql AS $guard$
BEGIN
  IF TG_OP = 'INSERT' AND NEW._ga_data_format = 'legacy-jsonb-v1' THEN
    RAISE EXCEPTION 'typed payload pairing required' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF TG_NARGS > 0 AND TG_ARGV[0] = 'data-write'
       AND (NEW._ga_data_format IS NOT DISTINCT FROM OLD._ga_data_format
            OR NEW._ga_data_canonical IS NOT DISTINCT FROM OLD._ga_data_canonical
            OR NEW._ga_data_format = 'legacy-jsonb-v1') THEN
      RAISE EXCEPTION 'typed payload pairing required' USING ERRCODE = '23514';
    END IF;
    IF OLD._ga_data_format <> 'legacy-jsonb-v1'
       AND NEW._ga_data_format = 'legacy-jsonb-v1' THEN
      RAISE EXCEPTION 'typed payload downgrade prohibited' USING ERRCODE = '23514';
    END IF;
    IF OLD._ga_data_format = 'legacy-jsonb-v1'
       AND NEW._ga_data_format = 'legacy-jsonb-v1'
       AND NEW.data::text IS DISTINCT FROM OLD.data::text THEN
      RAISE EXCEPTION 'typed payload pairing required' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$guard$
'''


def migrate_typed_payloads(engine, metadata):
    tables = [table for table in metadata.sorted_tables
        if FORMAT in table.c and CANONICAL in table.c]
    if not tables:
        return
    with engine.begin() as connection:
        connection.execute(text('SELECT pg_advisory_xact_lock(734106131)'))
        connection.execute(text(_TRIGGER_FUNCTION))
        inspector = inspect(connection)
        quote = connection.dialect.identifier_preparer.quote
        for table in tables:
            qualified = connection.dialect.identifier_preparer.format_table(table)
            columns = {column['name']: column for column in inspector.get_columns(table.name, schema=table.schema)}
            if FORMAT not in columns:
                connection.execute(text(f'ALTER TABLE {qualified} ADD COLUMN {quote(FORMAT)} TEXT NOT NULL DEFAULT \'{LEGACY_MARKER}\''))
            if CANONICAL not in columns:
                connection.execute(text(f'ALTER TABLE {qualified} ADD COLUMN {quote(CANONICAL)} TEXT'))
            if columns.get(FORMAT, {}).get('default') != "'typed-json-v131:unpaired'::text":
                connection.execute(text(f"ALTER TABLE {qualified} ALTER COLUMN {quote(FORMAT)} SET DEFAULT 'typed-json-v131:unpaired'"))
            name = constraint_name(table)
            constraints = {item['name'] for item in inspector.get_check_constraints(table.name, schema=table.schema)}
            if name not in constraints:
                constraint = next(item for item in table.constraints if item.name == name)
                connection.execute(AddConstraint(constraint))
            trigger = 'tr_ga_typed_payload_v131'
            exists = connection.scalar(text('SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass(:relation) AND tgname=:name AND NOT tgisinternal)'), {'relation': qualified, 'name': trigger})
            if not exists:
                connection.execute(text(f'CREATE TRIGGER {quote(trigger)} BEFORE INSERT OR UPDATE ON {qualified} FOR EACH ROW EXECUTE FUNCTION ga_typed_payload_no_downgrade_v131()'))
            data_trigger = 'tr_ga_typed_payload_data_v131'
            data_exists = connection.scalar(text('SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass(:relation) AND tgname=:name AND NOT tgisinternal)'), {'relation': qualified, 'name': data_trigger})
            if not data_exists:
                connection.execute(text(f"CREATE TRIGGER {quote(data_trigger)} BEFORE UPDATE OF data ON {qualified} FOR EACH ROW EXECUTE FUNCTION ga_typed_payload_no_downgrade_v131('data-write')"))
