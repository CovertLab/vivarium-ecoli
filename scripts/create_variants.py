import argparse
import os
import json
import pickle
from typing import Any, TYPE_CHECKING

import numpy as np

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.variants import VARIANT_REGISTRY
from ecoli.experiments.ecoli_master_sim import SimConfig

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli


def parse_variants(sim_data: 'SimulationDataEcoli', 
    variant_config: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any], 'SimulationDataEcoli']:
    """
    Parse variants and parameters specified under ``variants`` key of config.
    Variant names are used to pull the corresponding variant function from
    :py:data:`~ecoli.variant.VARIANT_REGISTRY`. If multiple variants names are
    provided, variant functions will be called in the order specified. Take the
    following ``variant_config``::

        {
            'var_1': {
                'a': {'value': [1, 2]},
                'c': {
                    'nested': {
                        'd': {'value': [3, 4]},
                        'e': {'value': [5, 6]},
                        'op': '+'
                    }
                },
                'op': 'x'
            }
        }
    
    This will generate 4 ``sim_data`` objects:

        1. ``var_1(sim_data, {'a': 1, 'c': {'d': 3, 'e': 5}})``
        2. ``var_1(sim_data, {'a': 1, 'c': {'d': 4, 'e': 6}})``
        3. ``var_1(sim_data, {'a': 2, 'c': {'d': 3, 'e': 5}})``
        4. ``var_1(sim_data, {'a': 2, 'c': {'d': 4, 'e': 6}})``

    Args:
        sim_data: Base ``sim_data`` object to modify
        variant_config: Dictionary of the form::
            {
                # Name of variant as defined in ecoli/variants/__init__.py
                'variant_name': {
                    # Define one or more parameters with any names EXCEPT `op`
                    'param_name': {
                        # Each parameter defines only ONE of the following keys
                        # A list of parameter values
                        'value': [...] 
                        # Numpy function that returns array of parameter values
                        # Example: np.linspace
                        'linspace': { 
                            # Kwargs
                            'start': ...,
                            'stop': ...,
                            'num' (optional): ...
                        }
                        # Dictionary of parameters with same rules as this one
                        'nested': {
                            'inner_param_name': {'value': [...]}, 
                            # Can nest as deeply as you desire
                            'another_inner_param_name': {'nested': {...}},
                            # Special key that can take on one of two values:
                            # '+': Zip parameters (must have same length)
                            # 'x': Cartesian product of parameters
                            'op': '+'
                        }
                    },
                    # Any time more than one parameter is defined at a given level
                    # in the config, an 'op' key MUST define how to combine them
                    ...
                }
            }

    Returns:
        List of tuples where the first value is a metadata dictionary of
        variant names to parameter value and the second value is the
        ``sim_data`` object generated from those parameters::

            ({'variant_1': {'param_1': val_1, ...}, ...}, sim_data)

    """
    sim_data_variants = [({}, sim_data)]
    variant_combos = variant_config.pop('combo', False)
    for variant_name, variant_params in variant_config.items():
        # Perform pre-processing of parameters
        parsed = {}
        combo = variant_params.get('combo', False)
        for param_name, param_conf in variant_params['params'].items():
            if len(param_conf) > 1:
                raise RuntimeError(f'Parameter {param_name} for '
                                   f'{variant_name} has >1 type.')
            param_type = list(param_conf.keys())[0]
            if param_type == 'value':
                parsed[param_name] = param_conf['value']
            else:
                try:
                    np_func = getattr(np, param_type)
                except AttributeError as e:
                    raise RuntimeError(f'Parameter {param_name} for '
                        f'{variant_name} is unrecognized type {param_type}.'
                        ) from e
                parsed[param_name] = np_func(**param_conf['arange'])
        # Compile parameter combinations
        if combo:
            param_combos = np.array(np.meshgrid(
                *(vals for vals in parsed.values()), indexing='ij')
            ).T.reshape(-1, len(parsed))
            param_combos = [
                {name: val for name, val in zip(parsed.keys(), param_arr)}
                for param_arr in param_combos]
        else:
            param_combos = []
            n_combos = -1
            for name, val in parsed.items():
                if n_combos == -1:
                    n_combos = len(val)
                if len(val) != n_combos:
                    raise RuntimeError('At least one parameter for '
                                       f'{variant_name} has a different # of '
                                       f' values than {name}.')
                param_combos.append({name: val[i]} for i in range(n_combos))
        # Generate variant sim_data objects
        variant_func = VARIANT_REGISTRY[variant_name]
        generated_variants = []
        for i, (metadata, variant_sim_data) in enumerate(sim_data_variants):
            if variant_combos:
                for params in param_combos:
                    new_metadata = {**metadata, variant_name: params}
                    new_sim_data = variant_func(variant_sim_data, params)
                    generated_variants.append((new_metadata, new_sim_data))
                else:
                    new_metadata = {**metadata, variant_name: param_combos[i]}
                    new_sim_data = variant_func(variant_sim_data, param_combos[i])
                    generated_variants.append((new_metadata, new_sim_data))
        sim_data_variants = generated_variants
    return sim_data_variants


def main():
    parser = argparse.ArgumentParser()
    default_config = os.path.join(CONFIG_DIR_PATH, 'default.json')
    parser.add_argument(
        '--config', '-c', action='store',
        default=default_config,
        help=(
            'Path to configuration file for the simulation. '
            'All key-value pairs in this file will be applied on top '
            f'of the options defined in {default_config}.'))
    parser.add_argument(
        '--kb', action='store', type=str,
        help='Path to kb folder generated by ParCa. Used by '
            'scripts/create_variants.py')
    parser.add_argument(
        '--outdir', '-o', action='store', type=str,
        help='Path to folder where variant sim_data and metadata are written.')
    config = SimConfig(parser=parser)
    config.update_from_cli()

    with open(os.path.join(config['kb'], 'simData.cPickle'), 'rb') as f:
        sim_data = pickle.load(f)
    sim_data_variants = parse_variants(sim_data, config.get('variants', {}))
    os.makedirs(config['outdir'], exist_ok=True)

    metadata = {}
    for i, (variant_config, variant_sim_data) in enumerate(sim_data_variants):
        variant_name = '_'.join(list(variant_config.keys()) + [i])
        metadata[variant_name] = variant_config
        out_path = os.path.join(config['outdir'], f'{variant_name}.cPickle')
        with open(out_path, 'wb') as f:
            pickle.dump(variant_sim_data, f)
    with open(os.path.join(config['outdir'], 'metadata.json'), 'w') as f:
        json.dump(metadata, f)


if __name__ == '__main__':
    main()
