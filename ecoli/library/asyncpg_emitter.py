import threading
from typing import Any, Mapping, Optional
import itertools
from multiprocessing import Process, Queue

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


async def async_copy_records(conn_args, table_name, records, columns=None):
    con = await asyncpg.connect(**conn_args)
    await con.copy_records_to_table(table_name, records=records, columns=columns)


def run_queries(records, colnames, table_id, conn_args):
    filled_records = []
    for record in records:
        filled_records.append([record.get(v, None) for v in colnames.values()])
    asyncio.run(async_copy_records(conn_args, table_id, filled_records, list(colnames.keys())))


def main_process(q, conn_args):
    all_colnames = {}
    collected_records = []
    table_id = None
    while True:
        query = q.get()
        if query == 'Shutting down...':
            run_queries(collected_records, all_colnames, table_id, conn_args)
            break
        if table_id is None:
            table_id = query[0]
        elif query[0] != table_id:
            run_queries(collected_records, all_colnames, table_id, conn_args)
            collected_records = []
            all_colnames = {}
            table_id = query[0]
        all_colnames.update(query[2])
        collected_records.append(query[1])
        if len(collected_records) > 5:
            run_queries(collected_records, all_colnames, table_id, conn_args)
            collected_records = []
            all_colnames = {}


_FLAG_FIRST = object()

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


def get_pg_type(py_val, inc_list=False):
    if isinstance(py_val, bool):
        return 'boolean'
    elif isinstance(py_val, int):
        return 'bigint'
    elif isinstance(py_val, float):
        return 'numeric'
    if isinstance(py_val, str):
        return 'text'
    elif inc_list and isinstance(py_val, list):
        if len(py_val) > 0:
            inner_pg_type = get_pg_type(py_val[0], inc_list)
            if inner_pg_type != 'bytea':
                return  inner_pg_type + '[]'
        return 'text'
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
        self.inc_lists = config.get('inc_lists', False)
        self._tables_created = {}
        self.fallback_serializer = make_fallback_serializer_function()
        self.async_event_loop = asyncio.new_event_loop()
        threading.Thread(target=self._insert_query, daemon=True).start()

    def _insert_query(self):
        self.async_event_loop.run_forever()

    def _get_curr_colnames(self, table_id):
        colnames_table = table_id + '_colnames'
        get_curr_colnames = f'SELECT fullname, seqname FROM "{colnames_table}"'
        curr_colnames = asyncio.run(async_query(
            self.connection_args, get_curr_colnames, (), True))
        # There is a chance different simulations try to add the same
        # full name at the same time, causing different seq values to
        # correspond to the same full name. We handle that by keeping
        # only the smallest seq value column (oldest).
        full_to_seq = {}
        seq_names_to_drop = []
        for pairing in curr_colnames:
            fullname = pairing['fullname']
            seqname = pairing['seqname']
            curr_seq_val = full_to_seq.setdefault(fullname, seqname)
            if seqname > curr_seq_val:
                seq_names_to_drop.append(seqname)
            elif seqname < curr_seq_val:
                seq_names_to_drop.append(curr_seq_val)
                full_to_seq[fullname] = seqname
        for drop_seq in seq_names_to_drop:
            drop_col_cmd = (f'ALTER TABLE "{table_id}" '
                f'DROP COLUMN IF EXISTS "{drop_seq}"')
            asyncio.run(async_query(
                self.connection_args, drop_col_cmd))
            del_colnames_cmd = (f'DELETE FROM "{colnames_table}" '
                'WHERE seqname = $1')
            asyncio.run(async_query(
                self.connection_args, del_colnames_cmd, (drop_seq,)))
        # As a sanity check, ensure that seq colnames in the main data table
        # match with those stored in the colnames table
        get_curr_seq = ("SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1")
        existing_uuids = asyncio.run(async_query(
            self.connection_args, get_curr_seq, (table_id,), True))
        existing_uuids = [int(i[0]) for i in existing_uuids]
        assert set(full_to_seq.values()) == set(existing_uuids)
        return full_to_seq
    
    def _add_new_keys(self, new_keys, table_id, col_types):
        new_keys = list(new_keys)
        new_seq = self._get_new_colnames(len(new_keys))
        fields = [f'"{s}" {col_types[k]}' for s, k in zip(new_seq, new_keys)]
        fields = ', '.join([f'ADD COLUMN {i}' for i in fields])
        alter_table_cmd = f'ALTER TABLE "{table_id}" {fields}'
        asyncio.run(async_query(
            self.connection_args, alter_table_cmd, (), False))

        colnames_table = table_id + '_colnames'
        asyncio.run(async_copy_records(
            self.connection_args, colnames_table, zip(new_seq, new_keys)))
        self._tables_created[table_id] = self._get_curr_colnames(table_id)
    
    def _get_new_colnames(self, n):
        # Get column names to create data table with
        new_seq = []
        for _ in range(n):
            next_val = asyncio.run(async_query(
                self.connection_args, "SELECT nextval('seqnames')", (), True))
            new_seq.append(next_val[0][0])
        return new_seq
    
    def _create_indexes(self, table_id, fullnames):
        for fullname in fullnames:
            col_seq = self._tables_created[table_id][fullname]
            create_index_cmd = (
                f'CREATE INDEX IF NOT EXISTS "{table_id}_{col_seq}"'
                f'ON "{table_id}" ("{col_seq}")')
            asyncio.run(async_query(self.connection_args, create_index_cmd))
    
    def _create_table(self, table_id, col_types):
        # Create sequence table to get new column names from
        asyncio.run(async_query(self.connection_args,
            "CREATE SEQUENCE IF NOT EXISTS seqnames"))

        new_seq = self._get_new_colnames(len(col_types))
        fields = [f'"{s}" {c}' for s, c in zip(new_seq, col_types.values())]
        fields = (', ').join(fields)
        create_table_cmd = f'CREATE TABLE IF NOT EXISTS "{table_id}" ({fields})'
        asyncio.run(async_query(self.connection_args, create_table_cmd))
        
        # PostgreSQL has 63 character limit for column names. Use sequential
        # column names in the main table and create a separate table
        # mapping full column names to sequential column names
        colnames_table = table_id + '_colnames'
        create_table_prefix = (f'CREATE TABLE IF NOT EXISTS "{colnames_table}" '
            '("seqname", "fullname") AS SELECT * FROM ( VALUES ')
        col_name_combined = list(itertools.chain.from_iterable(
            zip(new_seq, col_types)))
        col_values = []
        for i in range(len(col_types)):
            col_values.append(f'(${2*i+1}::int, ${2*i+2})')
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
                pg_type = get_pg_type(py_val, self.inc_lists)  
                if pg_type == 'bytea':
                    new_dict[k] = bin_val
                else:
                    new_dict[k] = py_val
                col_types[k] = pg_type 
            if table_id not in self._tables_created:
                self._create_table(table_id, col_types)
                if table_id == 'configuration':
                    fullnames = ['experiment_id']
                elif table_id == 'history':
                    # TODO: Add seed and variant information
                    fullnames = ['experiment_id', 'time', 'generation']
                self._create_indexes(table_id, fullnames)
            self.write_emit(new_dict, table_id, col_types)

    def write_emit(self, flat_data: dict[str, Any], table_id: str, col_types) -> None:
        """
        Write data as new row, creating tables, columns, and indices as needed.
        """
        # Add new columns if necessary (e.g. if new Stores were created)
        new_keys = set(flat_data) - set(self._tables_created[table_id])
        if len(new_keys) > 0:
            self._add_new_keys(new_keys, table_id, col_types)

        # Retrieve column indices to insert
        col_names = [str(self._tables_created[table_id][full_name])
                     for full_name in flat_data]
        asyncio.run_coroutine_threadsafe(
            async_copy_records(self.connection_args, table_id,
                               (flat_data.values(),), col_names),
            self.async_event_loop)

    def get_data(self, query: Optional[list] = None) -> None:
        return None


