"""
:py:class:`~vivarium.core.composer.Composer` used to generate the processes, 
steps, topology, and initial state of the E. coli whole cell model. 

.. note::
    Use the :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` interface 
    to configure and run simulations with this composer. 
"""

from copy import deepcopy
import os
from typing import Any
import warnings

# vivarium-core
from vivarium.core.composer import Composer
from vivarium.plots.topology import plot_topology
from vivarium.library.dict_utils import deep_merge
from vivarium.core.control import run_library_cli
from vivarium.core.engine import _StepGraph

# sim data
from ecoli.library.sim_data import LoadSimData, RAND_MAX

# logging
from ecoli.library.logging_tools import make_logging_process

# vivarium-ecoli processes
from ecoli.composites.ecoli_configs import (
    ECOLI_DEFAULT_PROCESSES, ECOLI_DEFAULT_TOPOLOGY)
from ecoli.plots.topology import get_ecoli_partition_topology_settings
from ecoli.processes.cell_division import Division, MarkDPeriod
from ecoli.processes.allocator import Allocator
from ecoli.processes.partition import PartitionedProcess
from ecoli.processes.unique_update import UniqueUpdate

# state
from ecoli.processes.partition import Requester, Evolver, Step, Process
from ecoli.states.wcecoli_state import get_state_from_file

RAND_MAX = 2**31
SIM_DATA_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), '..', '..', 'reconstruction',
        'sim_data', 'kb', 'simData.cPickle',
    )
)

MINIMAL_MEDIA_ID = 'minimal'
AA_MEDIA_ID = 'minimal_plus_amino_acids'
ANAEROBIC_MEDIA_ID = 'minimal_minus_oxygen'


