"""
=========================
DNA Supercoiling Listener
=========================
"""

import numpy as np
from ecoli.library.schema import numpy_schema, listener_schema, attrs
from vivarium.core.process import Step

from ecoli.processes.registries import topology_registry


NAME = 'dna_supercoiling_listener'
TOPOLOGY = {
    "listeners": ("listeners",),
    "chromosomal_segments": ("unique", "chromosomal_segment"),
    "global_time": ("global_time",),
    "timestep": ("timestep",)
}
topology_registry.register(
    NAME, TOPOLOGY
)

class DnaSupercoiling(Step):
    """
    Listener for DNA supercoiling data.
    """
    name = NAME
    topology = TOPOLOGY

    defaults = {
        'relaxed_DNA_base_pairs_per_turn': 0,
        'emit_unique': False,
        'time_step': 1
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.relaxed_DNA_base_pairs_per_turn = self.parameters[
            'relaxed_DNA_base_pairs_per_turn']

    def ports_schema(self):
        return {
            'listeners': {
                'dna_supercoiling': listener_schema({
                    'segment_left_boundary_coordinates': [-1],
                    'segment_right_boundary_coordinates': [-1],
                    'segment_domain_indexes': [-1],
                    'segment_superhelical_densities': [-1]
                })
            },
            'chromosomal_segments': numpy_schema('chromosomal_segments',
                emit=self.parameters['emit_unique']),
            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']}
        }
    
    def update_condition(self, timestep, states):
        return (states['global_time'] % states['timestep']) == 0

    def next_update(self, timestep, states):
        boundary_coordinates, domain_indexes, linking_numbers = attrs(
            states['chromosomal_segments'],
            ['boundary_coordinates', 'domain_index', 'linking_number'])

        if len(boundary_coordinates) == 0:
            return {
                'listeners': {
                    'dna_supercoiling': {
                        'segment_left_boundary_coordinates': [-1],
                        'segment_right_boundary_coordinates': [-1],
                        'segment_domain_indexes': [-1],
                        'segment_superhelical_densities': [-1]
                    }
                }
            }

        # Get mask for segments with nonzero lengths
        segment_lengths = boundary_coordinates[:, 1] - boundary_coordinates[
            :, 0]

        assert np.all(segment_lengths >= 0)
        nonzero_length_mask = (segment_lengths > 0)

        # Calculate superhelical densities
        linking_numbers_relaxed_DNA = (segment_lengths[nonzero_length_mask
            ] / self.relaxed_DNA_base_pairs_per_turn)

        update = {
            'listeners': {
                'dna_supercoiling': {
                    'segment_left_boundary_coordinates': boundary_coordinates[
                        nonzero_length_mask, 0],
                    'segment_right_boundary_coordinates': boundary_coordinates[
                        nonzero_length_mask, 1],
                    'segment_domain_indexes': domain_indexes[
                        nonzero_length_mask],
                    'segment_superhelical_densities': np.divide(
                        linking_numbers[nonzero_length_mask] -
                        linking_numbers_relaxed_DNA,
                        linking_numbers_relaxed_DNA)
                }
            }
        }
        return update
