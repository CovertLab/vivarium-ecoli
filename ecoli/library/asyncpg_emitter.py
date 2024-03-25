import asyncio
import atexit
import itertools
from multiprocessing import Process, Queue
from threading import Thread
from typing import Any, Iterable, Mapping, Optional

import asyncpg
import orjson
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function


async def async_query(conn_args: dict[str, str], query: str,
                      query_args: Iterable=(), return_val: bool=False):
    """
    Use with :py:func:`asyncio.run` for one-shot queries.
    
    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        query: Command to execute
        query_args: Iterable containing sequences of arguments
        return_val: Use :py:meth:`asyncpg.Connection.fetch` if true
            and :py:meth:`asyncpg.Connection.execute` if false
    """
    con = await asyncpg.connect(**conn_args)
    if return_val:
        return await con.fetch(query, *query_args)
    else:
        await con.execute(query, *query_args)


async def async_copy_records(conn_args: dict[str, str], table_name: str,
                             records: Iterable, columns: Iterable=None):
    """
    Use with :py:func:`asyncio.run` for one-shot COPY command.
    
    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.connect`
        table_name: See :py:meth:`asyncpg.Connection.copy_records_to_table`
        records: See :py:meth:`asyncpg.Connection.copy_records_to_table`
        columns: See :py:meth:`asyncpg.Connection.copy_records_to_table`
    """
    con = await asyncpg.connect(**conn_args)
    await con.copy_records_to_table(table_name, records=records, columns=columns)


async def async_queue_listener(conn_args: dict[str, str], queue: asyncio.Queue):
    """
    Coroutine to call :py:meth:`asyncpg.Connection.copy_records_to_table`
    with arguments pulled off input queue. Maintains a :py:class:`asyncpg.Pool`.
    Completes when the string ``'Shutting down...'`` is pulled from queue.
    
    Args:
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        queue: Queue containing tuples ``(table_id, records, columns)``
            for :py:meth:`asyncpg.Connection.copy_records_to_table`
    """
    async with asyncpg.create_pool(**conn_args) as pool:
        while True:
            query = await queue.get()
            if query == 'Shutting down...':
                break
            async with pool.acquire() as con:
                await con.copy_records_to_table(
                    query[0], records=query[1], columns=query[2])

def async_thread(loop: asyncio.BaseEventLoop, conn_args: dict[str, str],
                 cmd_queue: asyncio.Queue):
    """
    Use as target for :py:class:`threading.Thread` to start event loop
    for :py:func:`~.async_queue_listener` in separate thread.

    Args:
        loop: Event loop to run in
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
        cmd_queue: Queue containing tuples ``(table_id, records, columns)``
            for :py:meth:`asyncpg.Connection.copy_records_to_table`
    """
    loop.run_until_complete(async_queue_listener(conn_args, cmd_queue))


def main_process(q: Queue, conn_args: dict[str, str]):
    """
    Use as target for :py:class:`multiprocessing.Process` to create a new
    OS process to handle expensive database insert commands. Completes
    when the string ``'Shutting down...'`` is pulled from queue.

    Args:
        q: Queue containing tuples ``(table_id, records, columns)``
            for :py:meth:`asyncpg.Connection.copy_records_to_table`
        conn_args: Keyword arguments for :py:func:`asyncpg.create_pool`
    """
    asyncio_queue = asyncio.Queue()
    loop = asyncio.new_event_loop()
    t = Thread(target=async_thread, args=(loop, conn_args, asyncio_queue))
    try:
        t.start()
        while True:
            query = q.get()
            if query == 'Shutting down...':
                break
            loop.call_soon_threadsafe(asyncio_queue.put_nowait, query)
            if not t.is_alive():
                break
    finally:
        loop.call_soon_threadsafe(asyncio_queue.put_nowait, 'Shutting down...')
        t.join()


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


