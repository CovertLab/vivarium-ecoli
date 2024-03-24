import threading
from typing import Any, Mapping, Optional
import uuid
import itertools

import orjson
import asyncio
import asyncpg
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

async def async_query(conn_args, query, query_args=(), return_val=False):
    con = await asyncpg.connect(**conn_args)
    if return_val:
        return await con.fetch(query, *query_args)
    else:
        await con.execute(query, *query_args)

async def async_copy_records(conn_args, table_name, records):
    con = await asyncpg.connect(**conn_args)
    await con.copy_records_to_table(table_name, records=records)

_FLAG_FIRST = object()

def make_fields(col_types: list[str], field_names: list[str]):
    """Combine PostgreSQL column name and type
    in the format expected by CREATE/ALTER TABLE."""
    fields = []
    for col_type, field_name in zip(col_types, field_names):
        fields.append(f'"{field_name}" {col_type}')
    return fields

def flatten_dict(d):
    """
    Flatten nested dictionary down to key-value pairs where each key
    concatenates all the keys needed to reach the corresponding value
    in the input (separated by double underscores). Prune empty lists,
    empty dictionaries, and None (not supported by psycopg).
    """
    results = []
    def visit(subdict, results, partialKey):
        for k,v in subdict.items():
            newKey = k if partialKey==_FLAG_FIRST else f'{partialKey}__{k}'
            if isinstance(v, Mapping):
                visit(v, results, newKey)
            else:
                results.append((newKey, v))
    visit(d, results, _FLAG_FIRST)
    return results


def get_pg_type(py_val):
    if isinstance(py_val, bool):
        return 'boolean'
    elif isinstance(py_val, int):
        return 'bigint'
    elif isinstance(py_val, float):
        return 'numeric'
    if isinstance(py_val, str):
        return 'text'
    # elif isinstance(py_val, list):
    #     if len(py_val) > 0:
    #         inner_pg_type = get_pg_type(py_val[0])
    #         if inner_pg_type != 'bytea':
    #             return  inner_pg_type + '[]'
    #     py_val.append(None)
    #     return 'empty list'
    else:
        return 'bytea'



def reorganize_data(d, experiment_id):
    """
    Reorganize simulation outputs and add metadata for querying. Further
    prunes empty lists and None values that appears after serialization.
    """
    new_dicts = []
    if 'agents' in d:
        for agent_id, agent_data in d['agents'].items():
            agent_data_copy = dict(agent_data)
            agent_data_copy['agent_id'] = agent_id
            agent_data_copy['generation'] = len(agent_id)
            agent_data_copy['time'] = d['time']
            agent_data_copy['experiment_id'] = experiment_id
            new_dicts.append(agent_data_copy)
    else:
        d['experiment_id'] = experiment_id
        d.pop('metadata', None)
        new_dicts.append(d)
    return new_dicts


