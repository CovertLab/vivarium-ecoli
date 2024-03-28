import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from typing import Any, Callable, Iterable, Mapping
from datetime import datetime, timedelta
import pytz

import asyncpg
import orjson
import uvloop
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

INDEXES_TO_CREATE = [
    'experiment_id', 'variant', 'seed', 'generation', 'agent_id']
"""Default indexes to create for ``configuration`` table."""


async def create_hypertable(conn_args):
    con = await asyncpg.connect(**conn_args)
    await con.execute('CREATE TABLE IF NOT EXISTS history_colnames ('
        'seqname SERIAL PRIMARY KEY, fieldname TEXT UNIQUE)')
    await con.execute('CREATE TABLE IF NOT EXISTS history ('
        'exp_id_time TIMESTAMPTZ, agent_id TEXT)')
    await con.execute(
        "SELECT create_hypertable('history', "
        "by_range('exp_id_time', INTERVAL '400 seconds'), "
        "if_not_exists => TRUE)")
    await con.execute('CREATE INDEX IF NOT EXISTS agent_exp_id_idx '
        'ON history (agent_id, exp_id_time)')
    await con.execute(
        "ALTER TABLE history SET (timescaledb.compress = TRUE, "
        "timescaledb.compress_orderby = 'exp_id_time',"
        "timescaledb.compress_segmentby = 'agent_id')")
    await con.execute(
        "SELECT add_compression_policy('history', "
        "compress_created_before => INTERVAL '5 minutes', "
        "if_not_exists => TRUE)")
    await con.close()


async def add_new_fields(conn_args: dict[str, Any],
                         field_types: dict[str, str]) -> dict[str, str]:
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
    con = await asyncpg.connect(**conn_args)
    # fieldname has UNIQUE constraint and should raise a conflict for
    # duplicates. We tell PostgreSQL to skip inserting these duplicates.
    cmd_prefix = f'INSERT INTO history_colnames (fieldname) VALUES '
    col_values = []
    for i in range(len(field_types)):
        col_values.append(f'(${i+1})')
    col_values = ", ".join(col_values)
    cmd = ''.join([cmd_prefix, col_values, ' ON CONFLICT DO NOTHING'])
    await con.execute(cmd, *field_types)
    res = await con.fetch(
        f"SELECT (fieldname, seqname) FROM history_colnames")
    field_to_placeholder = dict(tuple(i)[0] for i in res)
    col_str = [f'"{field_to_placeholder[k]}" {v}'
                    for k, v in field_types.items()]
    # If any new column names are duplicates, nothing should happen.
    add_cols = ', '.join([f'ADD COLUMN IF NOT EXISTS {i}' for i in col_str])
    await con.execute(f'ALTER TABLE history {add_cols}')
    await con.close()
    return field_to_placeholder


async def insert_data(conn_args: dict[str, Any], cell_id: int,
                      insert_dict: dict[str, Any],
                      field_to_placeholder: dict[str, int],
                      time: float, agent_id: str):
    """
    Called by :py:func:`~.executor_proc` to insert data.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        cell_id: Unique identifier for data emitted by one simulated cell
        inserts: Tuples ``(table_id, values, columns)``
    """
    cell_id_time = datetime(cell_id, 1, 1) + timedelta(
        seconds=time)
    records = [(cell_id_time, agent_id, *insert_dict.values())]
    columns = (str(field_to_placeholder[k]) for k in insert_dict)
    columns = ['exp_id_time', 'agent_id', *columns]
    conn = await asyncpg.connect(**conn_args)
    await conn.copy_records_to_table(
        'history', records=records, columns=columns)
    await conn.close()


def executor_proc(conn_args: dict[str, Any], cell_id: int,
                  insert_dict: dict[str, Any],
                  field_to_placeholder: dict[str, int],
                  time: float, agent_id: str):
    """
    Called by ThreadPoolExecutor insert data.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        cell_id: Unique identifier for data emitted by one simulated cell
        inserts: Tuples ``(table_id, records, columns)``
            for :py:meth:`asyncpg.Connection.copy_records_to_table`
    
    Returns:
        Cell ID used to identify all rows from this simulation.
    """
    return asyncio.run(insert_data(
        conn_args, cell_id, insert_dict, field_to_placeholder, time, agent_id))


_FLAG_FIRST = object()

