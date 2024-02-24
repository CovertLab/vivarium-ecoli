import argparse
from pathlib import Path
import itertools
import os
import json
import pickle
import shutil
import subprocess
from typing import Any, TYPE_CHECKING

import numpy as np

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.variants import VARIANT_REGISTRY
from ecoli.experiments.ecoli_master_sim import SimConfig

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli


def parse_variants(variant_config: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
    """
    Parse parameters for a variant specified under ``variants`` key of config. 
    See :py:func:`~.test_parse_variants` for an sample ``variant_config`` and 
    what it gets parsed into.

    Args:
        variant_config: Dictionary of the form::
            
            {
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
                    'nested': {...}
                },
                # When more than one parameter is defined, an 'op' key 
                # MUST define how to combine them. The two methods are:
                # 'zip': Zip parameters (must have same length)
                # 'prod': Cartesian product of parameters
                'param_2': {...},
                'op': 'zip'
            }

    Returns:
        List of parameter dictionaries generated from variant config

    """
    # Extract operation if more than one parameter
    operation = None
    if len(variant_config) > 1:
        operation = variant_config.pop('op')

    # Perform pre-processing of parameters
    parsed = {}
    for param_name, param_conf in variant_config.items():
        if len(param_conf) > 1:
            raise TypeError(f'{param_name} should only have 1 type.')
        param_type = list(param_conf.keys())[0]
        param_vals = param_conf[param_type]
        if param_type == 'value':
            if not isinstance(param_vals, list):
                raise TypeError(f'{param_name} should have a list value.')
            parsed[param_name] = param_vals
        elif param_type == 'nested':
            parsed[param_name] = parse_variants(param_vals)
        else:
            try:
                np_func = getattr(np, param_type)
            except AttributeError as e:
                raise TypeError(f'{param_name} is unknown type {param_type}.'
                    ) from e
            parsed[param_name] = np_func(**param_vals)

    # Apply parameter operations
    if operation == 'prod':
        param_tuples = itertools.product(*(parsed[k] for k in parsed))
        param_dicts = [
            {name: val for name, val in zip(parsed.keys(), param_tuple)}
            for param_tuple in param_tuples
        ]
    elif operation == 'zip':
        n_combos = -1
        for name, val in parsed.items():
            if n_combos == -1:
                n_combos = len(val)
            if len(val) != n_combos:
                raise RuntimeError(f'At least 1 other parameter has a '
                                   f'different # of values than {name}.')
        param_dicts = [{name: val[i] for name, val in parsed.items()}
                       for i in range(n_combos)]
    elif operation is None:
        param_name = list(parsed.keys())[0]
        param_vals = parsed[param_name]
        param_dicts = [{param_name: param_val} for param_val in param_vals]
    else:
        raise RuntimeError(f'Unknown operation {operation} in {variant_config}')

    return param_dicts


def apply_and_save_variants(sim_data: 'SimulationDataEcoli',
                   param_dicts: list[dict[str, Any]],
                   variant_name: str,
                   outdir: str
                   ):
    """
    Applies variant function to ``sim_data`` with each parameter dictionary 
    in ``param_dicts``. Saves each variant as ``variant_name_{i}`` 
    in ``outdir``, where ``i`` is the index of the parameter dictionary in 
    ``param_dicts`` used to create that variant. Also saves ``metadata.json`` 
    in ``outdir`` that maps each ``variant_name_{i}`` to the parameter 
    dictionary used to create it.

    Args:
        sim_data: Simulation data object to modify
        param_dicts: Return vale of :py:func:`~.parse_variants`
        variant_name: Name of variant function in 
            :py:data:`~ecoli.variant.VARIANT_REGISTRY`
        outdir: Path to folder where variant ``sim_data`` pickles are saved
    """
    variant_func = VARIANT_REGISTRY[variant_name]
    variant_metadata = {}
    for i, params in enumerate(param_dicts):
        outname = f'{variant_name}_{i}'
        variant_metadata[outname] = params
        variant_sim_data = variant_func(sim_data, params)
        outpath = os.path.join(outdir, f'{outname}.cPickle')
        with open(outpath, 'wb') as f:
            pickle.dump(variant_sim_data, f)
    with open(os.path.join(outdir, 'metadata.json'), 'w') as f:
        json.dump(variant_metadata, f)


def test_parse_variants():
    """
    Test variant parameter parsing.
    """
    variant_config = {
        'a': {'value': [1, 2]},
        'b': {'value': ['one', 'two']},
        'c': {
            'nested': {
                'd': {'value': [3, 4]},
                'e': {'value': [5, 6]},
                'op': 'zip'
            }
        },
        'op': 'prod'
    }
    parsed_params = parse_variants(variant_config)
    assert set(parsed_params) == {
        {"a": 1, "b": "one", "c": {"d": 3, "e": 5.0}},
        {"a": 1, "b": "one", "c": {"d": 4, "e": 6.0}},
        {"a": 1, "b": "two", "c": {"d": 3, "e": 5.0}},
        {"a": 1, "b": "two", "c": {"d": 4, "e": 6.0}},
        {"a": 2, "b": "one", "c": {"d": 3, "e": 5.0}},
        {"a": 2, "b": "one", "c": {"d": 4, "e": 6.0}},
        {"a": 2, "b": "two", "c": {"d": 3, "e": 5.0}},
        {"a": 2, "b": "two", "c": {"d": 4, "e": 6.0}}
    }

class SimData():
    """
    Mock sim_data class for testing.
    """    
    pass

def test_create_variants():
    """
    Test modification and saving of variant sim_data.
    """
    try:
        os.makedirs('test_create_variants/kb', exist_ok=True)
        # Create mock sim_data pickle
        with open('test_create_variants/kb/simData.cPickle', 'wb') as f:
            pickle.dump(SimData(), f)
        # Test script and config system
        subprocess.run([
            'python', 'scripts/create_variants.py', 
            '-c', 'ecoli/composites/ecoli_configs/test_variant.json', 
            '--kb', 'test_create_variants/kb', 
            '-o', 'test_create_variants/out'], check=True)
        # Check that metadata aligns with variant sim_data attrs
        with open('test_create_variants/out/metadata.json') as f:
            variant_metadata = json.load(f)
        out_path = Path('test_create_variants/out')
        var_paths = out_path.glob('*.cPickle')
        for var_path in var_paths:
            with open(var_path, 'rb') as f:
                variant_sim_data = pickle.load(f)
            variant_params = variant_metadata[var_path.stem]
            assert variant_sim_data.a == variant_params['a']
            assert variant_sim_data.b == variant_params['b']
            assert variant_sim_data.d == variant_params['c']['d']
            assert variant_sim_data.e == variant_params['c']['e']
    finally:
        shutil.rmtree('test_create_variants', ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    default_config = os.path.join(CONFIG_DIR_PATH, 'default.json')
    parser.add_argument(
        '--config', '-c', action='store',
        default=default_config,
        help=(
            'Path to configuration file for the simulation. '
            'All key-value pairs in this file will be applied on top '
            f'of the options defined in {default_config}. To configure '
            'variants, the config must include the `variant` key. Under the '
            '`variant` key should be a single key with the name of the '
            'variant function (see ecoli/variants/__init__.py). Under the '
            'variant function key should be a parameter dictionary as '
            'described in the docstring for `parse_variants`.'))
    parser.add_argument(
        '--kb', action='store', type=str,
        help='Path to kb folder generated by ParCa.')
    parser.add_argument(
        '--outdir', '-o', action='store', type=str,
        help='Path to folder where variant sim_data and metadata are written.')
    config = SimConfig(parser=parser)
    config.update_from_cli()

    variant_config = config.get('variants', {})
    if len(variant_config) > 1:
        raise RuntimeError('Only one variant name allowed. Variants can '
                           'be manually composed in Python by having one '
                           'variant function internally call another.')
    variant_name = list(variant_config.keys())[0]
    variant_params = variant_config[variant_name]
    print('Parsing variants...')
    parsed_params = parse_variants(variant_params)
    print('Loading sim_data...')
    with open(os.path.join(config['kb'], 'simData.cPickle'), 'rb') as f:
        sim_data = pickle.load(f)
    os.makedirs(config['outdir'], exist_ok=True)
    print('Applying variants and saving variant sim_data...')
    apply_and_save_variants(sim_data, parsed_params,
                            variant_name, config['outdir'])
    print('Done.')


if __name__ == '__main__':
    main()
