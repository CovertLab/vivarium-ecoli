"""
Interface for configuring and running **single-cell** E. coli simulations.

.. note::
    Simulations can be configured to divide through this interface, but 
    full colony-scale simulations are best run using the 
    :py:mod:`~ecoli.composites.ecoli_engine_process` module for efficient 
    multithreading.
"""

import argparse
import copy
import os
import pstats
import subprocess
import json
import warnings
from datetime import datetime
from typing import Optional, Dict, Any

import numpy as np
from vivarium.core.engine import Engine
from vivarium.core.process import Process
from vivarium.core.serialize import deserialize_value, serialize_value
from vivarium.library.dict_utils import deep_merge, deep_merge_check
from vivarium.library.topology import inverse_topology
from vivarium.library.topology import assoc_path
from ecoli.library.logging_tools import write_json
import ecoli.composites.ecoli_master
# Environment composer for spatial environment sim
import ecoli.composites.environment.lattice

from ecoli.processes import process_registry
from ecoli.processes.registries import topology_registry

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.library.schema import not_a_process


def tuplify_topology(topology: dict[str, Any]) -> dict[str, Any]:
    """JSON files allow lists but do not allow tuples. This function 
    transforms the list paths in topologies loaded from JSON into 
    standard tuple paths.
    
    Args:
        topology: Topology to recursively iterate over, converting 
            all paths to tuples
    
    Returns:
        Topology with tuple paths (e.g. ``['bulk']`` turns into ``('bulk',)``)
    """
    tuplified_topology = {}
    for k, v in topology.items():
        if isinstance(v, dict):
            tuplified_topology[k] = tuplify_topology(v)
        elif isinstance(v, str):
            tuplified_topology[k] = (v,)
        else:
            tuplified_topology[k] = tuple(v)
    return tuplified_topology


def get_git_revision_hash() -> str:
    """Returns current Git hash for model repository to include in metadata 
    that is emitted when starting a simulation."""
    return subprocess.check_output(
        ['git', 'rev-parse', 'HEAD']).decode('ascii').strip()


def get_git_status() -> str:
    """Returns Git status of model repository to include in metadata that is 
    emitted when starting a simulation.
    """
    status_str = subprocess.check_output(
        ['git', 'status', '--porcelain']).decode('ascii').strip()
    status = status_str.split('\n')
    return status


def report_profiling(stats: pstats.Stats) -> None:
    """Prints out a summary of profiling statistics when ``profile`` option 
    is ``True`` in the config given to 
    :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim`
    
    Args:
        stats: Profiling statistics."""
    _, stats_keys = stats.get_print_list(
        ('(next_update)|(calculate_request)|(evolve_state)',))
    summed_stats = {}
    for key in stats_keys:
        key_stats = stats.stats[key]
        _, _, _, cumtime, _ = key_stats
        path, line, func = key
        path = os.path.basename(path)
        summed_stats[(path, line, func)] = summed_stats.get(
            (path, func), 0) + cumtime
    summed_stats_inverse_map = {
        time: key for key, time in summed_stats.items()
    }
    print('\nPer-process profiling:\n')
    for time in sorted(summed_stats_inverse_map.keys())[::-1]:
        path, line, func = summed_stats_inverse_map[time]
        print(f'{path}:{line} {func}(): {time}')
    print('\nOverall Profile:\n')
    stats.sort_stats('cumtime').print_stats(20)


def key_value_pair(argument_string: str) -> tuple[str, str]:
    """Parses key-value pairs specified as strings of the form ``key=value`` 
    via CLI. See ``emitter_arg`` option in 
    :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig`.

    Args:
        argument_string: Key-value pair as a string of the form ``key=value``

    Returns:
        ``[key, value]``
    """
    split = argument_string.split('=')
    if len(split) != 2:
        raise ValueError(
            'Key-value pair arguments must have exactly one `=`.')
    return split


