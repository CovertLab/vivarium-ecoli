import asyncio
from typing import Any, Callable, Iterable, Mapping
from concurrent.futures import ProcessPoolExecutor

import asyncpg
import orjson
import uvloop
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


INDEXES_TO_CREATE = {
    'history': ['experiment_id', 'variant', 'seed', 'generation',
                'agent_id', 'time'],
    'configuration': ['experiment_id']
}
"""Default indexes to create for each table."""


async def create_unique_constraint(conn_args: dict[str, Any], table_id: str,
                                   field_to_placeholder: dict[str, int]):
    """
    Add unique constraint on the columns we create indexes for.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        table_id: Name of table to create unique constraint for
        field_to_placeholder: Mapping of field names to placeholder
            column names (from :py:func:`~.map_field_to_placeholder`)
    """
    con = await asyncpg.connect(**conn_args)
    constraint_exists = await con.fetchval("SELECT constraint_name FROM "
        "information_schema.constraint_column_usage WHERE "
        f"constraint_schema='public' AND constraint_name='{table_id}_unique'")
    if constraint_exists is None:
        constraint_cols = ', '.join([f'"{str(field_to_placeholder[table_id][k])}"'
                                    for k in INDEXES_TO_CREATE[table_id]])
        await con.execute(f'ALTER TABLE {table_id} ADD CONSTRAINT {table_id}_unique '
                    f'UNIQUE({constraint_cols})')
    await con.close()


async def create_indexes(conn_args: dict[str, Any], table_id: str,
                         field_to_placeholder: dict[str, int]):
    """
    Create indexes for faster queries.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        table_id: Name of table to create indexes for
        field_to_placeholder: Mapping of field names to placeholder
            column names (from :py:func:`~.map_field_to_placeholder`)
    """
    con = await asyncpg.connect(**conn_args)
    for index in INDEXES_TO_CREATE[table_id]:
        placeholder = field_to_placeholder[table_id][index]
        await con.execute(
            f'CREATE INDEX IF NOT EXISTS "{table_id}_{placeholder}"'
            f'ON "{table_id}" ("{placeholder}")')
    await con.close()


async def add_new_fields(conn_args: dict[str, Any], table_id: str,
                         field_types: dict[str, str]) -> dict[str, str]:
    """
    Create new fields in a table.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
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
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        table_id: Name of table to create

    Returns:
        Mapping of fields to placeholder names.
    """
    con = await asyncpg.connect(**conn_args)
    await con.execute(f'CREATE TABLE IF NOT EXISTS "{table_id}_colnames" '
        '(seqname SERIAL PRIMARY KEY, fullname TEXT UNIQUE)')
    await con.execute(f'CREATE TABLE IF NOT EXISTS "{table_id}" ()')
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
        insert_dict[field] = bin_val if pg_type == 'bytea' else py_val
        col_types[field] = pg_type
    return insert_dict, col_types


async def insert_data(conn_args: dict[str, Any],
                      inserts: tuple[str, Iterable[Any], list[str]]):
    """
    Called by :py:func:`~.executor_proc` to insert data.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        inserts: Tuples ``(table_id, records, columns)``
            for :py:meth:`asyncpg.Connection.copy_records_to_table`
    """
    con = await asyncpg.connect(**conn_args)
    await con.copy_records_to_table(
        inserts[0], records=inserts[1], columns=inserts[2])
    await con.close()


def executor_proc(conn_args: dict[str, Any],
                  inserts: tuple[str, Iterable[Any], list[str]]):
    """
    Called by ProcessPoolExecutor to submit inserts.

    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        inserts: Tuples ``(table_id, records, columns)``
            for :py:meth:`asyncpg.Connection.copy_records_to_table`
    """
    asyncio.run(insert_data(conn_args, inserts))


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
            `orjson.loads` on an object. Must be built-in Python type.
    """
    if isinstance(py_val, bool):
        return 'boolean'
    elif isinstance(py_val, int):
        return 'bigint'
    elif isinstance(py_val, float):
        return 'numeric'
    if isinstance(py_val, str):
        return 'text'
    elif isinstance(py_val, list):
        if len(py_val) > 0:
            inner_pg_type = get_pg_type(py_val[0])
            if inner_pg_type != 'bytea':
                return inner_pg_type + '[]'
        return 'text'
    else:
        return 'bytea'


def reorganize_data(d: dict, experiment_id: str):
    """
    Put agent data on top level and add metadata keys ``agent_id``,
    ``generation``, ``time``, and ``experiment_id`` for querying.

    Returns:
        List of dictionaries. If ``d`` contains multiple agents,
        each agent gets its own dictionary.
    """
    new_dicts = []
    if 'agents' in d:
        for agent_id, agent_data in d['agents'].items():
            agent_data_copy = dict(agent_data)
            agent_data_copy['agent_id'] = agent_id
            agent_data_copy['generation'] = len(agent_id)
            agent_data_copy['time'] = d['time']
            agent_data_copy['experiment_id'] = experiment_id
            # TODO: These keys need to be added
            agent_data_copy['variant'] = None
            agent_data_copy['seed'] = None
            new_dicts.append(agent_data_copy)
    else:
        d['experiment_id'] = experiment_id
        # TODO: Some values too big
        d.pop('metadata', None)
        # TODO: These keys need to be added
        d['variant'] = None
        d['seed'] = None
        new_dicts.append(d)
    return new_dicts


class PgtablesEmitter(Emitter):
    """
    Emit data to a PostgreSQL database. Creates a separate OS process
    to handle the expensive insert operations.
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
            'user': config.get('user', 'postgres'),
            'database': config.get('database', 'postgres'),
            'password': config.get('password', None)
        }
        self.executor = ProcessPoolExecutor(2)
        self.field_to_placeholder = {}
        self.indexes_created = {}
        self.fallback_serializer = make_fallback_serializer_function()

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
                # take this opportunity to create indexes and constraints as well
                if not self.indexes_created.get(table_id):
                    asyncio.run(create_indexes(
                        self.connection_args, table_id, self.field_to_placeholder))
                    asyncio.run(
                        create_unique_constraint(
                            self.connection_args, table_id, self.field_to_placeholder))
                    self.indexes_created[table_id] = True
            # Need placeholder names in order of field names to insert
            colnames = [str(self.field_to_placeholder[table_id][k])
                        for k in insert_dict]
            self.executor.submit(executor_proc, self.connection_args,
                (table_id, (list(insert_dict.values()),), colnames))
