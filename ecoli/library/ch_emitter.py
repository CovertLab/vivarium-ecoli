from concurrent.futures import ProcessPoolExecutor
from typing import Any, Mapping
from clickhouse_driver import Client

import orjson
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function


json_enable = {
    'allow_experimental_object_type': 1
}


def get_ch_type(fieldname, py_val):
    if isinstance(py_val, bool):
        return 'Bool', 'ZSTD'
    elif isinstance(py_val, int):
        return 'Int64', 'Delta,ZSTD'
    elif isinstance(py_val, float):
        return 'Float64', 'Gorilla,ZSTD'
    elif isinstance(py_val, str):
        return 'LowCardinality(String)', 'ZSTD'
    elif isinstance(py_val, Mapping):
        return 'JSON', 'ZSTD'
    elif isinstance(py_val, list):
        inner_fieldname = fieldname + '.0'
        inner_type, codec = get_ch_type(inner_fieldname, py_val[0])
        if inner_type == 'Int64' or inner_type == 'Bool':
            codec = 'T64,ZSTD'
        else:
            codec = 'ZSTD'
        return f'Array({inner_type})', codec
    raise TypeError(f'{fieldname} has unsupported type {type(py_val)}')


CREATE_CMD = """
CREATE TABLE IF NOT EXISTS {}
(
    {}
) ENGINE = MergeTree
PRIMARY KEY (experiment_id, variant, seed, generation, agent_id, time)
"""


def add_new_fields(conn_args: dict[str, Any],
                   new_fields: dict[str, str],
                   table_id: str):
    """
    Create new fields in a table.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        table_id: Name of table to create new fields for
        field_types: Mapping of new field names to types (from
            :py:func:`~.get_pg_type`)

    Returns:
        Mapping of fields to placeholder names after any new fields added.
    """
    col_spec = {}
    for k, v in new_fields.items():
        ch_type, ch_codec = get_ch_type(k, v)
        col_spec[k] = f'`{k}` {ch_type} CODEC({ch_codec})'
    create_col_spec = ','.join(col_spec.values())
    client = Client(**conn_args, settings=json_enable, compression='zstd')
    client.execute(CREATE_CMD.format(table_id, create_col_spec))
    # Get current columns before we decide to make expensive alterations
    curr_cols = client.execute(f"SELECT name from system.columns WHERE table='{table_id}'")
    curr_cols = set(i[0] for i in curr_cols)
    actual_new_cols = set(new_fields) - curr_cols
    if len(actual_new_cols) == 0:
        return
    add_col_spec = ', ADD COLUMN IF NOT EXISTS '.join(
        [col_spec[k] for k in actual_new_cols])
    client.execute(
        f"ALTER TABLE {table_id} ADD COLUMN IF NOT EXISTS {add_col_spec}")


def insert_data(conn_args: dict[str, Any],
                insert_dict: dict[str, Any],
                table_id: str):
    """
    Called by :py:func:`~.executor_proc` to insert data.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        cell_id: Unique identifier for data emitted by one simulated cell
        inserts: Tuples ``(table_id, values, columns)``
    """
    client = Client(**conn_args, settings=json_enable, compression='zstd')
    column_names = '`, `'.join(insert_dict.keys())
    column_names = '`' + column_names + '`'
    client.execute(
        f'INSERT INTO {table_id} ({column_names}) VALUES',
        (list(insert_dict.values()),)
    )

_FLAG_FIRST = object()

def flatten_dict(d: dict):
    """
    Flatten nested dictionary down to key-value pairs where each key
    concatenates all the keys needed to reach the
    corresponding value in the input. Prunes empty dicts and lists.
    """
    results = []

    def visit_key(subdict, results, partialKey):
        for k, v in subdict.items():
            newKey = k if partialKey==_FLAG_FIRST else f'{partialKey}__{k}'
            if isinstance(v, Mapping):
                visit_key(v, results, newKey)
            elif isinstance(v, list) and len(v) == 0:
                continue
            elif v is None:
                continue
            else:
                results.append((newKey, v))

    visit_key(d, results, _FLAG_FIRST)
    return dict(results)


class ChEmitter(Emitter):
    """
    Emit data to a ClickHouse database. Creates a separate OS thread
    to handle the insert operations.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Pull connection arguments from ``config`` and start separate OS
        process for database inserts.

        Args:
            config: Must include ``experiment_id`` key. Can include keys for
                ``host``, ``port``, ``user``, ``database``, and ``password``
                to use as keyword arguments for :py:func:`asyncpg.connect`. 
        """
        self.experiment_id = config.get('experiment_id')
        # Collect connection arguments
        self.connection_args = {
            'host': config.get('host', 'localhost'),
            'port': config.get('port', 9000),
            'user': config.get('user', 'default'),
            'database': config.get('database', 'default'),
            'password': config.get('password', '')
        }
        self.executor = ProcessPoolExecutor(1)
        self.curr_fields = set()
        self.fallback_serializer = make_fallback_serializer_function()
        self.batched_emits = []

    def emit(self, data: dict[str, Any]):
        data = orjson.loads(orjson.dumps(
            data, option=orjson.OPT_SERIALIZE_NUMPY,
            default=self.fallback_serializer))
        # Config will always be first emit
        if data['table'] == 'configuration':
            metadata = data['data'].pop('metadata')
            data['data'] = {**metadata, **data['data']}
            data['experiment_id'] = data['data'].pop('experiment_id')
            data['agent_id'] = data['data'].pop('agent_id')
            data['seed'] = data['data'].pop('seed')
            data['generation'] = len(data['agent_id'])
            # TODO: These keys need to be added
            data['variant'] = 0
            data['time'] = 0
            add_new_fields(self.connection_args, data, 'configuration')
            self.executor.submit(insert_data,
                self.connection_args, data, 'configuration')
            return
        for agent_id, agent_data in data['data']['agents'].items():
            agent_data['generation'] = len(agent_id)
            agent_data['agent_id'] = agent_id
            agent_data['time'] = data['data']['time']
            agent_data['seed'] = 0
            agent_data['variant'] = ''
            agent_data['experiment_id'] = self.experiment_id
            agent_data = flatten_dict(agent_data)
            new_cols = set(agent_data) - self.curr_fields
            if len(new_cols) > 0:
                add_new_fields(self.connection_args,
                    {k: agent_data[k] for k in new_cols}, 'history')
                self.curr_fields.update(set(agent_data))
            self.executor.submit(insert_data,
                self.connection_args, agent_data, 'history')