def get_pg_type(py_val: Any, inc_list: bool=False):
    """
    Return PostgreSQL type. Used to figure out what column type to create
    for an emit value.

    Args:
        py_val: Output for calling :py:func:`orjson.dumps` then `orjson.loads`
            on a Python object. Should be a built-in Python type.
        inc_list: If True, try to figure out element type for ``list`` inputs.
            Otherwise, insert lists as bytes from :py:func:`orjson.dumps`.
    """
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
            new_dicts.append(agent_data_copy)
    else:
        d['experiment_id'] = experiment_id
        # TODO: Some values too big
        d.pop('metadata', None)
        new_dicts.append(d)
    return new_dicts


class AsyncpgEmitter(Emitter):
    """
    Emit data to a PostgreSQL database by serializing lists as bytes.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Pull connection arguments from ``config`` and start separate
        thread for data inserts.
        
        Args:
            config: Must include ``experiment_id`` key. Can include keys for
                ``host``, ``port``, ``user``, ``database``, and ``password``
                to use as keyword arguments for :py:func:`asyncpg.connect`. 
        """
        super().__init__(config)
        self.experiment_id = config.get('experiment_id')
        self.inc_lists = config.get('inc_lists', False)
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
        Thread(target=lambda loop: loop.run_forever(),
            args=(self.async_event_loop,), daemon=True).start()

    def _get_curr_colnames(self, table_id: str):
        """
        Retrieves the current mapping of full names to placeholder
        names for a table. At the same time, it removes any duplicate
        full names (e.g. from two simulations trying to add the same
        full name at the exact same time) by keeping only the smallest
        placeholder name, dropping the other columns from the main
        table and removing their rows from ``{table_id}_colnames``.

        See :py:meth:`~.AsyncpgEmitter._create_tables`.

        Returns:
            Dictionary of full names to placeholder names for ``table_id``
        """
        colnames_table = table_id + '_colnames'
        get_curr_colnames = f'SELECT fullname, seqname FROM "{colnames_table}"'
        curr_colnames = asyncio.run(async_query(
            self.connection_args, get_curr_colnames, (), True))
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
        existing_seq = asyncio.run(async_query(
            self.connection_args, get_curr_seq, (table_id,), True))
        existing_seq = [int(i[0]) for i in existing_seq]
        assert set(full_to_seq.values()) == set(existing_seq)
        return full_to_seq
    
    def _add_new_keys(self, new_keys: Iterable[str], table_id: str,
                      col_types: dict[str, str]):
        """
        Add new column names to table.

        Args:
            new_keys: Name of each new column to add
            table_id: Table to add new columns to
            col_types: Mapping of new column names to PostgreSQL types
                (from :py:func:`~.get_pg_type`)
        """
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
    
    def _get_new_colnames(self, n: int):
        """
        Generate ``n`` new placeholder column names using PostgreSQL SEQUENCE.
        See :py:meth:`~.AsyncpgEmitter._create_tables`.
        """
        new_seq = []
        for _ in range(n):
            next_val = asyncio.run(async_query(
                self.connection_args, "SELECT nextval('seqnames')", (), True))
            new_seq.append(next_val[0][0])
        return new_seq
    
    def _create_indexes(self, table_id: str, fullnames: Iterable[str]):
        """
        Create PostgreSQL indexes for selected columns.

        Args:
            table: Table to create indexes for.
            fullnames: Column names to create indexes for.
        """
        for fullname in fullnames:
            col_seq = self._tables_created[table_id][fullname]
            create_index_cmd = (
                f'CREATE INDEX IF NOT EXISTS "{table_id}_{col_seq}"'
                f'ON "{table_id}" ("{col_seq}")')
            asyncio.run(async_query(self.connection_args, create_index_cmd))
    
    def _create_tables(self, table_id: str, col_types: dict[str, str]):
        """
        Create fresh table with user-provided column types, if
        table with name does not already exist.

        To get around PostgreSQL's 63-character limit on column names,
        we create a PostgreSQL SEQUENCE to pull numbers to use as
        placeholder column names for each new column that we would
        like to insert. To map these placeholder column names back
        to full string column names, we create a table with the name
        ``{table_id}_colnames`` that has a ``seqname`` column of
        placeholder names and a ``fullname`` column of full names.

        Args:
            table_id: Name to table to create.
            col_types: Mapping of column names to PostgreSQL types
                (from :py:func:`~.get_pg_type`)
        """
        asyncio.run(async_query(self.connection_args,
            "CREATE SEQUENCE IF NOT EXISTS seqnames"))

        new_seq = self._get_new_colnames(len(col_types))
        fields = [f'"{s}" {c}' for s, c in zip(new_seq, col_types.values())]
        fields = (', ').join(fields)
        create_table_cmd = f'CREATE TABLE IF NOT EXISTS "{table_id}" ({fields})'
        asyncio.run(async_query(self.connection_args, create_table_cmd))

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

        # Cache column names for fast inserts and easy first check to see
        # if new columns might need to be added
        self._tables_created[table_id] = self._get_curr_colnames(table_id)
        
    def emit(self, data: dict[str, Any]):
        """
        Reorganize (:py:func:`~.reorganize_data`), flatten
        (:py:func:`~.flatten_dict`), and serialize data (``orjson`` and 
        :py:func:`~.get_pg_type`). Create tables/indexes as needed.
        Write data to database.
        """
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
                self._create_tables(table_id, col_types)
                if table_id == 'configuration':
                    fullnames = ['experiment_id']
                elif table_id == 'history':
                    # TODO: Add seed and variant information
                    fullnames = ['experiment_id', 'time', 'generation']
                self._create_indexes(table_id, fullnames)
            self.write_emit(new_dict, table_id, col_types)

    def write_emit(self, flat_data: dict[str, Any], table_id: str, col_types):
        """
        Write data as new row, creating new columns as needed.
        """
        # Add new columns if necessary (e.g. if new Stores were created)
        new_keys = set(flat_data) - set(self._tables_created[table_id])
        if len(new_keys) > 0:
            self._add_new_keys(new_keys, table_id, col_types)

        # Retrieve column indices to insert
        col_names = [str(self._tables_created[table_id][full_name])
                     for full_name in flat_data]
        asyncio.run_coroutine_threadsafe(async_copy_records(
            self.connection_args, table_id,
            (list(flat_data.values()),), col_names),
            self.async_event_loop).result()

    def get_data(self, query: Optional[list] = None) -> None:
        return None