def flatten_and_serialize(d: dict, default: Callable, first_run: bool):
    """
    Flatten nested dictionary down to key-value pairs where each key
    concatenates all the keys needed to reach the corresponding value
    in the input into comma-separated string. Prunes empty dicts.
    """
    results = []
    result_types = []
    def visit(subdict, results, result_types, partialKey):
        for k, v in subdict.items():
            newKey = k if partialKey==_FLAG_FIRST else f'{partialKey}, {k}'
            if isinstance(v, Mapping):
                visit(v, results, result_types, newKey)
            else:
                py_val = orjson.loads(orjson.dumps(
                    v, option=orjson.OPT_SERIALIZE_NUMPY, default=default))
                results.append((newKey, py_val))
                pg_type = get_pg_type(py_val)
                if first_run and 'empty list' in pg_type:
                    raise TypeError(f'({newKey}) contains an empty list. PostgreSQL '
                                    'cannot infer correct column type to create.')
                elif 'unsupported' in pg_type:
                    raise TypeError(f'({newKey}) contains unsupported type.')
                result_types.append((newKey, pg_type))
    visit(d, results, result_types, _FLAG_FIRST)
    return dict(results), dict(result_types)


def get_pg_type(py_val: Any):
    """
    Return PostgreSQL type to figure out what column type to create
    for an emit value.

    Args:
        py_val: Output from calling :py:func:`orjson.dumps` then
            `orjson.loads` on an object. Must be a scalar Python
            type or a non-empty list.
    """
    if isinstance(py_val, bool):
        return 'boolean'
    elif isinstance(py_val, int):
        return 'bigint'
    elif isinstance(py_val, float):
        return 'double precision'
    if isinstance(py_val, str):
        return 'text'
    elif isinstance(py_val, list):
        if len(py_val) > 0:
            inner_pg_type = get_pg_type(py_val[0])
            return  inner_pg_type + '[]'
        return 'empty list'
    return 'unsupported'


async def emit_config(conn_args, d, default):
    metadata = d.pop('metadata', None)
    if metadata is not None:
        d = {**metadata, **d}
    # TODO: These keys need to be added
    d['generation'] = None
    d['variant'] = None
    d['seed'] = None
    conn = await asyncpg.connect(**conn_args)
    await conn.execute(f'CREATE TABLE IF NOT EXISTS configuration '
        '(cell_id SERIAL PRIMARY KEY, config JSONB)')
    for index in INDEXES_TO_CREATE:
        await conn.execute(f"CREATE INDEX IF NOT EXISTS {index}_idx ON "
            f"configuration((config->>'{index}'))")
    # PostgreSQL JSONB is binary string with "1" byte prepended
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda data: b"\x01" + orjson.dumps(data,
            option=orjson.OPT_SERIALIZE_NUMPY, default=default),
        decoder=lambda data: orjson.loads(data[1:]),
        schema="pg_catalog",
        format="binary"
    )
    # Inserting the configuration should generate a unique cell ID
    # that we can use to set the year for the cell_id_time column
    # used to uniquely identify each data point in the history table
    insert_cmd = ("INSERT INTO configuration (config)"
        " VALUES ($1) RETURNING cell_id")
    # Python years cannot be year 0, so increment by 1
    return await conn.fetchval(insert_cmd, d) + 1


class PgtablesEmitter(Emitter):
    """
    Emit data to a PostgreSQL database. Creates a separate OS thread
    to handle the asynchronous insert operations.
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
            'host': config.get('host', None),
            'port': config.get('port', None),
            'user': config.get('user', 'covertlab'),
            'database': config.get('database', 'tsdb'),
            'password': config.get('password', None)
        }
        # self.executor = ThreadPoolExecutor()
        self.executor = ProcessPoolExecutor(1)
        self.field_to_placeholder = {}
        self.cell_id = None
        self.first_history_emit = True
        self.fallback_serializer = make_fallback_serializer_function()

    def emit(self, data: dict[str, Any]):
        # Config will always be first emit
        if data['table'] == 'configuration':
            self.cell_id = asyncio.run(emit_config(
                self.connection_args, data, self.fallback_serializer))
            return
        time = data['data']['time']
        for agent_id, agent_data in data['data']['agents'].items():
            agent_data['generation'] = len(agent_id)
            agent_data, data_types = flatten_and_serialize(
                agent_data, self.fallback_serializer, self.first_history_emit)
            self.first_history_emit = False
            # New columns needed when new Stores are created
            new_cols = set(agent_data) - set(self.field_to_placeholder)
            if len(new_cols) > 0:
                # If starting from a fresh DB, we need to add new cols so
                # take this opportunity to create TimescaleDB hypertable
                asyncio.run(create_hypertable(self.connection_args))
                new_col_types = {k: data_types[k] for k in new_cols}
                self.field_to_placeholder = asyncio.run(
                    add_new_fields(self.connection_args, new_col_types))
            # asyncio.run(
            #     insert_data(self.connection_args, self.cell_id,
            #     agent_data, self.field_to_placeholder, time, agent_id)
            # )
            self.executor.submit(executor_proc,
                self.connection_args, self.cell_id,
                agent_data, self.field_to_placeholder, time, agent_id)