class AsyncpgEmitter(Emitter):
    """
    Emit data to a PostgreSQL database

    Example:

    >>> config = {
    ...     'host': 'localhost',
    ...     'port': 5432,
    ...     'database': 'DB_NAME',
    ... }
    >>> # The line below works only if you have to have 5432 open locally
    >>> # emitter = AsyncpgEmitter(config)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """config may have 'host' and 'database' items."""
        super().__init__(config)
        self.experiment_id = config.get('experiment_id')
        # Collect connection arguments
        self.connection_args = {
            'host': config.get('host', None),
            'port': config.get('port', None),
            'user': config.get('user', 'postgres'),
            'database': config.get('database', 'postgres'),
            'password': config.get('password', None)
        }
        self._tables_created = {}
        self.fallback_serializer = make_fallback_serializer_function()
        self.async_event_loop = asyncio.new_event_loop()
        self.submit_thread = threading.Thread(
            target=self._insert_query, daemon=True
        )
        self.submit_thread.start()

    def _insert_query(self):
        self.async_event_loop.run_forever()

    def _get_curr_colnames(self, table_id):
        colnames_table = table_id + '_colnames'
        get_curr_colnames = f'SELECT fullname, uuidname FROM "{colnames_table}"'
        full_to_uuid = asyncio.run(async_query(
            self.connection_args, get_curr_colnames, (), True))
        full_to_uuid = dict([tuple(i) for i in full_to_uuid])
        get_curr_uuids = ("SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1")
        existing_uuids = asyncio.run(async_query(
            self.connection_args, get_curr_uuids, (table_id,), True))
        existing_uuids = [tuple(i)[0] for i in existing_uuids]
        assert set(full_to_uuid.values()) == set(existing_uuids)
        return full_to_uuid
    
    def _create_table(self, table_id, col_types):
        field_names = [str(uuid.uuid4()) for _ in range(len(col_types))]
        fields = make_fields(list(col_types.values()), field_names)
        fields = (', ').join(fields)
        create_table_cmd = f'CREATE TABLE IF NOT EXISTS "{table_id}" ({fields})'
        asyncio.run(async_query(
            self.connection_args, create_table_cmd, (), False))
        
        # PostgreSQL has 63 character limit for column names. Use number
        # column names in the main table and create a separate table
        # with two columns: full column name and number column name
        colnames_table = table_id + '_colnames'
        create_table_prefix = (f'CREATE TABLE IF NOT EXISTS "{colnames_table}" '
            '("uuidname", "fullname") AS SELECT * FROM ( VALUES ')
        col_name_combined = list(itertools.chain.from_iterable(
            zip(field_names, col_types)))
        col_values = []
        for i in range(len(col_types)):
            col_values.append(f'(${2*i+1}, ${2*i+2})')
        col_values = ", ".join(col_values)
        create_table_cmd = "".join([create_table_prefix, col_values, ' )'])
        asyncio.run(async_query(
            self.connection_args, create_table_cmd, col_name_combined, False))

        self._tables_created[table_id] = self._get_curr_colnames(table_id)
        
    def emit(self, data: dict[str, Any]) -> None:
        table_id = data['table']
        emit_dicts = reorganize_data(data['data'], self.experiment_id)
        for d in emit_dicts:
            flat_dict = dict(flatten_dict(d))
            new_dict = {}
            col_types = {}
            for k, v in flat_dict.items():
                bin_val = orjson.dumps(v, option=orjson.OPT_SERIALIZE_NUMPY,
                    default=self.fallback_serializer)
                py_val = orjson.loads(bin_val)
                pg_type = get_pg_type(py_val)  
                if pg_type == 'bytea':
                    new_dict[k] = bin_val
                else:
                    new_dict[k] = py_val
                col_types[k] = get_pg_type(py_val) 
            if table_id not in self._tables_created:
                self._create_table(table_id, col_types)
            self.write_emit(new_dict, table_id, col_types)

    def write_emit(self, flat_data: dict[str, Any], table_id: str, col_types) -> None:
        """
        Write data as new row, creating tables, columns, and indices as needed.
        """
        # Add new columns if necessary (e.g. if new Stores were created)
        new_keys = set(flat_data) - set(self._tables_created[table_id])
        if len(new_keys) > 0:
            new_keys = list(new_keys)
            keys_to_add = {}
            for key in new_keys:
                new_uuid = str(uuid.uuid4())
                self._tables_created[table_id][key] = new_uuid
                keys_to_add[new_uuid] = key
            fields = make_fields([col_types[k] for k in new_keys], list(keys_to_add.keys()))
            fields = ', '.join([f'ADD COLUMN {i}' for i in fields])
            alter_table_cmd = f'ALTER TABLE "{table_id}" {fields}'
            asyncio.run(async_query(
                self.connection_args, alter_table_cmd, (), False))

            colnames_table = table_id + '_colnames'
            asyncio.run(async_copy_records(
                self.connection_args, colnames_table, keys_to_add.items()))

        # Retrieve column indices to insert
        col_names = ', '. join([
            f'"{self._tables_created[table_id][full_name]}"'
            for full_name in flat_data.keys()])
        data_params = ', '.join([f'${i+1}' for i in range(len(flat_data))])
        insert_cmd = f'INSERT INTO "{table_id}" ({col_names}) VALUES ({data_params})'
        asyncio.run_coroutine_threadsafe(
            async_query(self.connection_args, insert_cmd, list(flat_data.values())),
            self.async_event_loop)

    def get_data(self, query: Optional[list] = None) -> None:
        return None
