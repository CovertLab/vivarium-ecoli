"""
===============================
Unique Molecule Counts Listener
===============================

Counts unique molecules
"""

from vivarium.core.process import Step
from ecoli.library.schema import numpy_schema, listener_schema
from ecoli.processes.registries import topology_registry

# Register default topology for this process, associating it with process name
NAME = 'unique_molecule_counts'
TOPOLOGY = {
    "unique": ("unique",),
    "listeners": ("listeners",),
    "global_time": ("global_time",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)

class UniqueMoleculeCounts(Step):
    """ UniqueMoleculeCounts """
    name = NAME
    topology = TOPOLOGY

    defaults = {
        'time_step': 1,
        'emit_unique': False,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.unique_ids = self.parameters['unique_ids']

    def ports_schema(self):
        ports = {
            'unique': {
                str(mol_id): numpy_schema(mol_id + 's',
                    emit=self.parameters['emit_unique'])
                for mol_id in self.unique_ids
                if mol_id not in [
                    'DnaA_box',
                    'active_ribosome'
                ]
            },
            'listeners': {
                'unique_molecule_counts': listener_schema({
                    str(mol_id): 0
                    for mol_id in self.unique_ids
                })
            },
            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']}
        }
        ports['unique'].update({
            'active_ribosome': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),
            'DnaA_box': numpy_schema('DnaA_boxes',
                emit=self.parameters['emit_unique']),
        })
        return ports
    
    def update_condition(self, timestep, states):
        return (states['global_time'] % states['timestep']) == 0

    def next_update(self, timestep, states):
        return {
            'listeners': {
                'unique_molecule_counts': {
                    str(unique_id): states['unique'][unique_id][
                        '_entryState'].sum()
                    for unique_id in self.unique_ids
                }
            }
        }