class AsyncpgMPEmitter(AsyncpgEmitter):
    """
    Emit data to a PostgreSQL database with proper conversion of
    lists into PostgreSQL arrays. Creates a separate OS process
    to handle this expensive insert operation.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """
        Pull connection arguments from ``config`` and start separate OS
        process for database inserts. See :py:meth:`.AsyncpgEmitter.__init__`.
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
        self.inc_lists = config.get('inc_lists', False)
        self._tables_created = {}
        self.fallback_serializer = make_fallback_serializer_function()
        self.main_queue = Queue()
        self.db_process = Process(target=main_process,
            args=(self.main_queue, self.connection_args))
        self.db_process.start()
        self.inc_lists = True
        atexit.register(self._cleanup)

    def _cleanup(self):
        """Tell separate OS process to finish inserts and close."""
        self.main_queue.put('Shutting down...')
        self.db_process.join()

    def write_emit(self, flat_data: dict[str, Any], table_id: str, col_types) -> None:
        """
        Write data as new row, creating new columns needed.
        """
        # Add new columns if necessary (e.g. if new Stores were created)
        new_keys = set(flat_data) - set(self._tables_created[table_id])
        if len(new_keys) > 0:
            self._add_new_keys(new_keys, table_id, col_types)

        if not self.db_process.is_alive():
            raise Exception('DB process was killed.')
        colnames = [str(self._tables_created[table_id][k]) for k in flat_data]
        self.main_queue.put((table_id, (list(flat_data.values()),), colnames))
