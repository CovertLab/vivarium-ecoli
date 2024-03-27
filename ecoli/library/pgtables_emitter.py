import asyncio
import atexit
from concurrent.futures import ThreadPoolExecutor
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


async def compress_chunk(conn_args, cell_id):
    con = await asyncpg.connect(**conn_args)
    newer_than = datetime(
        cell_id, 1, 1, tzinfo=pytz.timezone('Africa/Abidjan'))
    older_than = datetime(
        cell_id+1, 1, 1, tzinfo=pytz.timezone('Africa/Abidjan'))
    cell_chunk = await con.fetchval(
        "SELECT show_chunks('history', newer_than => $1,"
        "older_than => $2)", newer_than, older_than)
    await con.execute('SELECT compress_chunk($1)', cell_chunk)
    await con.close()


async def create_hypertable(conn_args):
    con = await asyncpg.connect(**conn_args)
    await con.execute(
        "SELECT create_hypertable('history', "
        "by_range('cell_id_time', INTERVAL '1 year'), "
        "if_not_exists => TRUE)")
    await con.execute(
        "ALTER TABLE 'history' SET (timescaledb.compress = TRUE, "
        "timescaledb.compress_orderby = 'cell_id_time ASC')")
    await con.close()


async def create_indexes(conn_args: dict[str, Any],
                         field_to_placeholder: dict[str, int]):
    """
    Create indexes for faster queries.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        field_to_placeholder: Mapping of field names to placeholder
            column names (from :py:func:`~.map_field_to_placeholder`)
    """
    con = await asyncpg.connect(**conn_args)
    for index in INDEXES_TO_CREATE:
        placeholder = field_to_placeholder['configuration'][index]
        await con.execute(
            f'CREATE INDEX IF NOT EXISTS "configuration_{placeholder}"'
            f'ON configuration ("{placeholder}")')
    await con.close()


async def add_new_fields(conn_args: dict[str, Any], table_id: str,
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
    # fullname has UNIQUE constraint and should raise a conflict for
    # duplicates. We tell PostgreSQL to skip inserting these duplicates.
    cmd_prefix = f'INSERT INTO "{table_id}_colnames" (fullname) VALUES '
    col_values = []
    for i in range(len(field_types)):
        col_values.append(f'(${i+1})')
    col_values = ", ".join(col_values)
    cmd = ''.join([cmd_prefix, col_values, ' ON CONFLICT DO NOTHING'])
    await con.execute(cmd, *field_types)
    field_to_placeholder = await map_field_to_placeholder(con, table_id)
    col_str = [f'"{field_to_placeholder[k]}" {v}'
                    for k, v in field_types.items()]
    # If any new column names are duplicates, nothing should happen.
    add_cols = ', '.join([f'ADD COLUMN IF NOT EXISTS {i}' for i in col_str])
    await con.execute(f'ALTER TABLE "{table_id}" {add_cols}')
    await con.close()
    return field_to_placeholder


async def map_field_to_placeholder(con: asyncpg.Connection, table_id: str):
    """
    Get mapping of full field names to placeholder column names.
    """
    res = await con.fetch(
        f"SELECT (fullname, seqname) FROM {table_id}_colnames")
    return dict(tuple(i)[0] for i in res)


async def initialize_tables(conn_args: dict[str, Any], table_id: str):
    """
    Create table with given name. Also creates a helper table that maps
    field names potentially longer than PostgreSQL's 63 char limit to a
    much shorter placeholder column name used in the actual table.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        table_id: Name of table to create

    Returns:
        Mapping of fields to placeholder names.
    """
    con = await asyncpg.connect(**conn_args)
    await con.execute(f'CREATE TABLE IF NOT EXISTS "{table_id}_colnames" '
        '(seqname SERIAL PRIMARY KEY, fullname TEXT UNIQUE)')
    if table_id == 'history':
        await con.execute(
            f'CREATE TABLE IF NOT EXISTS "{table_id}" (cell_id_time TIMESTAMPTZ)')
    elif table_id == 'configuration':
        await con.execute(
            f'CREATE TABLE IF NOT EXISTS "{table_id}" (cell_id SERIAL '
            'PRIMARY KEY)')
    else:
        raise NameError(f'Unrecognized table_id: {table_id}.')
    field_to_placeholder = await map_field_to_placeholder(con, table_id)
    await con.close()
    return field_to_placeholder


def serialize_with_types(d: dict[str, Any], default: Callable):
    """
    Serialize flattened dictionary with orjson and get PostgreSQL types for
    each field.

    Args:
        d: Flattened dictionary
        default: Fallback serializer function for orjson

    Returns:
        Serialized dictionary, mapping of field names to PostgreSQL types
    """
    insert_dict = {}
    col_types = {}
    for field, v in d.items():
        bin_val = orjson.dumps(v, option=orjson.OPT_SERIALIZE_NUMPY,
            default=default)
        py_val = orjson.loads(bin_val)
        pg_type = get_pg_type(py_val)
        # PostgreSQL JSONB is just binary string with "1" byte prepended
        insert_dict[field] = b"\x01" + bin_val if pg_type == 'jsonb' else py_val
        col_types[field] = pg_type
    return insert_dict, col_types


async def insert_data(conn_args: dict[str, Any], cell_id: int,
                      table_id: str, insert_dict: dict[str, Any],
                      field_to_placeholder: dict[str, int]):
    """
    Called by :py:func:`~.executor_proc` to insert data.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        cell_id: Unique identifier for data emitted by one simulated cell
        inserts: Tuples ``(table_id, values, columns)``

    Returns:
        Cell ID that was either just generated (first insert into
        ``configuration`` table) or propagated through simulation
    """
    conn = await asyncpg.connect(**conn_args)
    # We've already serialized JSONB using orjson
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda data: data,
        decoder=lambda data: orjson.loads(data[1:]),
        schema="pg_catalog",
        format="binary"
    )
    columns = [str(field_to_placeholder[k]) for k in insert_dict]
    if cell_id is None and table_id == 'configuration':
        # Inserting the configuration row should generate a unique cell ID
        quoted_columns = ', '.join((f'"{c}"' for c in columns))
        cmd_params = ', '.join((f'${i+1}' for i in range(len(columns))))
        insert_cmd = (f"INSERT INTO {table_id} ({quoted_columns})"
            f" VALUES ({cmd_params}) RETURNING cell_id")
        cell_id = await conn.fetchval(insert_cmd, *insert_dict.values())
    else:
        cell_id_time = datetime(cell_id, 1, 1) + timedelta(
            seconds=insert_dict['time'])
        records = [(cell_id_time, *insert_dict.values())]
        columns = ['cell_id_time', *columns]
        await conn.copy_records_to_table(
            table_id, records=records, columns=columns)
    await conn.close()
    return cell_id