class AsyncpgMPEmitter(AsyncpgEmitter):
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
        self.experiment_id = config.get('experiment_id')
        # Collect connection arguments
        self.connection_args = {
            'host': config.get('host', None),
            'port': config.get('port', None),
            'user': config.get('user', 'postgres'),
            'database': config.get('database', 'postgres'),
            'password': config.get('password', None)
        }
        self.inc_lists = config.get('inc_lists', False)
        self._tables_created = {}
        self.fallback_serializer = make_fallback_serializer_function()
        self.main_queue = Queue()
        self.db_process = Process(target=main_process,
            args=(self.main_queue, self.connection_args))
        self.db_process.start()
        self.inc_lists = True

    def __del__(self):
        self.main_queue.put('Shutting down...')
        self.db_process.join()

    def write_emit(self, flat_data: dict[str, Any], table_id: str, col_types) -> None:
        """
        Write data as new row, creating tables, columns, and indices as needed.
        """
        # Add new columns if necessary (e.g. if new Stores were created)
        new_keys = set(flat_data) - set(self._tables_created[table_id])
        if len(new_keys) > 0:
            self.add_new_keys(new_keys, table_id, col_types)

        seq_to_full = {str(v): k for k, v in self._tables_created[table_id].items()}
        self.main_queue.put((table_id, flat_data, seq_to_full))
