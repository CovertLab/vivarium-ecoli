import itertools
import queue
import threading
from typing import Any, Mapping, Optional

import orjson
import psycopg
from psycopg import sql
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

_FLAG_FIRST = object()

def make_fields(col_types: list[str], field_names: list[str]):
    """Combine PostgreSQL column name and type
    in the format expected by CREATE/ALTER TABLE."""
    fields = []
    for col_type, field_name in zip(col_types, field_names):
        fields.append(sql.SQL("{} {}").format(
            sql.Identifier(str(field_name)),
            sql.SQL(col_type)))
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


class PostgresEmitter(Emitter):
    """
    Emit data to a PostgreSQL database

    Example:

    >>> config = {
    ...     'host': 'localhost',
    ...     'port': 5432,
    ...     'database': 'DB_NAME',
    ... }
    >>> # The line below works only if you have to have 5432 open locally
    >>> # emitter = PostgresEmitter(config)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """config may have 'host' and 'database' items."""
        super().__init__(config)
        self.experiment_id = config.get('experiment_id')
        # Construct connection string
        self.conninfo = ("postgresql://postgres:postgres@"
            f"{config.get('host', 'localhost')}:{config.get('port', 5432)}"
            "/postgres")
        self._tables_created = {}
        self.fallback_serializer = make_fallback_serializer_function()
        self.query_queue = queue.Queue()
        self.submit_thread = threading.Thread(
            target=self._insert_query, daemon=True
        )
        self.submit_thread.start()

    def _insert_query(self):
        while True:
            query = self.query_queue.get()
            with psycopg.connect(self.conninfo, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(query[0], query[1])

    def _get_curr_colnames(self, table_id):
        colnames_table = table_id + '_colnames'
        get_curr_colnames = sql.SQL('SELECT fullname, numname FROM {}').format(
            sql.Identifier(colnames_table))
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(get_curr_colnames)
                full_to_numnames = dict(cur.fetchall())
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT column_name FROM "
                    "information_schema.columns WHERE table_name=%s",
                    (table_id,))
                existing_num_names = [int(i) for i in list(zip(*cur.fetchall()))[0]]
        assert set(full_to_numnames.values()) == set(existing_num_names)
        return full_to_numnames

    def _keys_to_add(self, full_to_numnames, table_id, keys):
        curr_max_numname = -1
        if len(full_to_numnames) > 0:
            curr_max_numname = max(full_to_numnames.values())
        keys_to_add = []
        for key in keys:
            if key not in full_to_numnames:
                curr_max_numname += 1
                full_to_numnames[key] = curr_max_numname
                keys_to_add.append((key, curr_max_numname))
        self._tables_created[table_id] = full_to_numnames
        return keys_to_add
    
    def _create_table(self, table_id, col_types):
        field_names = list(range(len(col_types)))
        fields = make_fields(list(col_types.values()), field_names)
        fields = sql.SQL(', ').join(fields)
        create_table_cmd = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})"
            ).format(sql.Identifier(table_id), fields)
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_cmd)
        
        # PostgreSQL has 63 character limit for column names. Use number
        # column names in the main table and create a separate table
        # with two columns: full column name and number column name
        colnames_table = table_id + '_colnames'
        create_table_prefix = sql.SQL('CREATE TABLE IF NOT EXISTS {} '
            '("numname", "fullname") AS SELECT * FROM ( VALUES ').format(
                sql.Identifier(colnames_table))
        col_name_combined = list(itertools.chain.from_iterable(list(enumerate(col_types))))
        col_values = sql.SQL(", ").join(sql.SQL("({})").format(
            sql.SQL(',').join(sql.Placeholder()*2)) * len(col_types))
        create_table_cmd = sql.Composed([create_table_prefix, col_values, sql.SQL(" )")])
        with psycopg.connect(self.conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_cmd, col_name_combined)

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
                if isinstance(py_val, str):
                    new_dict[k] = py_val
                    col_types[k] = 'text'
                elif isinstance(py_val, int):
                    new_dict[k] = py_val
                    col_types[k] = 'bigint'
                elif isinstance(py_val, float):
                    new_dict[k] = py_val
                    col_types[k] = 'numeric'
                elif isinstance(py_val, bool):
                    new_dict[k] = py_val
                    col_types[k] = 'boolean'
                else:
                    new_dict[k] = bin_val
                    col_types[k] = 'bytea'
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
            curr_max_numname = max(self._tables_created[table_id].values())
            keys_to_add = {}
            for key in new_keys:
                curr_max_numname += 1
                self._tables_created[table_id][key] = curr_max_numname
                keys_to_add[key] = curr_max_numname
            fields = make_fields([col_types[k] for k in new_keys], list(keys_to_add.values()))
            fields = sql.SQL(', ').join([sql.SQL("ADD COLUMN {}").format(
                i) for i in fields])
            alter_table_cmd = sql.SQL("ALTER TABLE {} \
                {}""").format(sql.Identifier(table_id), fields)
            with psycopg.connect(self.conninfo, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(alter_table_cmd)

            colnames_table = table_id + '_colnames'
            insert_colnames_cmd = sql.SQL(
                'COPY {} ("fullname", "numname") FROM STDIN'
                ).format(sql.Identifier(colnames_table))
            with psycopg.connect(self.conninfo, autocommit=True) as conn:
                with conn.cursor() as cur:
                    with cur.copy(insert_colnames_cmd) as copy:
                        for record in keys_to_add.items():
                            copy.write_row(record)

        # Retrieve column indices to insert
        col_idx = [str(self._tables_created[table_id][full_name])
                   for full_name in flat_data.keys()]
        insert_cmd = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table_id),
            sql.SQL(', ').join(map(sql.Identifier, col_idx)),
            sql.SQL(', ').join(sql.Placeholder() * len(flat_data))
        )
        self.query_queue.put((insert_cmd, list(flat_data.values())))

    def get_data(self, query: Optional[list] = None) -> None:
        return None