class Ecoli(Composer):
    """
    The main composer used to create the :py:class:`~vivarium.core.composer.Composite` 
    that is given to :py:class:`~vivarium.core.engine.Engine` to run the E. coli whole 
    cell model.
    """

    defaults = {
        'time_step': 2.0,
        'seed': 0,
        'sim_data_path': SIM_DATA_PATH,
        'daughter_path': tuple(),
        'agent_id': '0',
        'agents_path': ('..', '..', 'agents',),
        'division_threshold': 668,  # fg
        'division_variable': ('listeners', 'mass', 'dry_mass'),
        'chromosome_path': ('unique',' full_chromosome'),
        'divide': False,
        'log_updates': False,
        'mar_regulon': False,
        'amp_lysis': False,
        'process_configs': {},
        'flow': {},
    }
    """A subset of configuration options with default values for testing 
    purposes (see :py:func:`~ecoli.composites.ecoli_master.ecoli_topology_plot`). 
    For normal users, this composer should only be called indirectly via the 
    :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim` interface, whose 
    defaults are laid out in the JSON file at 
    ``ecoli/composites/ecoli_configs/default.json``. 

    :meta hide-value:
    """


    def __init__(self, config: dict[str, Any]):
        """Loads pickled simulation data object (from ParCa, see 
        :py:mod:`~reconstruction.ecoli.fit_sim_data_1`) and instantiates all 
        processes and steps (also dynamically generates flow for steps).

        Attributes:
            load_sim_data: Instance of 
                :py:class:`~ecoli.library.sim_data.LoadSimData`
            config: Cached copy of ``config``
            processes_and_steps: Processes, steps, and flow generated by 
                :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps`

        Args:
            config: Configuration dictionary that is typically supplied by 
                :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim`
        """
        super().__init__(config)
        self.load_sim_data = LoadSimData(
            **self.config)

        if not self.config.get('processes'):
            self.config['processes'] = deepcopy(ECOLI_DEFAULT_PROCESSES)
        if not self.config.get('process_configs'):
            self.config['process_configs'] = {process: "sim_data"
                for process in self.config['processes']}
        if not self.config.get('topology'):
            self.config['topology'] = deepcopy(ECOLI_DEFAULT_TOPOLOGY)

        self.processes_and_steps = self.generate_processes_and_steps(self.config)


    def initial_state(self, config: dict[str, Any]=None) -> dict[str, Any]:
        """Users have three options for configuring the simulation initial state:
        
        1. ``config['initial_state']``
        
        2. Load the JSON file at ``f'data/{config['initial_state_file]}.json'`` 
        using :py:func:`~ecoli.states.wcecoli_state.get_state_from_file`.
        
        3. Generate initial state from simulation data object (see 
        :py:meth:`~ecoli.library.sim_data.LoadSimData.generate_initial_state`)

        This method will go through these options in order until a dictionary 
        is loaded. This dictionary will serve as the base initial state.

        Users can override values in the initial state by specifying one or more 
        override filenames in ``config['initial_state_overrides']``. For each 
        filename, the JSON at the path ``f'data/{override_filename}.json'`` is 
        loaded. Bulk molecule overrides (anything under the ``bulk`` key in the 
        loaded JSON) take the form of key-value pairs where keys are bulk 
        molecule IDs and values are the desired counts of those molecules. 
        These key-value pairs are parsed to change the desired counts for the 
        correct rows in the bulk molecule structured Numpy array (see :ref:`bulk`). 
        All other overrides are directly used to update the initial state with no 
        further processing. 
        
        As explained in :ref:`partitioning`, instances of 
        :py:class:`~ecoli.processes.partition.PartitionedProcess` are turned 
        into two :py:class:`~vivarium.core.process.Step` instances in the 
        final model: a :py:class:`~ecoli.processes.partition.Requester` and an 
        :py:class:`~ecoli.processes.partition.Evolver`. To ensure that both of 
        these steps have access to the same mutable parameters (e.g. if a 
        Requester changes a parameter, the Evolver will see the change), this 
        method places the the 
        :py:class:`~ecoli.processes.partition.PartitionedProcess` that they 
        share into the simulation state under the ``('process',)`` path. 
        
        .. WARNING::
            This method will **NOT** work if run after calling 
            :py:meth:`~vivarium.core.composer.Composer.generate` on this 
            composer.
            
        Args:
            config: Defaults to the ``config`` used to initialize 
                :py:class:`~ecoli.composites.ecoli_master.Ecoli`
        
        Returns:
            Complete initial state for an E. coli simulation."""
        config = config or self.config
        # Allow initial state to be directly supplied instead of a file name
        # (e.g. when loading individual cells in a colony save file)
        initial_state = config.get('initial_state', None)
        if not initial_state:
            initial_state_file = config.get('initial_state_file', None)
            # Generate initial state from sim_data if no file specified
            if not initial_state_file:
                initial_state = self.load_sim_data.generate_initial_state()
            else:
                initial_state = get_state_from_file(
                    path=f'data/{initial_state_file}.json')
        
        # Load first agent state in a division-enabled save state by default
        if 'agents' in initial_state.keys():
            warnings.warn("Trying to load a multi-agent simulation state into "
                "a single-cell simulation. Loading the state of arbitrary agent.")
            initial_state = list(initial_state['agents'].values())[0]

        initial_state_overrides = config.get('initial_state_overrides', [])
        # Create mapping of bulk molecule names to row indices, allowing users to 
        # specify bulk molecule overrides by name
        if initial_state_overrides:
            bulk_map = {bulk_id: row_id for row_id, bulk_id
                in enumerate(initial_state['bulk']['id'])}
        for override_file in initial_state_overrides:
            override = get_state_from_file(path=f"data/{override_file}.json")
            # Apply bulk overrides of the form {molecule: count} to Numpy array
            bulk_overrides = override.pop('bulk', {})
            initial_state['bulk'].flags.writeable = True
            for molecule, count in bulk_overrides.items():
                initial_state['bulk']['count'][bulk_map[molecule]] = count
            initial_state['bulk'].flags.writeable = False
            # All other overrides directly update initial state
            deep_merge(initial_state, override)

        # Put shared process instances for partitioned steps into state
        _, steps, _ = self.processes_and_steps
        initial_state['process'] = {
            step.parameters['process'].name: (step.parameters['process'],)
            for step in steps.values()
            if 'process' in step.parameters
        }
        return initial_state


    def generate_processes_and_steps(self, config: dict[str, Any]
        ) -> tuple[dict[str, Process], dict[str, Step], dict[str, tuple[str]]]:
        """Helper function that dynamically initializes all processes and 
        steps (including their flow) according to options supplied in ``config``. 
        This method is called when :py:class:`~ecoli.composites.ecoli_master.Ecoli` 
        is initialized and its return value is cached as the instance variable 
        :py:data:`~ecoli.composites.ecoli_master.Ecoli.processes_and_steps`. This 
        allows the :py:class:`~ecoli.composites.ecoli_master.Ecoli.initial_state` 
        method to be run before calling 
        :py:meth:`~vivarium.core.composer.Composer.generate` on this composer.
        
        Args:
            config: Important key-value pairs in this dictionary include:
                
                * ``process_configs``: 
                    Mapping of process names (:py:class:`str`) 
                    to process configs. The configs can either be dictionaries 
                    that will be used to initialize said process, the string 
                    ``"sim_data"`` to indicate that the process config should 
                    be loaded from the pickled simulation data object using 
                    :py:meth:`~ecoli.library.sim_data.LoadSimData.get_config_by_name`, 
                    or the string ``"default"`` to indicate that the 
                    ``defaults`` attribute of the process should be used as its config.
                
                * ``processes``: 
                    Mapping of all process names (:py:class:`str`) 
                    to the :py:class:`~vivarium.core.process.Process`, 
                    :py:class:`~vivarium.core.process.Step`, or 
                    :py:class:`~ecoli.processes.partition.PartitionedProcess` 
                    instances that they refer to.
                    
                * ``log_updates``: 
                    Boolean option indicating whether to emit 
                    the updates of all processes in ``config['processes']`` 
                    (separately log the updates from the 
                    :py:class:`~ecoli.processes.partition.Requester`
                    and :py:class:`~ecoli.processes.partition.Evolver` created 
                    from each 
                    :py:class:`~ecoli.processes.partition.PartitionedProcess`) at 
                    the path ``('log_update',)`` by wrapping them with 
                    :py:func:`~ecoli.library.logging_tools.make_logging_process`. 
                    See :py:mod:`~ecoli.plots.blame` for a plotting script that 
                    can be used to visualize how each process changes bulk 
                    molecule counts.
                
                * ``flow``: 
                    Mapping of process names to their dependencies. 
                    Note that the only names allowed must correspond to 
                    instances of either :py:class:`~vivarium.core.process.Step` 
                    or :py:class:`~ecoli.processes.partition.PartitionedProcess`. 
                    This method parses the names of partitioned processes and 
                    edits the flow to create the four execution layers detailed 
                    in :ref:`implementation`.
                    
                * ``divide``: 
                    Boolean option that adds 
                    :py:class:`~ecoli.processes.cell_division.Division` if true.
                
                * ``division_threshold``: 
                    Config option for 
                    :py:class:`~ecoli.processes.cell_division.Division`
                
                * ``agent_id``: 
                    Config option for 
                    :py:class:`~ecoli.processes.cell_division.Division`
                
                * ``d_period``: 
                    Boolean option that only matters if ``division`` is true. 
                    Adds :py:class:`~ecoli.processes.cell_division.MarkDPeriod` 
                    if true.
        
        Returns:
            Tuple consisting of a mapping of process names to fully initialized 
            :py:class:`~vivarium.core.process.Process` instances, a mapping of 
            step names to fully initialized 
            :py:class:`~vivarium.core.process.Step` instances, 
            and a flow describing the dependencies between steps.
        """
        time_step = config['time_step']
        # get the configs from sim_data (except for allocator, built later)
        process_configs = config['process_configs']
        for process in process_configs.keys():
            if process_configs[process] == "sim_data":
                process_configs[process] = \
                    self.load_sim_data.get_config_by_name(process, time_step)
            elif process_configs[process] == "default":
                process_configs[process] = None
            else:
                # user passed a dict, deep-merge with config from LoadSimData
                # if it exists, else, deep-merge with default
                try:
                    default = self.load_sim_data.get_config_by_name(
                        process, time_step)
                except KeyError:
                    default = self.config['processes'][process].defaults
                process_configs[process] = deep_merge(
                    deepcopy(default), process_configs[process])
                if 'seed' in process_configs[process]:
                    process_configs[process]['seed'] = (
                        process_configs[process]['seed'] +
                        config['seed']) % RAND_MAX

        # make the processes
        processes = {}
        steps = {}
        flow = {}
        self.partitioned_processes = []
        for process_name, process_class in config['processes'].items():
            if issubclass(process_class, PartitionedProcess):
                process = process_class(process_configs[process_name])
                if config['log_updates']:
                    steps[f'{process_name}_evolver'] = \
                        make_logging_process(Evolver)({
                            'time_step': time_step,
                            'process': process
                        })
                    steps[f'{process_name}_requester'] = \
                        make_logging_process(Requester)({
                            'time_step': time_step,
                            'process': process
                        })
                else:
                    steps[f'{process_name}_evolver'] = Evolver({
                        'time_step': time_step,
                        'process': process
                    })
                    steps[f'{process_name}_requester'] = Requester({
                        'time_step': time_step,
                        'process': process
                    })
                self.partitioned_processes.append(process_name)
            elif issubclass(process_class, Step):
                if config['log_updates']:
                    process_class = make_logging_process(process_class)
                process = process_class(process_configs[process_name])
                steps[process_name] = process
                continue
            else:
                if config['log_updates']:
                    process_class = make_logging_process(process_class)
                process = process_class(process_configs[process_name])
                processes[process_name] = process
                continue

        # Parse flow to get execution layers
        step_graph = _StepGraph()
        for process in config['processes']:
            # Get Step dependencies as tuple paths
            deps = config['flow'].get(process, [])
            tuplified_deps = []
            for dep_path in deps:
                # Use evolver for partitioned dependencies
                if dep_path[-1] in self.partitioned_processes:
                    tuplified_deps.append(tuple(dep_path[:-1])
                        + (f'{dep_path[-1]}_evolver',))
                else:
                    tuplified_deps.append(tuple(dep_path))
            # For partitioned steps, requesters must run before evolvers
            if process in self.partitioned_processes:
                step_graph.add((f'{process}_requester',), tuplified_deps)
                step_graph.add((f'{process}_evolver',),
                               [(f'{process}_requester',)])
            elif process in steps:
                step_graph.add((process,), tuplified_deps)
        
        # Build simulation flow with UniqueUpdate and Allocator layers
        layers = step_graph.get_execution_layers()
        allocator_counter = 1
        unique_update_counter = 1
        for layer_steps in layers:
            requesters = False
            for step_path in layer_steps:
                # Evolvers always go after the allocator for a given layer
                if 'evolver' in step_path[-1]:
                    flow[step_path[-1]] = [
                        (f'allocator_{allocator_counter - 1}',)]
                # Aside from first layer, all non-evolver layers will be
                # immediately preceeded by a UniqueUpdate layer
                elif unique_update_counter > 1:
                    flow[step_path[-1]] = [
                        (f'unique_update_{unique_update_counter - 1}',)]
                    if 'requester' in step_path[-1]:
                        requesters = True
                # First Step has no dependencies
                else:
                    flow[step_path[-1]] = []
            # Add Allocator layer right after requester layer
            if requesters:
                flow[f'allocator_{allocator_counter}'] = layer_steps
                allocator_counter += 1
            # Add UniqueUpdate layer after non-requester layers
            else:
                flow[f'unique_update_{unique_update_counter}'] = [step_path]
                unique_update_counter += 1

        # Add Allocator Steps
        allocator_config = self.load_sim_data.get_allocator_config(
            time_step, process_names=self.partitioned_processes)
        for i in range(1, allocator_counter):
            steps[f'allocator_{i}'] = Allocator(allocator_config)

        # Add UniqueUpdate Steps
        unique_mols = (self.load_sim_data.sim_data.internal_state
                       ).unique_molecule.unique_molecule_definitions.keys()
        unique_topo = {
            unique_mol + 's': ('unique', unique_mol)
            for unique_mol in unique_mols
            if unique_mol not in ['active_ribosome', 'DnaA_box']}
        unique_topo['active_ribosome'] = ('unique', 'active_ribosome')
        unique_topo['DnaA_boxes'] = ('unique', 'DnaA_box')
        params = {'unique_topo': unique_topo}
        for i in range(1, unique_update_counter):
            steps[f'unique_update_{i}'] = UniqueUpdate(params)

        # add division Step
        if config['divide']:
            division_config = {
                'division_threshold': config['division_threshold'],
                'agent_id': config['agent_id'],
                'composer': Ecoli,
                'composer_config': self.config,
                'dry_mass_inc_dict': \
                    self.load_sim_data.sim_data.expectedDryMassIncreaseDict,
                'seed': config['seed'],
            }
            steps['division'] = Division(division_config)
            if config['d_period']:
                steps['mark_d_period'] = MarkDPeriod()
                flow['mark_d_period'] = [
                    (f'unique_update_{unique_update_counter - 1}',)]
                flow['division'] = [('mark_d_period',)]
            else:
                flow['division'] = [
                    (f'unique_update_{unique_update_counter - 1}',)]
                
        # update schema overrides for evolvers and requesters
        update_override = {}
        delete_override = []
        for process_id, override in self.schema_override.items():
            if process_id in self.partitioned_processes:
                delete_override.append(process_id)
                update_override[f'{process_id}_evolver'] = override
                update_override[f'{process_id}_requester'] = override
        for process_id in delete_override:
            del self.schema_override[process_id]
        self.schema_override.update(update_override)

        return processes, steps, flow


    def generate_processes(self, config: dict[str, Any]) -> dict[str, Process]:
        """Retrieve cached processes generated by 
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps` 
        when :py:meth:`~vivarium.core.composer.Composer.generate` is called 
        on this composer.
        """
        processes, _, _ = self.processes_and_steps
        return processes


    def generate_steps(self, config: dict[str, Any]) -> dict[str, Step]:
        """Retrieve cached steps generated by 
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps` 
        when :py:meth:`~vivarium.core.composer.Composer.generate` is called 
        on this composer.
        """
        _, steps, _ = self.processes_and_steps
        return steps


    def generate_flow(self, config: dict[str, Any]) -> dict[str, list[tuple[str]]]:
        """Retrieve cached flow generated by 
        :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps` 
        when :py:meth:`~vivarium.core.composer.Composer.generate` is called 
        on this composer.
        """
        _, _, flow = self.processes_and_steps
        return flow


    def generate_topology(self, config: dict[str, Any]) -> dict[str, tuple[str]]:
        """Creates simulation topology when 
        :py:meth:`~vivarium.core.composer.Composer.generate` is called 
        on this composer.

        Args:
            config: Uses the same ``config`` supplied to this composer in 
                :py:class:`~ecoli.composites.ecoli_master.Ecoli` that 
                was used to generate the processes and steps in 
                :py:meth:`~ecoli.composites.ecoli_master.Ecoli.generate_processes_and_steps`. 
                Important key-value pairs include:

                    * ``topology``: 
                        Mapping of process names to topologies. Names of 
                        :py:class:`~ecoli.processes.partition.PartitionedProcess` 
                        instances are automatically split into two separate 
                        topologies, one for the 
                        :py:class:`~ecoli.processes.partition.Requester` and 
                        another for the 
                        :py:class:`~ecoli.processes.partition.Evolver` 
                        that is created for each partitioned process 
                        (see :ref:`partitioning`).
                    
                    * ``log_updates``: 
                        Boolean, adds additional ``log_update`` topology 
                        path to write the updates of each process when true. See 
                        :py:func:`~ecoli.library.logging_tools.make_logging_process`.

                    * ``divide``:
                        Boolean, adds toplogy for 
                        :py:class:`~ecoli.processes.cell_division.Division` 
                        when true.
                    
                    * ``d_period``:
                        Boolean, adds topology for 
                        :py:class:`~ecoli.processes.cell_division.MarkDPeriod` 
                        when true.

        Returns:
            Full topology for an E. coli simulation.
        """
        topology = {}
        # make the topology
        for process_id, ports in config['topology'].items():
            # make the partitioned processes' topologies
            if process_id in self.partitioned_processes:
                topology[f'{process_id}_requester'] = deepcopy(ports)
                topology[f'{process_id}_evolver'] = deepcopy(ports)
                if config['log_updates']:
                    topology[f'{process_id}_evolver']['log_update'] = (
                        'log_update', f'{process_id}_evolver',)
                    topology[f'{process_id}_requester']['log_update'] = (
                        'log_update', f'{process_id}_requester',)
                # Only the bulk ports should be included in the request
                # and allocate topologies
                topology[f'{process_id}_requester']['request'] = {
                    'bulk': ('request', process_id, 'bulk',)}
                topology[f'{process_id}_evolver']['allocate'] = {
                    'bulk': ('allocate', process_id, 'bulk',)}
                topology[f'{process_id}_requester'][
                    'next_update_time'] = ('next_update_time', process_id)
                topology[f'{process_id}_evolver'][
                    'next_update_time'] = ('next_update_time', process_id)
                topology[f'{process_id}_requester'][
                    'process'] = ('process', process_id,)
                topology[f'{process_id}_evolver'][
                    'process'] = ('process', process_id,)
                # Add global time
                topology[f'{process_id}_requester'][
                    'global_time'] = ('global_time',)
                topology[f'{process_id}_evolver'][
                    'global_time'] = ('global_time',)
            # make the non-partitioned processes' topologies
            else:
                topology[process_id] = ports
                if config['log_updates']:
                    topology[process_id]['log_update'] = (
                        'log_update', process_id,)

        # add division
        if config['divide']:
            if config['d_period']:
                topology['mark_d_period'] = {
                    'full_chromosome': tuple(config['chromosome_path']),
                    'global_time': ('global_time',),
                    'divide': ('divide',)}
            topology['division'] = {
                'division_variable': tuple(config['division_variable']),
                'full_chromosome': tuple(config['chromosome_path']),
                'agents': tuple(config['agents_path']),
                'media_id': ('environment', 'media_id'),
                'division_threshold': ('division_threshold',)}

        # Add Allocator and UniqueUpdate topologies
        _, steps, _ = self.processes_and_steps
        allocator_topo = {
            'request': ('request',),
            'allocate': ('allocate',),
            'bulk': ('bulk',),
        }
        for step_name in steps.keys():
            if 'unique_update' in step_name:
                topology[step_name] = steps[step_name].unique_topo.copy()
            elif 'allocator' in step_name:
                topology[step_name] = allocator_topo.copy()

        # Do not keep an unnecessary reference to these
        self.processes_and_steps = None
        return topology