class SimConfig:

    default_config_path = os.path.join(CONFIG_DIR_PATH, 'default.json')

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        '''Stores configuration options for a simulation. Has dictionary-like 
        interface (e.g. bracket indexing, get, keys).

        Attributes:
            config: Current configuration.
            parser: Argument parser for the command-line interface.

        Args:
            config: Configuration options. If not provided, the default
                configuration is loaded from the file path 
                :py:data:`~ecoli.experiments.ecoli_master_sim.SimConfig.default_config_path`.
        '''
        self._config = config or {}
        if not self._config:
            self.update_from_json(self.default_config_path)

        self.parser = argparse.ArgumentParser(description='ecoli_master')
        self.parser.add_argument(
            '--config', '-c', action='store',
            default=self.default_config_path,
            help=(
                'Path to configuration file for the simulation. '
                'All key-value pairs in this file will be applied on top '
                f'of the options defined in {self.default_config_path}.'))
        self.parser.add_argument(
            '--experiment_id', '-id', action="store",
            help=(
                'ID for this experiment. A UUID will be generated if '
                'this argument is not used and "experiment_id" is null '
                'in the configuration file.'))
        self.parser.add_argument(
            '--emitter', '-e', action='store',
            choices=["timeseries", "database", "print", "null",
                "shared_ram"],
            help=(
                "Emitter to use. Timeseries uses RAMEmitter, database "
                "emits to MongoDB, and print emits to stdout."))
        self.parser.add_argument(
            '--emitter_arg', '-ea', action='store', nargs='*',
            type=key_value_pair,
            help=(
                'Key-value pairs, separated by `=`, to include in '
                'emitter config.'))
        self.parser.add_argument(
            '--seed', '-s', action="store", type=int,
            help="Random seed.")
        self.parser.add_argument(
            '--initial_state', '-t0', action="store",
            help=(
                "Name of the initial state to load from (corresponding "
                "initial state file must be present in data folder)."))
        self.parser.add_argument(
            '--total_time', '-t', action="store", type=float,
            help="Time to run the simulation for.")
        self.parser.add_argument(
            '--generations', '-g', action="store", type=int,
            help="Number of generations to run the simulation for.")
        self.parser.add_argument(
            '--log_updates', '-u', action="store_true",
            help=(
                "Save updates from each process if this flag is set, "
                "e.g. for use with blame plot."))
        self.parser.add_argument(
            '--raw_output', action="store_true",
            help=(
                "Whether to return data in raw format (dictionary "
                "where keys are times, values are states)."))
        self.parser.add_argument(
            "--agent_id", action="store", type=str,
            help="Agent ID.")
        self.parser.add_argument(
            '--sim_data_path', nargs="*", default=None,
            help="Path to the sim_data to use for this experiment.")
        self.parser.add_argument(
            '--parallel', action='store_true', default=False,
            help='Run processes in parallel.')
        self.parser.add_argument(
            '--profile', action='store_true', default=False,
            help='Print profiling information at the end.')
        self.parser.add_argument(
            '--initial_state_file', action='store',
            default='',
            help='Name of initial state file (no ".json") under data/')
        self.parser.add_argument(
            '--initial_state_overrides', action='store', nargs='*',
            help='Name of initial state overrides (no ".json") under '
                'data/overrides')

    @staticmethod
    def merge_config_dicts(d1: dict[str, Any], d2: dict[str, Any]) -> None:
        """Helper function to safely merge two config dictionaries. Config 
        options whose values are lists (e.g. ``save_times``, ``add_processes``, 
        etc.) are handled separately so that the lists from each config are 
        concatenated in the merged output.

        Args:
            d1: Config to mutate by merging in ``d2``.
            d2: Config to merge into ``d1``.
        """
        # Handle config keys that need special handling.
        LIST_KEYS_TO_MERGE = (
            'save_times', 'add_processes', 'exclude_processes',
            'processes', 'engine_process_reports', 'initial_state_overrides')
        for key in LIST_KEYS_TO_MERGE:
            d2.setdefault(key, [])
            d2[key].extend(d1.get(key, []))
            if key == 'engine_process_reports':
                d2[key] = [tuple(path) for path in d2[key]]
            # Ensures there are no duplicates in d2
            d2[key] = list(set(d2[key])) 
            d2[key].sort()
        deep_merge(d1, d2)

    def update_from_json(self, path: str) -> None:
        """Loads config dictionary from file path ``path`` and merges it into 
        the currently loaded config.

        Args:
            path: The file path of the JSON config to merge in.
        """
        with open(path, 'r') as f:
            new_config = json.load(f)
        new_config = deserialize_value(new_config)
        for config_name in new_config.get('inherit_from', []):
            config_path = os.path.join(CONFIG_DIR_PATH, config_name)
            self.update_from_json(config_path)
        self.merge_config_dicts(self._config, new_config)

    def update_from_cli(self):
        """Parses command-line options defined in ``__init__`` and 
        updates config.
        """
        args = self.parser.parse_args()
        # First load in a configuration file, if one was specified.
        config_path = getattr(args, 'config', None)
        if config_path:
            self.update_from_json(config_path)
        # Then override the configuration file with any command-line
        # options.
        cli_config = {
            key: value
            for key, value in vars(args).items()
            if value and key not in (
                'config', 'parallel', 'no_parallel')
        }
        cli_config['parallel'] = args.parallel
        self.merge_config_dicts(self._config, cli_config)

    def update_from_dict(self, dict_config: dict[str, Any]):
        """Updates loaded config with user-specified dictionary."""
        self.merge_config_dicts(self._config, dict_config)

    def __getitem__(self, key):
        return self._config[key]

    def get(self, key, default=None):
        return self._config.get(key, default)

    def __setitem__(self, key, val):
        self._config[key] = val

    def keys(self):
        return self._config.keys()

    def to_dict(self):
        return copy.deepcopy(self._config)


