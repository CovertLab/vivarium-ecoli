"""
========
Emitters
========

Emitters log configuration data and time-series data somewhere.
"""

import os
from typing import Any, Dict, Mapping, Optional
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

import orjson
import datetime
import pytz
import bsonjs
from bson.raw_bson import RawBSONDocument
from pymongo.mongo_client import MongoClient
from vivarium.core.emitter import Emitter
from vivarium.core.serialize import make_fallback_serializer_function

CONFIGURATION_INDEXES = [
    'experiment_id',
    'variant',
    'seed',
    'generation',
    'agent_id'
]

_FLAG_FIRST = object()

def flatten_dict(d: dict):
    """
    Flatten nested dictionary down to key-value pairs where each key
    concatenates all the keys needed to reach the
    corresponding value in the input. Prunes empty dicts and lists.
    """
    results = []

    def visit_key(subdict, results, partialKey, popKey):
        subdict_copy = dict(subdict)
        for k, v in subdict_copy.items():
            newKey = k if partialKey==_FLAG_FIRST else f'{partialKey}__{k}'
            if isinstance(v, Mapping):
                visit_key(v, results, newKey, popKey)
                for key_to_pop in popKey:
                    subdict[k].pop(key_to_pop, None)
                popKey.clear()
                if len(v) == 0:
                    subdict.pop(k)
            elif isinstance(v, list):
                v_len = len(v)
                if v_len == 0:
                    popKey.add(k)
                    return
                if v_len > 100:
                    popKey.add(k)
                    results.append((newKey, v))

    visit_key(d, results, _FLAG_FIRST, set())
    return dict(results)


def insert(host, agent_data, metadata, separate_docs):
    db = MongoClient(host, compressors='zstd').simulations
    db.history.insert_one({**metadata, **agent_data})
    curr_collections = db.list_collection_names()
    for k, v in separate_docs.items():
        if k not in curr_collections:
            db.create_collection(k, timeseries={
                'timeField': 'time', 'metaField': 'metadata'})
        db[k].insert_one({**metadata, 'v': v})


class MongoTimeseries(Emitter):
    """
    Emit data to a MongoDB timeseries collection

    Example:

    >>> config = {
    ...     'host': 'localhost:27017',
    ...     'database': 'DB_NAME',
    ... }
    >>> # The line below works only if you have to have 27017 open locally
    >>> # emitter = DatabaseEmitter(config)
    """
    default_host = 'localhost:27017'
    client_dict: Dict[int, MongoClient] = {}

    def __init__(self, config: Dict[str, Any]) -> None:
        """config may have 'host' and 'database' items."""
        super().__init__(config)
        self.experiment_id = config.get('experiment_id')
        # create new MongoClient per OS process
        curr_pid = os.getpid()
        if curr_pid not in MongoTimeseries.client_dict:
            MongoTimeseries.client_dict[curr_pid] = MongoClient(
                config.get('host', self.default_host),
                compressors='zstd')
        self.host = config.get('host', self.default_host)
        self.client = MongoTimeseries.client_dict[curr_pid]

        self.db = self.client[config.get('database', 'simulations')]
        curr_collections = self.db.list_collection_names()
        if 'history' not in curr_collections:
            self.db.create_collection('history', timeseries={
                'timeField': 'time', 'metaField': 'metadata'})
        self.configuration = self.db['configuration']
        for column in CONFIGURATION_INDEXES:
            self.configuration.create_index(column)

        self.fallback_serializer = make_fallback_serializer_function()
        self.executor = ProcessPoolExecutor()
    

    def emit(self, data: Dict[str, Any]) -> None:
        table_id = data['table']
        table = self.db[table_id]
        emit_data = data['data']
        # Metadata will be in configuration (first) emit
        if 'metadata' in emit_data:
            emit_data = {**emit_data.pop('metadata'), **emit_data}
            emit_data['generation'] = len(emit_data['agent_id'])
            # TODO: This key needs to be added
            emit_data['variant'] = 0
            emit_data = RawBSONDocument(bsonjs.loads(orjson.dumps(
                emit_data, option=orjson.OPT_SERIALIZE_NUMPY,
                default=self.fallback_serializer)))
            table.insert_one(emit_data)
            return
        time = data['data'].pop('time', None)
        time = datetime.datetime(1, 1, 1, tzinfo=pytz.timezone('UTC')
            ) + datetime.timedelta(seconds=time)
        for agent_id, agent_data in emit_data['agents'].items():
            agent_data = orjson.loads(orjson.dumps(
                agent_data, option=orjson.OPT_SERIALIZE_NUMPY,
                default=self.fallback_serializer))
            separate_docs = flatten_dict(agent_data)
            metadata = {
                'time': time,
                'metadata': {
                    'agent_id': agent_id,
                    'experiment_id': self.experiment_id,
                    'generation': len(agent_id),
                    'seed': 0,
                    'variant': ''
                }
            }
            self.executor.submit(insert(
                self.host, agent_data, metadata, separate_docs))

    def get_data(self, query: Optional[list] = None) -> dict:
        return {}

