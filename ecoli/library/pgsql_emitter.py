from vivarium.core.emitter import Emitter
import psycopg
from psycopg import sql
from typing import Any, Mapping, Optional

from vivarium.core.serialize import (
    make_fallback_serializer_function,
    serialize_value,
    deserialize_value)

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
            # TODO: Fix empty values and remove
            elif isinstance(v, list) and len(v) == 0:
                continue
            elif v is None:
                continue
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
        conninfo = ("postgresql://postgres:postgres@"
            f"{config.get('host', 'localhost')}:{config.get('port', 5432)}"
            "/postgres")
        self.conn = psycopg.connect(conninfo, autocommit=True)
        self.cur = self.conn.cursor()
        self._tables_created = {}
        self.fallback_serializer = make_fallback_serializer_function()

    def _get_fields(self, flat_data: dict[str, Any], table_id: str, field_names: str):
        """Get PostgreSQL column name and type for each value in dictionary
        in the format expected by CREATE/ALTER TABLE. Replaces full column
        names with number indicating index of column in separate column
        name table."""
        col_types = []
        for col, val in flat_data.items():
            # Use text type for all strings
            if isinstance(val, str):
                col_types.append((col, 'text'))
                continue
            elif isinstance(val, list):
                if isinstance(val[0], str):
                    col_types.append((col, 'text[]'))
                    continue
                elif isinstance(val[0], list) and isinstance(val[0][0], str):
                    col_types.append((col, 'text[]'))
                    continue
            type_cmd = sql.SQL("SELECT pg_typeof({})").format(sql.Literal(val))
            self.cur.execute(type_cmd)
            col_types.append((col, self.cur.fetchone()[0]))
        fields = []
        for col_type, field_name in zip(col_types, field_names):
            fields.append(sql.SQL("{} {}").format(
                sql.Identifier(str(field_name)), sql.SQL(col_type[1])))
        return fields
    
    def _keys_to_add(self, table_id, keys):
        colnames_table = table_id + '_colnames'
        get_curr_colnames = sql.SQL("SELECT {}, {} FROM {}").format(
            sql.Identifier("fullname"),
            sql.Identifier("numname"),
            sql.Identifier(colnames_table))
        self.cur.execute(get_curr_colnames)
        full_to_numnames = dict(self.cur.fetchall())
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
        
    def emit(self, data: dict[str, Any]) -> None:
        table_id = data['table']
        emit_dicts = reorganize_data(data['data'], self.experiment_id)
        for d in emit_dicts:
            flat_dict = dict(flatten_dict(d))
            flat_dict = serialize_value(flat_dict, self.fallback_serializer)
            self.write_emit(table_id, flat_dict)

    def write_emit(self, table_id: str, flat_data: dict[str, Any]) -> None:
        """
        Write data as new row, creating tables, columns, and indices as needed.
        """
        # First time a table is written to, create if necessary
        if table_id not in self._tables_created:
            field_names = list(range(len(flat_data)))
            fields = self._get_fields(flat_data, table_id, field_names)
            fields = sql.SQL(', ').join(fields)
            create_table_cmd = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})"
                ).format(sql.Identifier(table_id), fields)
            self.cur.execute(create_table_cmd)
            
            # PostgreSQL has 63 character limit for column names. Use number
            # column names in the main table and create a separate table
            # with two columns: full column name and number column name
            colnames_table = table_id + '_colnames'
            create_table_cmd = sql.SQL('CREATE TABLE IF NOT EXISTS {} '
                '({} integer, {} text)').format(
                    sql.Identifier(colnames_table),
                    sql.Identifier("numname"),
                    sql.Identifier("fullname"))
            self.cur.execute(create_table_cmd)
            
            keys_to_add = self._keys_to_add(table_id, flat_data.keys())
            insert_colnames_cmd = sql.SQL("COPY {} ({}, {}) FROM STDIN"
                ).format(sql.Identifier(colnames_table),
                    sql.Identifier("fullname"),
                    sql.Identifier("numname"))
            with self.cur.copy(insert_colnames_cmd) as copy:
                for record in keys_to_add:
                    copy.write_row(record)
        
        # Add new columns if necessary (e.g. if new Stores were created)
        new_keys = set(flat_data) - set(self._tables_created[table_id])
        if len(new_keys) > 0:
            new_keys = list(new_keys)
            keys_to_add = self._keys_to_add(table_id, new_keys)
            fullnames_to_add, numnames_to_add = list(zip(*keys_to_add))
            fields = self._get_fields(dict(
                ((k,v) for k,v in flat_data.items() if k in fullnames_to_add)), table_id,
                numnames_to_add)
            fields = sql.SQL(', ').join([sql.SQL("ADD COLUMN {}").format(
                i) for i in fields])
            alter_table_cmd = sql.SQL("ALTER TABLE {} \
                {}""").format(sql.Identifier(table_id), fields)
            self.cur.execute(alter_table_cmd)

            colnames_table = table_id + '_colnames'
            insert_colnames_cmd = sql.SQL("COPY {} ({}, {}) FROM STDIN"
                ).format(sql.Identifier(colnames_table),
                    sql.Identifier("fullname"),
                    sql.Identifier("numname"))
            with self.cur.copy(insert_colnames_cmd) as copy:
                for record in keys_to_add:
                    copy.write_row(record)

        # Retrieve column indices to insert
        col_idx = [str(self._tables_created[table_id][full_name])
                   for full_name in flat_data.keys()]
        insert_cmd = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table_id),
            sql.SQL(', ').join(map(sql.Identifier, col_idx)),
            sql.SQL(', ').join(sql.Placeholder() * len(flat_data))
        )
        self.cur.execute(insert_cmd, list(flat_data.values()))
        

    def get_data(self, query: Optional[list] = None) -> None:
        return None