class EcoliSim:
    def __init__(self, config: dict[str, Any]):
        """Main interface for running single-cell E. coli simulations. Typically 
        instantiated using one of two methods:

        1. :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_file`
        2. :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_cli`
        
        Config options can be modified after the creation of an 
        :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` object 
        in one of two ways.

        1. ``sim.total_time = 100``
        2. ``sim.config['total_time'] = 100``

        Args:
            config: Automatically generated from 
                :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig` when 
                :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` is 
                instantiated using 
                :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_file` 
                or :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_cli`
        """
        # Do some datatype pre-processesing
        config['agents_path'] = tuple(config['agents_path'])
        config['processes'] = {
            process: None for process in config['processes']}

        # Keep track of base experiment id
        # in case multiple simulations are run with suffix_time = True.
        self.experiment_id_base = config['experiment_id']
        self.config = config
        self.ecoli = None
        """vivarium.core.composer.Composite: Contains the fully instantiated 
        processes, steps, topologies, and flow necessary to run simulation. 
        Generated by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.build_ecoli` and 
        cleared when :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run` 
        is called to potentially free up memory after division."""
        self.generated_initial_state = None
        """dict: Fully populated initial state for simulation. Generated by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.build_ecoli` and 
        cleared when :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run` 
        is called to potentially free up memory after division."""
        self.ecoli_experiment = None
        """vivarium.core.engine.Engine: Engine that runs the simulation. 
        Instantiated by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`."""

        # Unpack config using Descriptor protocol:
        # All of the entries in config are translated to properties
        # (of EcoliSim class) that get/set an entry in self.config.
        #
        # For example:
        #
        # >> sim = EcoliSim.from_file()
        # >> sim.total_time
        #    10
        # >> sim.config['total_time']
        #    10
        # >> sim.total_time = 100
        # >> sim.config['total_time']
        #    100

        class ConfigEntry():
            def __init__(self, name):
                self.name = name

            def __get__(self, sim, type=None):
                return sim.config[self.name]

            def __set__(self, sim, value):
                sim.config[self.name] = value

        for attr in self.config.keys():
            config_entry = ConfigEntry(attr)
            setattr(EcoliSim, attr, config_entry)

        if self.generations:
            warnings.warn("generations option is not yet implemented!")

    @staticmethod
    def from_file(filepath=CONFIG_DIR_PATH + 'default.json') -> 'EcoliSim':
        """Used to instantiate 
        :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` with 
        a config loaded from the JSON at ``filepath`` by 
        :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig`.
        
        Args:
            filepath: String filepath of JSON file with config options to 
                apply on top of the options laid out in the default JSON 
                located at the default value for ``filepath``.
        """
        config = SimConfig()
        config.update_from_json(filepath)
        return EcoliSim(config.to_dict())

    @staticmethod
    def from_cli() -> 'EcoliSim':
        """Used to instantiate 
        :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` with 
        a config loaded from the command-line arguments parsed by 
        :py:class:`~ecoli.experiments.ecoli_master_sim.SimConfig`.
        """
        config = SimConfig()
        config.update_from_cli()
        return EcoliSim(config.to_dict())

    def _retrieve_processes(self,
                            processes: list[str],
                            add_processes: list[str],
                            exclude_processes: list[str],
                            swap_processes: dict[str, str],
                            ) -> dict[str, Process]:
        """
        Retrieve process classes from 
        :py:data:`~vivarium.core.registry.process_registry` (processes are
        registered in ``ecoli/processes/__init__.py``).

        Args:
            processes: Base list of process names to retrieve classes for
            add_processes: Additional process names to retrieve classes for
            exclude_processes: Process names to not retrieve classes for
            swap_processes: Mapping of process names to the names of the 
                processes they should be swapped for. It is assumed that 
                the swapped processes share the same topologies.

        Returns:
            Mapping of process names to process classes.
        """
        result = {}
        for process_name in list(processes.keys()) + list(add_processes):
            if process_name in exclude_processes:
                continue
            if process_name in swap_processes:
                process_name = swap_processes[process_name]
            process_class = process_registry.access(process_name)
            if not process_class:
                raise ValueError(f"Unknown process with name {process_name}. "
                    "Did you call process_registry.register() in "
                    "ecoli/processes/__init__.py?")
            result[process_name] = process_class

        return result

    def _retrieve_topology(self,
                           topology: dict[str, dict[str, tuple[str]]],
                           processes: list[str],
                           swap_processes: dict[str, str],
                           log_updates: bool,
                           ) -> dict[str, dict[str, tuple[str]]]:
        """
        Retrieves topologies for processes from 
        :py:data:`~ecoli.processes.registries.topology_registry`.

        Args:
            topology: Mapping of process names to user-specified topologies. 
                Will be merged with topology from topology_registry, if exists.
            processes: List of process names for which to retrive topologies.
            swap_processes: Mapping of process names to the names of processes 
                to swap them for. By default, the new processes are assumed to 
                have the same topology as the processes they replaced. When 
                this is not the case, users can add/modify the original process 
                topology with custom values in ``topology`` under either the new 
                or the old process name.
            log_updates: Whether to emit process updates. Adds topology for 
                ``log_update`` port.
        
        Returns:
            Mapping of process names to process topologies.
        """
        result = {}
        original_processes = {v: k for k, v in swap_processes.items()}
        for process in processes:
            # Start from default topology if it exists
            original_process = (process
                                if process not in swap_processes.values()
                                else original_processes[process])
            process_topology = topology_registry.access(original_process)
            if process_topology:
                process_topology = copy.deepcopy(process_topology)
            else:
                process_topology = {}
            # Allow the user to override default topology
            if original_process in topology.keys():
                deep_merge(process_topology, tuplify_topology(
                    topology[original_process]))
            # For swapped processes, do additional overrides if provided
            if process != original_process and process in topology.keys():
                deep_merge(process_topology, tuplify_topology(
                    topology[process]))
            result[process] = process_topology

        # Add log_update ports if log_updates is True
        if log_updates:
            for process, ports in result.items():
                result[process]['log_update'] = ('log_update', process,)

        return result

    def _retrieve_process_configs(self, 
                                  process_configs: dict[str, dict[str, Any]], 
                                  processes: list[str]) -> dict[str, Any]:
        """
        Sets up process configs to be interpreted by 
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps`.

        Args:
            process_configs: Mapping of process names to user-specified process 
                configuration dictionaries.
            processes: List of process names to set up process config for.
        
        Returns:
            Mapping of process names to process configs.
        """
        result = {}
        for process in processes:
            result[process] = process_configs.get(process)
            if result[process] is None:
                result[process] = "sim_data"
        return result

    def build_ecoli(self):
        """
        Creates the E. coli composite. **MUST** be called before calling 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`.

        For all processes in ``config['processes']``:

        1. Retrieves process class from 
        :py:data:`~vivarium.core.registry.process_registry`, which is 
        populated in ``ecoli/processes/__init__.py``.

        2. Retrieves process topology from 
        :py:data:`~ecoli.processes.registries.topology_registry` and merge 
        with user-specified topology from ``config['topology']``, if applicable

        3. Retrieves process configs from ``config['process_configs']`` 
        if present, else indicate that process config should be loaded from 
        pickled simulation data using 
        :py:meth:`~ecoli.library.sim_data.LoadSimData.get_config_by_name`

        Adds spatial environment if ``config['spatial_environment']`` is 
        ``True``. Spatial environment config options are loaded from 
        ``config['spatial_environment_config]``. See 
        ``ecoli/composites/ecoli_configs/spatial.json`` for an example.

        .. note::
            When loading from a saved state with a file name of the format 
            ``vivecoli_t{save time}``, the simulation seed is automatically 
            set to ``config['seed'] + {save_time}`` to prevent 
            :py:func:`~ecoli.library.schema.create_unique_indexes` from 
            generating clashing indices.
        """
        # build processes, topology, configs
        self.processes = self._retrieve_processes(
            self.processes, self.add_processes, self.exclude_processes,
            self.swap_processes)
        self.topology = self._retrieve_topology(
            self.topology, self.processes, self.swap_processes,
            self.log_updates)
        self.process_configs = self._retrieve_process_configs(
            self.process_configs, self.processes)

        # Prevent clashing unique indices by reseeding when loading
        # a saved state (assumed to have name 'vivecoli_t{save time}')
        initial_state_path = self.config.get('initial_state_file', '')
        if initial_state_path.startswith('vivecoli'):
            time_str = initial_state_path[len('vivecoli_t'):]
            seed = int(float(time_str))
            self.config['seed'] += seed

        # initialize the ecoli composer
        ecoli_composer = ecoli.composites.ecoli_master.Ecoli(
            self.config)

        # set path at which agent is initialized
        path = tuple()
        if self.divide or self.spatial_environment:
            path = ('agents', self.agent_id,)

        # get initial state
        initial_cell_state = ecoli_composer.initial_state()
        initial_cell_state = assoc_path({}, path, initial_cell_state)

        # generate the composite at the path
        self.ecoli = ecoli_composer.generate(path=path)
        # Some processes define their own initial_state methods
        # Incoporate them into the generated initial state
        self.generated_initial_state = self.ecoli.initial_state({
            'initial_state': initial_cell_state})

        # merge a lattice composite for the spatial environment
        if self.spatial_environment:
            initial_state_config = self.spatial_environment_config.get(
                'initial_state_config')
            environment_composite = \
                ecoli.composites.environment.lattice.Lattice(
                    self.spatial_environment_config).generate()
            initial_environment = environment_composite.initial_state(
                initial_state_config)
            self.ecoli.merge(environment_composite)
            self.generated_initial_state = deep_merge(
                self.generated_initial_state, initial_environment)

    def save_states(self):
        """
        Runs the simulation while saving the states of specific
        timesteps to jsons. Automatically invoked by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run` 
        if ``config['save'] == True``. State is saved as a JSON that 
        can be reloaded into a simulation as described in 
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.initial_state`.
        """
        for time in self.save_times:
            if time > self.total_time:
                raise ValueError(
                    f'Config contains save_time ({time}) > total '
                    f'time ({self.total_time})')

        for i in range(len(self.save_times)):
            if i == 0:
                time_to_next_save = self.save_times[i]
            else:
                time_to_next_save = self.save_times[i] - self.save_times[i-1]
            self.ecoli_experiment.update(time_to_next_save)
            time_elapsed = self.save_times[i]
            state = self.ecoli_experiment.state.get_value(
                condition=not_a_process)
            if self.divide:
                for agent_state in state['agents'].values():
                    # Processes can't be serialized
                    del agent_state['process']
                    # Bulk random state can't be serialized
                    del agent_state['allocator_rng']
                    # Save bulk and unique dtypes
                    agent_state['bulk_dtypes'] = str(agent_state['bulk'].dtype)
                    agent_state['unique_dtypes'] = {}
                    for name, mols in agent_state['unique'].items():
                        agent_state['unique_dtypes'][name] = str(mols.dtype)
            else:
                del state['process']
                del state['allocator_rng']
                state['bulk_dtypes'] = str(state['bulk'].dtype)
                state['unique_dtypes'] = {}
                for name, mols in state['unique'].items():
                    state['unique_dtypes'][name] = str(mols.dtype)
            write_json('data/vivecoli_t' + str(time_elapsed) + '.json', state)
            print('Finished saving the state at t = ' + str(time_elapsed))
        time_remaining = self.total_time - self.save_times[-1]
        if time_remaining:
            self.ecoli_experiment.update(time_remaining)

    def run(self):
        """Create and run an EcoliSim experiment.
        
        .. WARNING::
            Run :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.build_ecoli` 
            before calling :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.run`!
        """
        if self.ecoli is None:
            raise RuntimeError("Build the composite by calling build_ecoli() \
                before calling run().")

        metadata = self.get_metadata()
        metadata['output_metadata'] = self.get_output_metadata()
        # make the experiment
        emitter_config = {'type': self.emitter}
        for key, value in self.emitter_arg:
            emitter_config[key] = value
        experiment_config = {
            'description': self.description,
            'metadata': metadata,
            'processes': self.ecoli.processes,
            'steps': self.ecoli.steps,
            'flow': self.ecoli.flow,
            'topology': self.ecoli.topology,
            'initial_state': self.generated_initial_state,
            'progress_bar': self.progress_bar,
            'emit_topology': self.emit_topology,
            'emit_processes': self.emit_processes,
            'emit_config': self.emit_config,
            'emitter': self.emitter,
        }
        if self.experiment_id:
            # Store backup of base experiment ID,
            # in case multiple experiments are run in a row
            # with suffix_time = True.
            if not self.experiment_id_base:
                self.experiment_id_base = self.experiment_id
            if self.suffix_time:
                self.experiment_id = datetime.now().strftime(
                    f"{self.experiment_id_base}_%d/%m/%Y %H:%M:%S")
            experiment_config['experiment_id'] = self.experiment_id
        experiment_config['profile'] = self.profile

        # Since unique numpy updater is an class method, internal
        # deepcopying in vivarium-core causes this warning to appear
        warnings.filterwarnings("ignore",
            message="Incompatible schema assignment at ")
        self.ecoli_experiment = Engine(**experiment_config)

        # Only emit designated stores if specified
        if self.config['emit_paths']:
            self.ecoli_experiment.state.set_emit_values([tuple()], False)
            self.ecoli_experiment.state.set_emit_values(
                self.config['emit_paths'],
                True,
            )
        
        # Clean up unnecessary references
        self.generated_initial_state = None
        self.ecoli_experiment.initial_state = None
        del metadata, experiment_config, emitter_config
        self.ecoli = None

        # run the experiment
        if self.save:
            self.save_states()
        else:
            self.ecoli_experiment.update(self.total_time)
        self.ecoli_experiment.end()
        if self.profile:
            report_profiling(self.ecoli_experiment.stats)

    def query(self, query: list[tuple[str]]=None):
        """
        Query emitted data.

        Args:
            query: List of tuple-style paths in the simulation state to 
                retrieve emitted values for. Returns all emitted data 
                if ``None``.
        
        Returns:
            Dictionary of emitted data in one of two forms. 
            
            * Raw data (if ``self.raw_output``): Data is keyed by time 
              (e.g. ``{0: {'data': ...}, 1: {'data': ...}, ...}``)
            
            * Timeseries: Data is reorganized to match the structure of the 
              simulation state. Leaf values in the returned dictionary are 
              lists of the simulation state value over time (e.g. 
              ``{'data': [..., ..., ...]}``).
        """
        # Retrieve queried data (all if not specified)
        if self.raw_output:
            return self.ecoli_experiment.emitter.get_data(query)
        else:
            return self.ecoli_experiment.emitter.get_timeseries(query)

    def merge(self, other: 'EcoliSim'):
        """
        Combine settings from this EcoliSim with another, overriding
        current settings with those from the other EcoliSim.

        Args:
            other: Simulation with settings to override current simulation.
        """
        deep_merge(self.config, other.config)

    def get_metadata(self) -> dict[str, Any]:
        """
        Compiles all simulation settings, git hash, and process list into a single 
        dictionary. Called by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.to_json_string`.
        """
        # create metadata of this experiment to be emitted,
        # namely the config of this EcoliSim object
        # with an additional key for the current git hash.
        # Goal is to save enough information to reproduce the experiment.
        metadata = dict(self.config)
        try:
            metadata["git_hash"] = get_git_revision_hash()
        except:
            warnings.warn("Unable to retrieve current git revision hash. "
                "Try making a note of this manually if your experiment may "
                "need to be replicated.")
        metadata['processes'] = [k for k in metadata['processes'].keys()]
        return metadata

    def to_json_string(self) -> str:
        """
        Serializes simulation setting dictionary along with git hash and 
        final list of process names. Called by 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.export_json`.
        
        """
        return str(serialize_value(self.get_metadata()))
    
    def get_output_metadata(self) -> dict[str, Any]:
        """
        Filters all ports schemas to include only output metadata
        located at the path ('_properties', 'metadata') for each schema.
        See :py:meth:`~ecoli.library.schema.listener_schema` for usage details.
        """
        if self.divide:
            processes_and_steps = self.ecoli.processes['agents'][self.agent_id]
            processes_and_steps.update(
                self.ecoli.steps['agents'][self.agent_id])
            topologies = self.ecoli.topology['agents'][self.agent_id]
        else:
            processes_and_steps = self.ecoli.processes
            processes_and_steps.update(self.ecoli.steps)
            topologies = self.ecoli.topology
        output_metadata = {}
        for proc_name, proc in processes_and_steps.items():
            proc_ports_schema = proc.get_schema()
            extracted = extract_metadata(proc_ports_schema)
            if extracted:
                extracted = inverse_topology((), extracted, topologies[proc_name])
                output_metadata = deep_merge_check(
                    output_metadata, extracted, check_equality=True)
        if self.divide:
            output_metadata = {'agents': {self.agent_id: output_metadata}}
        return output_metadata

    def export_json(self, filename: str=CONFIG_DIR_PATH + "export.json"):
        """
        Saves current simulation settings along with git hash and final list 
        of process names as a JSON that can be reloaded using 
        :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.from_file`.

        Args:
            filename: Filepath and name for saved JSON (include ``.json``).
        """
        with open(filename, 'w') as f:
            f.write(self.to_json_string())


