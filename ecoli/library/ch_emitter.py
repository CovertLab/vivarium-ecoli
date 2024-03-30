import atexit
import csv
import os
import subprocess
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Mapping

import clickhouse_connect
import orjson
from clickhouse_connect.driver.tools import insert_file
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

CLIENT_SETTINGS = {
    'allow_experimental_object_type': 1,
    'input_format_null_as_default': 1
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
    col_types = []
    for k, v in new_fields.items():
        ch_type, ch_codec = get_ch_type(k, v)
        col_types.append(f'`{k}` {ch_type} CODEC({ch_codec})')
    create_table_cmd = "CREATE TABLE IF NOT EXISTS {table_id:Identifier} ( " \
        f"{', '.join(col_types)} ) ENGINE = MergeTree " \
        "PRIMARY KEY (experiment_id,variant,seed,generation,agent_id,time)"
    add_cols_cmd = "ALTER TABLE {table_id:Identifier} ADD COLUMN IF NOT " \
        f"EXISTS {', ADD COLUMN IF NOT EXISTS '.join(col_types)}"
    curr_cols_cmd = "SELECT name from system.columns WHERE " \
        "table={table_id:String} ORDER BY position"
    client = clickhouse_connect.get_client(
        **conn_args, settings=CLIENT_SETTINGS)
    client.command(create_table_cmd,  {'table_id': table_id})
    client.command(add_cols_cmd,  {'table_id': table_id})
    curr_cols = client.query_np(curr_cols_cmd, {'table_id': table_id})
    return curr_cols[:, 0].tolist()


def insert_data(emit_data: dict[str, Any],
                column_names: list[str],
                experiment_id: str):
    """
    Called by :py:func:`~.executor_proc` to insert data.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        cell_id: Unique identifier for data emitted by one simulated cell
        inserts: Tuples ``(table_id, values, columns)``
    """
    write_header = False
    if not os.path.isfile(f'{experiment_id}_temp.tsv'):
        write_header = True
    with open(f'{experiment_id}_temp.tsv', 'a+', newline='') as f:
        tsv_writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        if write_header:
            tsv_writer.writerow(column_names)
        tsv_writer.writerow((emit_data.get(k, r'\N') for k in column_names))

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
            'port': config.get('port', 8123),
            'user': config.get('user', 'default'),
            'database': config.get('database', 'default'),
            'password': config.get('password', '')
        }
        self.executor = ProcessPoolExecutor(1)
        self.curr_fields = []
        self.fallback_serializer = make_fallback_serializer_function()
        atexit.register(self._push_to_db)

    def _push_to_db(self):
        if len(self.curr_fields) == 0:
            return
        subprocess.run(['zstd', f'{self.experiment_id}_temp.tsv', '-o',
            f'{self.experiment_id}_temp.gz', '--rm', '-f'], check=True)
        client = clickhouse_connect.get_client(
            **self.connection_args, settings=CLIENT_SETTINGS)
        insert_file(client, 'history', f'{self.experiment_id}_temp.gz',
            'TSVWithNames', settings=CLIENT_SETTINGS, compression='zstd')
        subprocess.run(['rm', f'{self.experiment_id}_temp.gz'], check=True)

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
            curr_config_fields = add_new_fields(
                self.connection_args, data, 'configuration')
            data = [data[k] for k in curr_config_fields]
            client = clickhouse_connect.get_client(
                **self.connection_args, settings=CLIENT_SETTINGS)
            client.insert('configuration', [data])
            return
        for agent_id, agent_data in data['data']['agents'].items():
            agent_data['generation'] = len(agent_id)
            agent_data['agent_id'] = agent_id
            agent_data['time'] = data['data']['time']
            agent_data['seed'] = 0
            agent_data['variant'] = ''
            agent_data['experiment_id'] = self.experiment_id
            agent_data = flatten_dict(agent_data)
            new_cols = set(agent_data) - set(self.curr_fields)
            if len(new_cols) > 0:
                self._push_to_db()
                self.curr_fields = add_new_fields(self.connection_args,
                    {k: agent_data[k] for k in new_cols}, 'history')
            self.executor.submit(insert_data,
                agent_data, self.curr_fields, self.experiment_id)
