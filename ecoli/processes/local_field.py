import numpy as np

from vivarium.core.process import Deriver
from vivarium.library.units import units, remove_units

from ecoli.library.lattice_utils import (
    get_bin_site,
    get_bin_volume,
    count_to_concentration,
)


class LocalField(Deriver):

    name = 'local_field'
    defaults = {
        'initial_external': {'location': [0.5, 0.5], 'dimensions': {'bounds': [1, 1], 'n_bins': [1, 1], 'depth': 1}},
        'nonspatial': False,
        'bin_volume': 1e-6 * units.L,
        'time_step': 2.0,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.nonspatial = self.parameters['nonspatial']
        self.bin_volume = self.parameters['bin_volume']


    def initial_state(self, config=None):
        return {
            'fields': self.parameters['initial_external']
        }


    def ports_schema(self):
        return {
            'exchanges': {
                '*': {
                    '_default': 0,
                }
            },
            'location': {
                '_default': [50, 50],
                '_updater': 'set'
            },
            'fields': {
                '*': {
                    '_default': np.ones(1) if not self.nonspatial else 1.0,
                    '_emit': True,
                }
            },
            'dimensions': {
                'bounds': {
                    '_default': [100, 100],
                    '_updater': 'set'
                },
                'n_bins': {
                    '_default': [1, 1],
                    '_updater': 'set'
                },
                'depth': {
                    '_default': 1,
                },
            }
        }


    def next_update(self, timestep, states):
        location = remove_units(states['location'])
        n_bins = states['dimensions']['n_bins']
        bounds = states['dimensions']['bounds']
        depth = states['dimensions']['depth']
        exchanges = states['exchanges']

        # get bin volume
        if self.nonspatial:
            bin_volume = self.bin_volume
        else:
            bin_site = get_bin_site(location, n_bins, bounds)
            bin_volume = get_bin_volume(n_bins, bounds, depth) * units.L

        # apply exchanges
        delta_fields = {}
        reset_exchanges = {}
        
        for mol_id, value in exchanges.items():

            # delta concentration
            exchange = value * units.count
            concentration = count_to_concentration(exchange, bin_volume).to(
                units.mmol / units.L).magnitude

            if self.nonspatial:
                delta_fields[mol_id] = {
                    '_value': concentration,
                    '_updater': 'nonnegative_accumulate'}
            else:
                delta_field = np.zeros((n_bins[0], n_bins[1]), dtype=np.float64)
                delta_field[bin_site[0], bin_site[1]] += concentration
                delta_fields[mol_id] = {
                    '_value': delta_field,
                    '_updater': 'nonnegative_accumulate'}
        
        return {
            'fields': delta_fields,
            'location': [50, 50],
            'dimensions': {
                'bounds': [100, 100]
            }
        }


def test_local_fields():
    parameters = {}
    local_fields_process = LocalField(parameters)

    bounds = [5, 5]
    n_bins = [3, 3]
    initial_state = {
        'exchanges': {
            'A': 20
        },
        'location': [0.5, 0.5],
        'fields': {
            'A': np.ones((n_bins[0], n_bins[1]), dtype=np.float64)
        },
        'dimensions': {
            'bounds': bounds,
            'n_bins': n_bins,
            'depth': 1,
        }
    }

    output = local_fields_process.next_update(0, initial_state)


if __name__ == '__main__':
    test_local_fields()
