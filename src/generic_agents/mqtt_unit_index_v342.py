"""Online, additive index migration for MQTT unit-proof custody."""
import re
from sqlalchemy import text

NAME='uq_audit_mqtt_units_v342'
ACTION='mqtt.observation.units_captured_v342'
DDL=("CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_audit_mqtt_units_v342 "
     "ON audit_events (workspace_id, object_id) "
     "WHERE action = 'mqtt.observation.units_captured_v342'")


def _checked(connection):
    row=connection.execute(text("""
        SELECT i.indisunique,i.indisvalid,i.indisready,i.indnkeyatts,i.indnatts,
               pg_get_indexdef(i.indexrelid,1,true),pg_get_indexdef(i.indexrelid,2,true),
               pg_get_expr(i.indpred,i.indrelid)
        FROM pg_index i WHERE i.indexrelid=to_regclass(:index_name)
          AND i.indrelid=to_regclass('audit_events')
    """),{'index_name':NAME}).first()
    if row is None:
        raise RuntimeError('MQTT 单位证明索引未找到或指向了错误的数据表')
    predicate=re.sub(r'\s+|[()]|::(?:text|character varying)','',row[7] or '').lower()
    expected="action='"+ACTION+"'"
    if not (row[0] and row[1] and row[2] and row[3]==row[4]==2
            and row[5]=='workspace_id' and row[6]=='object_id' and predicate==expected):
        raise RuntimeError('MQTT 单位证明索引无效或定义不一致；保留原索引并检查迁移')


def ensure(engine):
    """Create without blocking writes; never drop or trust an invalid index."""
    if engine.dialect.name!='postgresql':
        raise RuntimeError('MQTT 单位证明需要 PostgreSQL 唯一索引')
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
        connection.execute(text(DDL))
        _checked(connection)