def run_ecoli(
    filename: str = 'default',
    total_time: int = 10,
    divide: bool = False,
    progress_bar: bool = True,
    log_updates: bool = False,
    emitter: str = 'timeseries',
    time_series: bool = True,
):
    """Run E. coli simulations.

    Args:
        total_time: the total runtime of the experiment
        divide: whether to incorporate division
        progress_bar: whether to show a progress bar
        log_updates: whether to save updates from each process (refer to 
            :py:func:`~ecoli.library.logging_tools.make_logging_process`)
        emitter: type of emitter to use (refer to ``emitter`` argument 
            for :py:class:`~vivarium.core.engine.Engine`)
        time_series: whether to return data in timeseries format (refer to 
            :py:meth:`~ecoli.experiments.ecoli_master_sim.EcoliSim.query`)

    Returns:
        Data emitted by simulation. 

    .. note::
        If data is not set up to be emitted to a MongoDB database (e.g. 
        ``emitter`` is not ``database``), users will not have easy access 
        to the listener metadata that is emitted when ``emit_config`` is 
        true (see :py:class:`~ecoli.experiments.ecoli_master_sim.EcoliSim`, 
        :py:class:`~vivarium.core.engine.Engine`, and 
        :py:func:`~ecoli.library.schema.listener_schema` for details). 
    """
    # Import here to avoid circular import
    from ecoli.experiments.ecoli_master_sim import EcoliSim, CONFIG_DIR_PATH

    sim = EcoliSim.from_file(CONFIG_DIR_PATH + filename + '.json')
    sim.total_time = total_time
    sim.divide = divide
    sim.progress_bar = progress_bar
    sim.log_updates = log_updates
    sim.emitter = emitter
    sim.raw_output = not time_series
    sim.build_ecoli()
    sim.run()
    return sim.query()


def ecoli_topology_plot(config=None):
    if not config:
        config = {}
    """Make a topology plot of Ecoli"""
    agent_id_config = {'agent_id': '1'}
    ecoli = Ecoli({**agent_id_config, **config})
    settings = get_ecoli_partition_topology_settings()
    topo_plot = plot_topology(
        ecoli,
        filename='topology',
        out_dir='out/composites/ecoli_master',
        settings=settings
    )
    return topo_plot


test_library = {
    '0': run_ecoli,
    '1': ecoli_topology_plot,
}

# run experiments in test_library from the command line with:
# python ecoli/composites/ecoli_master.py -n [experiment id]
if __name__ == '__main__':
    run_library_cli(test_library)
