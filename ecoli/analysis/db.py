import copy
import itertools
import collections
from bson import MinKey, MaxKey
from functools import partial
from concurrent.futures import ProcessPoolExecutor

from vivarium.core.emitter import (
    data_from_database,
    DatabaseEmitter,
    assemble_data,
    get_local_client,
    get_data_chunks
)
from vivarium.core.serialize import deserialize_value
from vivarium.library.units import remove_units

from ecoli.library.sim_data import LoadSimData


def deserialize_and_remove_units(d):
    return remove_units(deserialize_value(d))


def custom_deep_merge_check(
    dct, merge_dct, check_equality=False, path=tuple(), overwrite_none=False):
    """Recursively merge dictionaries with checks to avoid overwriting. Also
    allows None value to always be overwritten (useful for aggregation pipelines
    that return None for missing fields like in `access_counts`).

    Args:
        dct: The dictionary to merge into. This dictionary is mutated
            and ends up being the merged dictionary.  If you want to
            keep dct you could call it like
            ``deep_merge_check(copy.deepcopy(dct), merge_dct)``.
        merge_dct: The dictionary to merge into ``dct``.
        check_equality: Whether to use ``==`` to check for conflicts
            instead of the default ``is`` comparator. Note that ``==``
            can cause problems when used with Numpy arrays.
        path: If the ``dct`` is nested within a larger dictionary, the
            path to ``dct``. This is normally an empty tuple (the
            default) for the end user but is used for recursive calls.
        overwrite_none: If true, None values will always be overwritten
            by other values. 

    Returns:
        ``dct``

    Raises:
        ValueError: Raised when conflicting values are found between
            ``dct`` and ``merge_dct``.
    """
    for k in merge_dct:
        if overwrite_none and merge_dct[k] == None:
            continue
        if k in dct:
            if overwrite_none and dct[k] == None:
                dct[k] = merge_dct[k]
                continue
            elif (isinstance(dct[k], dict)
                and isinstance(merge_dct[k], collections.abc.Mapping)):
                custom_deep_merge_check(
                    dct[k], merge_dct[k],
                    check_equality, path + (k,), overwrite_none)
            elif not check_equality and (dct[k] is not merge_dct[k]):
                raise ValueError(
                    f'Failure to deep-merge dictionaries at path '
                    f'{path + (k,)}: {dct[k]} IS NOT {merge_dct[k]}'
                )
            elif check_equality and (dct[k] != merge_dct[k]):
                raise ValueError(
                    f'Failure to deep-merge dictionaries at path '
                    f'{path + (k,)}: {dct[k]} DOES NOT EQUAL {merge_dct[k]}'
                )
            else:
                dct[k] = merge_dct[k]
        else:
            dct[k] = merge_dct[k]
    return dct


def access(
    experiment_id, query=None, host='localhost', port=27017,
    func_dict=None, f=None, sampling_rate=None, start_time=MinKey(),
    end_time=MaxKey(), cpus=1
):
    config = {
        'host': '{}:{}'.format(host, port),
        'database': 'simulations'}
    emitter = DatabaseEmitter(config)
    db = emitter.db

    filters = {}
    if sampling_rate:
        filters['data.time'] = {'$mod': [sampling_rate, 0]}
    data, sim_config = data_from_database(
        experiment_id, db, query, func_dict, f, filters,
        start_time, end_time, cpus)

    return data, experiment_id, sim_config


def get_agent_ids(experiment_id, host='localhost', port=27017):
    config = {
        'host': '{}:{}'.format(host, port),
        'database': 'simulations'}
    emitter = DatabaseEmitter(config)
    db = emitter.db

    result = db.history.aggregate([
        {'$match': {'experiment_id': experiment_id}},
        {'$project': {'agents': {'$objectToArray': '$data.agents'}}},
        {'$project': {'agents.k': 1, '_id': 0}},
    ])
    agents = set()
    for document in result:
        assert list(document.keys()) == ['agents']
        for sub_document in document['agents']:
            assert list(sub_document.keys()) == ['k']
            agents.add(sub_document['k'])
    return agents


def get_aggregation(host, port, aggregation):
    """Helper function for parallel aggregations"""
    history_collection = get_local_client(host, port, 'simulations').history
    return list(history_collection.aggregate(
        aggregation, hint={'experiment_id':1, 'data.time':1, '_id':1}))


