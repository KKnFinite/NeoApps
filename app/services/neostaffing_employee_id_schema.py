"""Explicit PostgreSQL upgrade for Python strip/lower Employee ID uniqueness.

Do not use database-locale lower/trim: neither has Python's Unicode semantics.
The immutable SQL function contains only generated Unicode character tables,
not employee data. Its definition is verified, never replaced beneath an index.
"""
from functools import lru_cache

from sqlalchemy import inspect, text

from app.extensions import db


INDEX_NAME = "uq_staffing_people_employee_id_normalized"
FUNCTION_NAME = "staffing_employee_id_key"


class EmployeeIdMigrationBlocked(RuntimeError):
    pass


def audit_employee_ids(connection):
    """Read-only preflight with the application's exact normalization."""
    groups, invalid = {}, []
    rows = connection.execute(text(
        "SELECT id, employee_id FROM staffing_people ORDER BY id"
    ))
    for row in rows:
        if row.employee_id is None or not row.employee_id.strip():
            invalid.append((row.id, row.employee_id))
        else:
            groups.setdefault(row.employee_id.strip().lower(), []).append(
                (row.id, row.employee_id)
            )
    collisions = [records for records in groups.values() if len(records) > 1]
    if invalid or collisions:
        raise EmployeeIdMigrationBlocked(
            "Employee ID upgrade blocked; no employees were changed. "
            f"Normalized collisions (person ID, stored Employee ID): {collisions!r}; "
            f"NULL/blank IDs requiring review: {invalid!r}. "
            "Resolve records explicitly before retrying bootstrap."
        )
    return sum(len(records) for records in groups.values())


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


@lru_cache(maxsize=1)
def normalization_body():
    """Freeze this Python runtime's Unicode lower/strip rules into SQL.

    Unicode default lowercase has one contextual rule: Final_Sigma. Derive
    Cased/Case_Ignorable from that rule itself, including cased-but-ignorable
    characters, instead of approximating them with alphabetic/category tests.
    Other lowercase mappings are character-local (including expansions).
    This memoizes code generation only; there is no cached application data.
    """
    whitespace, source, target, expansions, cased, ignorable = [], [], [], [], [], []
    for code in range(1, 0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue  # PostgreSQL UTF-8 text cannot contain surrogates or NUL.
        char = chr(code)
        if char.isspace():
            whitespace.append(char)
        lower = char.lower()
        if lower != char:
            if len(lower) == 1:
                source.append(char)
                target.append(lower)
            else:
                expansions.append((char, lower))
        if ("AΣ" + char).lower()[1] == "σ":
            cased.append(char)
        elif ("AΣ" + char + "A").lower()[1] == "σ":
            ignorable.append(char)

    lowered = "translate(value, " + _literal("".join(source)) + ", " + _literal("".join(target)) + ")"
    for original, expanded in expansions:
        lowered = f"replace({lowered}, {_literal(original)}, {_literal(expanded)})"
    return f"""
DECLARE
  value text := btrim(input, {_literal(''.join(whitespace))});
  cased constant text := {_literal(''.join(cased))};
  ignorable constant text := {_literal(''.join(ignorable))};
  position integer; neighbor integer; before_cased boolean;
BEGIN
  IF strpos(value, 'Σ') > 0 THEN
    FOR position IN 1..char_length(value) LOOP
      IF substr(value, position, 1) <> 'Σ' THEN CONTINUE; END IF;
      neighbor := position - 1;
      WHILE neighbor > 0 AND strpos(ignorable, substr(value, neighbor, 1)) > 0 LOOP
        neighbor := neighbor - 1;
      END LOOP;
      before_cased := neighbor > 0 AND strpos(cased, substr(value, neighbor, 1)) > 0;
      IF NOT before_cased THEN CONTINUE; END IF;
      neighbor := position + 1;
      WHILE neighbor <= char_length(value) AND strpos(ignorable, substr(value, neighbor, 1)) > 0 LOOP
        neighbor := neighbor + 1;
      END LOOP;
      IF neighbor > char_length(value) OR strpos(cased, substr(value, neighbor, 1)) = 0 THEN
        value := overlay(value placing 'ς' from position for 1);
      END IF;
    END LOOP;
  END IF;
  RETURN {lowered};
END
"""


def sync_staffing_employee_id_schema():
    """Called by explicit deployment bootstrap; caller owns commit/rollback."""
    connection = db.session.connection()
    if connection.dialect.name != "postgresql" or not inspect(connection).has_table("staffing_people"):
        return
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    # Serialize bootstrap attempts and block writers through audit + index DDL.
    # Readers continue. This is deployment-only, not Bulk Change locking.
    connection.execute(text("LOCK TABLE staffing_people IN SHARE ROW EXCLUSIVE MODE"))
    audit_employee_ids(connection)
    body = normalization_body()
    existing = connection.execute(text("""
        SELECT p.prosrc, p.provolatile, p.proisstrict, p.proparallel, l.lanname,
               p.prosecdef, p.proconfig, p.prorettype = 'text'::regtype AS returns_text
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.oid=to_regprocedure('staffing_employee_id_key(text)')
    """)).mappings().first()
    if existing:
        if (existing['prosrc'] != body or existing['provolatile'] != 'i'
                or not existing['proisstrict'] or existing['proparallel'] != 's'
                or existing['lanname'] != 'plpgsql' or existing['prosecdef']
                or existing['proconfig'] or not existing['returns_text']):
            raise EmployeeIdMigrationBlocked(
                "Employee ID normalization definition changed. An explicit audited "
                "index migration is required; bootstrap will not replace it."
            )
    else:
        connection.execute(text(f"CREATE FUNCTION {FUNCTION_NAME}(input text) RETURNS text "
                                f"LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $employee_id${body}$employee_id$"))
    existing_index = connection.execute(text("""
        SELECT i.indisunique AND i.indisvalid AND i.indisready AND i.indimmediate
               AND i.indnkeyatts=1 AND i.indnatts=1 AND i.indpred IS NULL
               AND i.indcollation[0]='"C"'::regcollation
               AND i.indrelid='staffing_people'::regclass AS valid,
               pg_get_indexdef(i.indexrelid, 1, true) AS expression
        FROM pg_index i WHERE i.indexrelid=to_regclass(:name)
    """), {'name': INDEX_NAME}).mappings().first()
    if existing_index:
        if (not existing_index['valid'] or existing_index['expression'] !=
                'staffing_employee_id_key(employee_id::text)'):
            raise EmployeeIdMigrationBlocked(f"Unexpected Employee ID index definition {dict(existing_index)!r}; manual schema review required.")
    else:
        connection.execute(text(f"CREATE UNIQUE INDEX {INDEX_NAME} ON staffing_people "
                                f"(({FUNCTION_NAME}(employee_id) COLLATE \"C\"))"))