def executor_proc(conn_args: dict[str, Any], cell_id: int,
                  table_id: str, insert_dict: dict[str, Any],
                  field_to_placeholder: dict[str, int]):
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
        conn_args, cell_id, table_id, insert_dict, field_to_placeholder))


_FLAG_FIRST = object()

def flatten_dict(d: dict):
    """
    Flatten nested dictionary down to key-value pairs where each key
    concatenates all the keys needed to reach the corresponding value
    in the input into comma-separated string. Prunes empty dicts.
    """
    results = []
    def visit(subdict, results, partialKey):
        for k,v in subdict.items():
            newKey = k if partialKey==_FLAG_FIRST else f'{partialKey}, {k}'
            if isinstance(v, Mapping):
                visit(v, results, newKey)
            else:
                results.append((newKey, v))
    visit(d, results, _FLAG_FIRST)
    return results


def get_pg_type(py_val: Any):
    """
    Return PostgreSQL type to figure out what column type to create
    for an emit value.

    Args:
        py_val: Output from calling :py:func:`orjson.dumps` then
            `orjson.loads` on an object. Everything that is not
            a scalar built-in type is emitted to a JSONB column.
    """
    if isinstance(py_val, bool):
        return 'boolean'
    elif isinstance(py_val, int):
        return 'bigint'
    elif isinstance(py_val, float):
        return 'numeric'
    if isinstance(py_val, str):
        return 'text'
    else:
        return 'jsonb'


def reorganize_data(d: dict, experiment_id: str):
    """
    Put agent data on top level and add metadata keys ``agent_id``,
    ``generation``, ``time``, and ``experiment_id`` for querying.

    TODO: Figure out how to handle colony emit data.

    Returns:
        List of dictionaries. If ``d`` contains multiple agents,
        each agent gets its own dictionary.
    """
    new_dicts = []
    if 'agents' in d:
        for agent_data in d['agents'].values():
            agent_data_copy = dict(agent_data)
            agent_data_copy['time'] = d['time']
            new_dicts.append(agent_data_copy)
    else:
        metadata = d.pop('metadata', None)
        if metadata is not None:
            d = {**metadata, **d}
        d['experiment_id'] = experiment_id
        d['generation'] = len(d['agent_id'])
        # TODO: These keys need to be added
        d['variant'] = None
        d['seed'] = None
        new_dicts.append(d)
    return new_dicts


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
            'user': config.get('user', 'seancheah'),
            'database': config.get('database', 'tsdb'),
            'password': config.get('password', None)
        }
        self.executor = ThreadPoolExecutor()
        self.field_to_placeholder = {}
        self.hypertable_created = False
        self.cell_id = None
        self.fallback_serializer = make_fallback_serializer_function()
        atexit.register(self._compress_chunk)

    def _compress_chunk(self):
        asyncio.run(compress_chunk(self.connection_args, self.cell_id))

    def emit(self, data: dict[str, Any]):
        """Adds data to queue to be handled by :py:func:`~.main_process`"""
        table_id = data['table']
        # First time this is run, create tables if needed and cache
        # mapping of field names to placeholder names
        if not self.field_to_placeholder.get(table_id, False):
            self.field_to_placeholder[table_id] = asyncio.run(
                initialize_tables(self.connection_args, table_id))
        emit_dicts = reorganize_data(data['data'], self.experiment_id)
        for d in emit_dicts:
            flat_dict = dict(flatten_dict(d))
            insert_dict, col_types = serialize_with_types(
                flat_dict, self.fallback_serializer)
            # New columns needed when new Stores are created
            new_cols = set(insert_dict) - set(self.field_to_placeholder[table_id])
            if len(new_cols) > 0:
                new_cols = {k: col_types[k] for k in new_cols}
                self.field_to_placeholder[table_id] = asyncio.run(
                    add_new_fields(self.connection_args, table_id, new_cols))
                # If starting from a fresh DB, we need to add new cols so
                # take this opportunity to create TimescaleDB hypertable
                # from history table and indexes for configuration table
                if table_id == 'history' and not self.hypertable_created:
                    asyncio.run(create_hypertable(self.connection_args))
                    asyncio.run(create_indexes(
                        self.connection_args, self.field_to_placeholder))
                    self.hypertable_created = True
            cell_id_future = self.executor.submit(executor_proc,
                self.connection_args, self.cell_id, table_id,
                insert_dict, self.field_to_placeholder[table_id])
            if self.cell_id is None:
                self.cell_id = cell_id_future.result()