def extract_metadata(ports_schema: dict[str, Any], properties: bool=False):
    """
    Filters ports schema to contain only a mapping of ports to user-supplied
    metadata (pulled from path `('_properties', 'metadata')` for each schema).
    See :py:meth:`~ecoli.library.schema.listener_schema` for usage details.

    Args:
        ports_schema: Ports schema to filter and compile metadata for
        properties: Flag used internally during recursive filtering
    Returns:
        Dictionary with same structure as ports schema but with only metadata
        as leaf nodes instead of complete schema
    """
    extracted = {}

    if '_properties' in ports_schema and isinstance(
            ports_schema['_properties'], dict):
        return extract_metadata(ports_schema['_properties'], True)
    
    if properties and 'metadata' in ports_schema:
        metadata = ports_schema['metadata']
        if isinstance(metadata, np.ndarray):
            metadata = metadata.tolist()
        return metadata

    for port, schema in ports_schema.items():
        if isinstance(schema, dict):
            subextracted = extract_metadata(schema)
            if subextracted is not None:
                extracted[port] = subextracted
    
    return extracted or None


def main():
    """
    Runs a simulation with CLI options.

    .. note::
        Parallelizing processes (e.g. ``--parallel``) is not recommended 
        because the overhead outweighs any performance advantage. This could 
        change in the future if a computationally intensive process is 
        introduced in the future that makes the model faster to run in parallel 
        instead of sequentially.
    """
    import multiprocessing; multiprocessing.set_start_method('spawn')
    ecoli_sim = EcoliSim.from_cli()
    ecoli_sim.build_ecoli()
    ecoli_sim.run()


# python ecoli/experiments/ecoli_master_sim.py
if __name__ == '__main__':
    main()
