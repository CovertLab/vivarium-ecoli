import copy
import itertools
import collections
from bson import MinKey, MaxKey
from functools import partial
from concurrent.futures import ProcessPoolExecutor
from typing import Optional

from vivarium.core.emitter import (
    data_from_database,
    DatabaseEmitter,
    assemble_data,
    get_local_client,
    get_data_chunks,
    apply_func
)
from vivarium.core.serialize import deserialize_value
from vivarium.library.units import remove_units

from ecoli.analysis.centralCarbonMetabolismScatter import get_toya_flux_rxns
from ecoli.library.sim_data import LoadSimData, SIM_DATA_PATH
from wholecell.utils import toya


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
        if overwrite_none and merge_dct[k] is None:
            continue
        if k in dct:
            if overwrite_none and dct[k] is None:
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


def val_at_idx_in_path(idx, path):
    """Helper function that returns MongoDB aggregation expression
    which evaluates to the value at idx in the array at path after
    an $objectToArray projection."""
    return {
        '$cond': {
            # If path does not point to an array, return null
            'if': {'$isArray': {'$first': f"${path}"}},
            'then': {
                # Flatten array of length 1 into single count
                '$first': {
                    # Get monomer count at specified index with $slice
                    '$slice': [
                        # $objectToArray turns all embedded document fields 
                        # into arrays so we flatten here before slicing
                        {'$first': f"${path}"},
                        idx,
                        1
                    ]
                },
            },
            'else': None
        }
        
    }