def access_counts(experiment_id, monomer_names=None, mrna_names=None,
    rna_init=None, rna_synth_prob=None, inner_paths=None, outer_paths=None,
    host='localhost', port=27017, sampling_rate=None, start_time=None,
    end_time=None, cpus=1
):
    """Retrieve monomer/mRNA counts or any other data from MongoDB. Note that
    this only works for experiments run using EcoliEngineProcess (each cell
    emits separately).
    
    Args:
        experiment_id: Experiment ID for simulation
        monomer_names: Refer to reconstruction/ecoli/flat/rnas.tsv
        mrna_names: Refer to reconstruction/ecoli/flat/rnas.tsv
        rna_init: List of RNAs to get # of initiations / timestep for
        rna_synth_prob: List of RNAs to get synthesis probabilities for
        inner_paths: Paths to stores inside each agent. For example,
            if you want to get the surface area of each cell, putting
            [('surface_area',)] here would retrieve:
            ('data', 'agents', '0', 'surface_area'), 
            ('data', 'agents', '01', 'surface_area'),
            ('data', 'agents', '00', 'surface_area'), etc.
        outer_paths: Paths to stores in outer sim. Putting [('data', 'time',)]
            here would retrieve ('data', 'time').
        host: Host name of MongoDB
        port: Port of MongoDB
        sampling_rate: Get data every this many seconds
        start_time: Time to start pulling data
        end_time: Time to stop pulling data
        cpus: Number of chunks to split aggregation into to be run in parallel
    """
    if not monomer_names:
        monomer_names = []
    if not mrna_names:
        mrna_names = []
    if not rna_init:
        rna_init = []
    if not rna_synth_prob:
        rna_synth_prob = []
    if not inner_paths:
        inner_paths = []
    if not outer_paths:
        outer_paths = []
    if not start_time:
        start_time = MinKey()
    if not end_time:
        end_time = MaxKey()
    config = {
        'host': f'{host}:{port}',
        'database': 'simulations'
    }
    emitter = DatabaseEmitter(config)
    db = emitter.db

    # Retrieve and re-assemble experiment config
    experiment_query = {'experiment_id': experiment_id}
    experiment_config = db.configuration.find(experiment_query)
    experiment_assembly = assemble_data(experiment_config)
    assert len(experiment_assembly) == 1
    assembly_id = list(experiment_assembly.keys())[0]
    experiment_config = experiment_assembly[assembly_id]['metadata']
    # Load sim_data using parameters from experiment_config
    rnai_data = experiment_config['process_configs'].get(
        'ecoli-rna-interference', None)
    sim_data = LoadSimData(
        sim_data_path=experiment_config['sim_data_path'],
        seed=experiment_config['seed'],
        mar_regulon=experiment_config.get('mar_regulon', False),
        rnai_data=rnai_data)

    time_filter = {'data.time': {'$gte': start_time, '$lte': end_time}}
    if sampling_rate:
        time_filter['data.time']['$mod'] = [sampling_rate, 0]
    aggregation = [{'$match': {
        **experiment_query, **time_filter}}]
    aggregation.append({'$project': {
        'data.agents': {'$objectToArray': '$data.agents'},
        'data.time': 1,
        'data.fields': 1,
        'data.dimensions': 1,
        'assembly_id': 1,
        }})
    monomer_idx = sim_data.get_monomer_counts_indices(monomer_names)
    projection = {
        '$project': {
            f'data.agents.v.monomer.{monomer}': {
                # Flatten array of length 1 into single count
                '$reduce': {
                    # Get monomer count at specified index with $slice
                    'input': {'$slice': [
                        # $objectToArray makes all embedded document fields 
                        # into arrays so we flatten here before slicing
                        {'$reduce': {
                            'input': '$data.agents.v.listeners.monomer_counts',
                            'initialValue': None,
                            'in': '$$this'
                        }},
                        monomer_index,
                        1
                    ]},
                    'initialValue': None,
                    'in': '$$this'
                }
            }
            for monomer, monomer_index in zip(monomer_names, monomer_idx)
        }
    }
    mrna_idx = sim_data.get_mrna_counts_indices(mrna_names)
    projection['$project'].update({
        f'data.agents.v.mrna.{mrna}': {
            # Flatten array of length 1 into single count
            '$reduce': {
                # Get monomer count at specified index with $slice
                'input': {'$slice': [
                    # $objectToArray makes all embedded document fields 
                    # into arrays so we flatten here before slicing
                    {'$reduce': {
                        'input': '$data.agents.v.listeners.mRNA_counts',
                        'initialValue': None,
                        'in': '$$this'
                    }},
                    mrna_index,
                    1
                ]},
                'initialValue': None,
                'in': '$$this'
            }
        }
        for mrna, mrna_index in zip(mrna_names, mrna_idx)
    })
    rna_idx = sim_data.get_rna_indices(rna_init)
    projection['$project'].update({
        f'data.agents.v.rna_init.{rna}': {
            # Flatten array of length 1 into single count
            '$reduce': {
                # Get monomer count at specified index with $slice
                'input': {'$slice': [
                    # $objectToArray makes all embedded document fields 
                    # into arrays so we flatten here before slicing
                    {'$reduce': {
                        'input': '$data.agents.v.listeners.rnap_data.rnaInitEvent',
                        'initialValue': None,
                        'in': '$$this'
                    }},
                    rna_index,
                    1
                ]},
                'initialValue': None,
                'in': '$$this'
            }
        }
        for rna, rna_index in zip(rna_init, rna_idx)
    })
    rna_idx = sim_data.get_rna_indices(rna_synth_prob)
    projection['$project'].update({
        f'data.agents.v.rna_synth_prob.{rna}': {
            # Flatten array of length 1 into single count
            '$reduce': {
                # Get monomer count at specified index with $slice
                'input': {'$slice': [
                    # $objectToArray makes all embedded document fields 
                    # into arrays so we flatten here before slicing
                    {'$reduce': {
                        'input': '$data.agents.v.listeners.rna_synth_prob.rna_synth_prob',
                        'initialValue': None,
                        'in': '$$this'
                    }},
                    rna_index,
                    1
                ]},
                'initialValue': None,
                'in': '$$this'
            }
        }
        for rna, rna_index in zip(rna_synth_prob, rna_idx)
    })
    for inner_path in inner_paths:
        inner_path = ('data', 'agents', 'v') + inner_path
        projection['$project']['.'.join(inner_path)] = 1
    # Boundary data necessary for snapshot plots
    projection['$project']['data.agents.v.boundary'] = 1
    projection['$project']['data.fields'] = 1
    projection['$project']['data.dimensions'] = 1
    projection['$project']['data.agents.k'] = 1
    projection['$project']['data.time'] = 1
    projection['$project']['assembly_id'] = 1
    aggregation.append(projection)

    final_projection = {'$project': {
        'data.agents': {'$arrayToObject': '$data.agents'},
        'data.time': 1,
        'assembly_id': 1,
    }}
    for outer_path in outer_paths:
        final_projection['$project']['.'.join(outer_path)] = 1
    aggregation.append(final_projection)
    
    if cpus > 1:
        chunks = get_data_chunks(
            db.history, experiment_id, start_time, end_time, cpus)
        aggregations = []
        for chunk in chunks:
            agg_chunk = copy.deepcopy(aggregation)
            agg_chunk[0]['$match'] = {
                **experiment_query,
                '_id': {'$gte': chunk[0], '$lt': chunk[1]},
                'data.time': {'$gte': start_time, '$lte': end_time, 
                              '$mod': [sampling_rate, 0]}
            }
            aggregations.append(agg_chunk)
        partial_get_agg = partial(get_aggregation, host, port)
        with ProcessPoolExecutor(cpus) as executor:
            queried_chunks = executor.map(partial_get_agg, aggregations)
        result = itertools.chain.from_iterable(queried_chunks)
    else:
        result = db.history.aggregate(
            aggregation, 
            hint={'experiment_id':1, 'data.time':1, '_id':1})

    # re-assemble data
    assembly = assemble_data(list(result))

    # restructure by time
    data = {}
    for datum in assembly.values():
        time = datum['time']
        datum = datum.copy()
        datum.pop('_id', None)
        datum.pop('time', None)
        custom_deep_merge_check(
            data,
            {time: datum},
            check_equality=True,
            overwrite_none=True
        )

    return data