def access_data(experiment_id: str, path_dict: dict[tuple[str], Optional[int]],
                variant_id: Optional[str]=None, seed: Optional[int]=None,
                generation: Optional[int]=None, host: str='localhost',
                port: int=27017, sampling_rate: Optional[int]=None,
                start_time: Optional[int]=None, end_time: Optional[int]=None,
                cpus: int=1):
    """
    Retrieve simulation data from MongoDB for a single experiment.

    TODO: Figure out how to implement variant, seed, and generation IDs.
    
    Args:
        experiment_id: Experiment ID for simulation/workflow
        path_dict: Mapping of tuples to optional integer index of interest
            if value at tuple path is an array (e.g. bulk counts). Tuple paths
            should omit the ('agent', agent_id) prefix. By default, data is
            collected for all paths from all cells in the specified time range.
        variant_id: Variant ID to retrieve data for
        seed: Initial seed to retrieve data for
        generation: Simulated generation to retrieve data for. Only applies for
            lineage simulation workflows (not colony simulations)
        host: Hostname of MongoDB server
        port: Port of MongoDB server
        sampling_rate: Number of seconds between retrieved data points (get
            everything by default)
        start_time: Initial time for data retrieval (default: minimum possible)
        end_time: Final time for data retrieval (default: max possible)
        cpus: Number of processes to parallelize query over
    """
    config = {
        'host': f'{host}:{port}',
        'database': 'simulations'
    }
    emitter = DatabaseEmitter(config)
    db = emitter.db

    if start_time is None:
        start_time = MinKey()
    if end_time is None:
        end_time = MaxKey()

    # Match experiment ID and time range
    experiment_query = {'experiment_id': experiment_id}
    time_filter = {'data.time': {'$gte': start_time, '$lte': end_time}}
    if sampling_rate:
        time_filter['data.time']['$mod'] = [sampling_rate, 0]
    # Ensure data is ordered by time (putting it early in pipeline
    # allows MongoDB to use index)
    aggregation = [{'$match': {**experiment_query, **time_filter}},
                   {'$sort': {'data.time': 1}}]
    # Separate agents into their own documents
    aggregation.append({'$project': {
        'data.agents': {
            '$objectToArray': {
                # Add fail-safe for sims with no live agents
                '$ifNull': ['$data.agents', {}]
            }
        },
        'data.time': 1,
        }})
    aggregation.append({'$unwind': '$data.agents'})
    # Construct aggregation stage to retrieve data at paths and group them
    # into timeseries arrays for each agent ID
    retrieve_paths = {
        'data.agents.k': 1,
        'data.time': 1
    }
    group_paths = {
        '_id': '$data.agents.k',
        'start_time': {'$first': '$data.time'},
        'end_time': {'$last': '$data.time'}
    }
    final_projection = {
        '_id': 0,
        'agent_id': '$_id',
        'start_time': 1,
        'end_time': 1
    }
    for path, index in path_dict.items():
        path_name = '.'.join(path)
        # Group stage does not allow dot notation (nested dictionaries),
        # so we temporarily restructure into a single-level dictionary
        # with field names equal to the path joined by double underscores
        temp_path_name = '__'.join(path)
        dollar_path_name = '$data.agents.' + path_name
        dollar_temp_path_name = '$'+ temp_path_name
        if index is not None:
            retrieve_paths[temp_path_name] = {
                '$arrayElemAt': [dollar_path_name, index]
            }
        else:
            retrieve_paths[temp_path_name] = dollar_path_name
        group_paths[temp_path_name] = {
            '$push': {
                '$cond': {
                    # When data is split across multiple documents to meet
                    # MongoDB's 16MB/doc limit, not all documents will have
                    # all paths. In these cases, we hack MongoDB to skip
                    # pushing null values onto the growing array by telling
                    # it to push the non-existent 'noval' field instead.
                    'if': {'$ne': [dollar_temp_path_name, None]},
                    'then': dollar_temp_path_name,
                    'else': '$noval'
                }
            }
        }
        # Final project stage brings back nested dictionary structure
        final_projection[path_name] = dollar_temp_path_name
    aggregation.append({'$project': retrieve_paths})
    aggregation.append({'$group': group_paths})
    aggregation.append({'$project': final_projection})

    # Having many CPU processes query different segments of the total
    # search space greatly improves I/O saturation for maximum speed
    if cpus > 1:
        chunks = get_data_chunks(
            db.history, experiment_id, start_time, end_time, cpus)
        aggregations = []
        for chunk in chunks:
            agg_chunk = copy.deepcopy(aggregation)
            agg_chunk[0]['$match'] = {
                **experiment_query,
                '_id': {'$gte': chunk[0], '$lt': chunk[1]},
                **time_filter
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
    return list(result)


def access_counts_old(experiment_id, monomer_names=None, mrna_names=None,
    rna_init=None, rna_synth_prob=None, inner_paths=None, outer_paths=None,
    host='localhost', port=27017, sampling_rate=None, start_time=None,
    end_time=None, cpus=1, func_dict=None
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
        func_dict: a dict which maps the given query paths to a function that
            operates on the retrieved values and returns the results. If None
            then the raw values are returned.
            In the format: {('path', 'to', 'field1'): function}
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
        'data.agents': {
            '$objectToArray': {
                # Add fail-safe for sims with no live agents
                '$ifNull': ['$data.agents', {}]
            }
        },
        'data.time': 1,
        'data.fields': 1,
        'data.dimensions': 1,
        'assembly_id': 1,
        }})
    monomer_idx = sim_data.get_monomer_counts_indices(monomer_names)
    projection = {
        '$project': {
            f'data.agents.v.monomer.{monomer}':
                val_at_idx_in_path(
                    monomer_index, 
                    "data.agents.v.listeners.monomer_counts"
                )
            for monomer, monomer_index in zip(monomer_names, monomer_idx)
        }
    }
    mrna_idx = sim_data.get_mrna_counts_indices(mrna_names)
    projection['$project'].update({
        f'data.agents.v.mrna.{mrna}':
            val_at_idx_in_path(
                mrna_index,
                'data.agents.v.listeners.rna_counts.mRNA_counts'
            )
        for mrna, mrna_index in zip(mrna_names, mrna_idx)
    })
    projection['$project'].update({
        'data.agents.v.total_mrna': {
            '$cond': {
                # If path does not point to an array, return null
                'if': {'$isArray': {'$first': '$data.agents.v.listeners.rna_counts.mRNA_counts'}},
                'then': {
                    '$sum': {
                        '$first': '$data.agents.v.listeners.rna_counts.mRNA_counts'
                    }
                },
                'else': None
            }
        }
    })
    rna_idx = sim_data.get_rna_indices(rna_init)
    projection['$project'].update({
        f'data.agents.v.rna_init.{rna}':
            val_at_idx_in_path(
                rna_index,
                'data.agents.v.listeners.rnap_data.rna_init_event'
            )
        for rna, rna_index in zip(rna_init, rna_idx)
    })
    rna_idx = sim_data.get_rna_indices(rna_synth_prob)
    projection['$project'].update({
        f'data.agents.v.rna_synth_prob.{rna}':
            val_at_idx_in_path(
                rna_index,
                'data.agents.v.listeners.rna_synth_prob.rna_synth_prob'
            )
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
    final_projection['$project']['data.fields'] = 1
    final_projection['$project']['data.dimensions'] = 1
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
    
    if func_dict:
        raw_data = []
        for document in result:
            assert document.get('assembly_id'), \
                "all database documents require an assembly_id"
            for field, func in func_dict.items():
                document["data"] = apply_func(
                    document["data"], field, func)
            raw_data.append(document)
    else:
        raw_data = result

    # re-assemble data
    assembly = assemble_data(list(raw_data))

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


def get_proteome_data(experiment_id, host='localhost', port=27017, cpus=1):
    """Get monomer counts for all agents in a sim.
    
    Args:
        experiment_id: Experiment ID for simulation
        host: Host name of MongoDB
        port: Port of MongoDB
        cpus: Number of chunks to split aggregation into to be run in parallel
    """
    config = {
        'host': f'{host}:{port}',
        'database': 'simulations'
    }
    emitter = DatabaseEmitter(config)
    db = emitter.db

    aggregation = [
        {'$match': {'experiment_id': experiment_id}},
        {
            '$project': {
                'data.agents': {
                    '$objectToArray': {
                        # Add fail-safe for sims with no live agents
                        '$ifNull': ['$data.agents', {}]
                    }
                },
                'data.time': 1,
                'assembly_id': 1,
            }
        },
        {
            '$project': {
                'data.agents.v.listeners.monomer_counts': 1,
                'data.agents.k': 1,
                'data.time': 1,
                'assembly_id': 1
            }
        },
        {
            '$project': {
                'data.agents': {'$arrayToObject': '$data.agents'},
                'data.time': 1,
                'assembly_id': 1,
            }
        }
    ]

    if cpus > 1:
        start_time = MinKey()
        end_time = MaxKey()
        chunks = get_data_chunks(
            db.history, experiment_id, start_time, end_time, cpus)
        aggregations = []
        for chunk in chunks:
            agg_chunk = copy.deepcopy(aggregation)
            agg_chunk[0]['$match'] = {
                'experiment_id': experiment_id,
                '_id': {'$gte': chunk[0], '$lt': chunk[1]}
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


def get_transcriptome_data(experiment_id, host='localhost', port=27017, cpus=1):
    """Get mRNA counts for all agents in a sim.
    
    Args:
        experiment_id: Experiment ID for simulation
        host: Host name of MongoDB
        port: Port of MongoDB
        cpus: Number of chunks to split aggregation into to be run in parallel
    """
    config = {
        'host': f'{host}:{port}',
        'database': 'simulations'
    }
    emitter = DatabaseEmitter(config)
    db = emitter.db

    aggregation = [
        {'$match': {'experiment_id': experiment_id}},
        {
            '$project': {
                'data.agents': {
                    '$objectToArray': {
                        # Add fail-safe for sims with no live agents
                        '$ifNull': ['$data.agents', {}]
                    }
                },
                'data.time': 1,
                'assembly_id': 1,
            }
        },
        {
            '$project': {
                'data.agents.v.listeners.RNA_counts.mRNA_counts': 1,
                'data.agents.k': 1,
                'data.time': 1,
                'assembly_id': 1
            }
        },
        {
            '$project': {
                'data.agents': {'$arrayToObject': '$data.agents'},
                'data.time': 1,
                'assembly_id': 1,
            }
        }
    ]

    if cpus > 1:
        start_time = MinKey()
        end_time = MaxKey()
        chunks = get_data_chunks(
            db.history, experiment_id, start_time, end_time, cpus)
        aggregations = []
        for chunk in chunks:
            agg_chunk = copy.deepcopy(aggregation)
            agg_chunk[0]['$match'] = {
                'experiment_id': experiment_id,
                '_id': {'$gte': chunk[0], '$lt': chunk[1]}
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


def get_gene_expression_data(experiment_id, host='localhost', port=27017, cpus=1):
    """Get expression events for each mRNAs at each timestep for each agent.
    
    Args:
        experiment_id: Experiment ID for simulation
        host: Host name of MongoDB
        port: Port of MongoDB
        cpus: Number of chunks to split aggregation into to be run in parallel
    """
    config = {
        'host': f'{host}:{port}',
        'database': 'simulations'
    }
    emitter = DatabaseEmitter(config)
    db = emitter.db

    aggregation = [
        {'$match': {'experiment_id': experiment_id}},
        {
            '$project': {
                'data.agents': {
                    '$objectToArray': {
                        # Add fail-safe for sims with no live agents
                        '$ifNull': ['$data.agents', {}]
                    }
                },
                'data.time': 1,
                'assembly_id': 1,
            }
        },
        {
            '$project': {
                'data.agents.v.listeners.transcript_elongation_listener.countRnaSynthesized': 1,
                'data.agents.k': 1,
                'data.time': 1,
                'assembly_id': 1
            }
        },
        {
            '$project': {
                'data.agents': {'$arrayToObject': '$data.agents'},
                'data.time': 1,
                'assembly_id': 1,
            }
        }
    ]

    if cpus > 1:
        start_time = MinKey()
        end_time = MaxKey()
        chunks = get_data_chunks(
            db.history, experiment_id, start_time, end_time, cpus)
        aggregations = []
        for chunk in chunks:
            agg_chunk = copy.deepcopy(aggregation)
            agg_chunk[0]['$match'] = {
                'experiment_id': experiment_id,
                '_id': {'$gte': chunk[0], '$lt': chunk[1]}
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


def get_fluxome_data(experiment_id, host='localhost', port=27017, cpus=1):
    """Get central carbon metabolism fluxes for all agents in a sim.
    
    Args:
        experiment_id: Experiment ID for simulation
        host: Host name of MongoDB
        port: Port of MongoDB
        cpus: Number of chunks to split aggregation into to be run in parallel
    """
    config = {
        'host': f'{host}:{port}',
        'database': 'simulations'
    }
    emitter = DatabaseEmitter(config)
    db = emitter.db
    
    rxn_ids = get_toya_flux_rxns(SIM_DATA_PATH)
    sim_rxn_indices = [int(i) for i in itertools.chain.from_iterable(list(rxn_ids.values()))]

    aggregation = [
        {'$match': {'experiment_id': experiment_id}},
        {
            '$project': {
                'data.agents': {
                    '$objectToArray': {
                        # Add fail-safe for sims with no live agents
                        '$ifNull': ['$data.agents', {}]
                    }
                },
                'data.time': 1,
                'data.fields': 1,
                'data.dimensions': 1,
                'assembly_id': 1,
            }
        }
    ]
    projection = {
        '$project': {
            f'data.agents.v.fluxome.{rxn_index}':
                val_at_idx_in_path(
                    rxn_index, 
                    "data.agents.v.listeners.fba_results.reactionFluxes"
                )
            for rxn_index in sim_rxn_indices
        }
    }

    projection['$project']['data.agents.k'] = 1
    projection['$project']['data.time'] = 1
    projection['$project']['assembly_id'] = 1
    aggregation.append(projection)

    final_projection = {'$project': {
        'data.agents': {'$arrayToObject': '$data.agents'},
        'data.time': 1,
        'assembly_id': 1,
    }}
    aggregation.append(final_projection)

    if cpus > 1:
        start_time = MinKey()
        end_time = MaxKey()
        chunks = get_data_chunks(
            db.history, experiment_id, start_time, end_time, cpus)
        aggregations = []
        for chunk in chunks:
            agg_chunk = copy.deepcopy(aggregation)
            agg_chunk[0]['$match'] = {
                'experiment_id': experiment_id,
                '_id': {'$gte': chunk[0], '$lt': chunk[1]}
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
