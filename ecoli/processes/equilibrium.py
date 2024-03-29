"""
===========
Equilibrium
===========

This process models how ligands are bound to or unbound
from their transcription factor binding partners in a fashion
that maintains equilibrium.
"""

import numpy as np

from ecoli.library.schema import (
    numpy_schema, bulk_name_to_idx, counts, listener_schema)
from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess

from wholecell.utils import units


# Register default topology for this process, associating it with process name
NAME = 'ecoli-equilibrium'
TOPOLOGY = {
    "listeners": ("listeners",),
    "bulk": ("bulk",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)


class Equilibrium(PartitionedProcess):
    """ Equilibrium PartitionedProcess

    molecule_names: list of molecules that are being iterated over size:94
    """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'jit': False,
        'n_avogadro': 0.0,
        'cell_density': 0.0,
        'stoichMatrix': [[]],
        'fluxesAndMoleculesToSS': lambda counts, volume, avogadro, random, jit: ([], []),
        'moleculeNames': [],
        'seed': 0,
        'complex_ids': [],
        'reaction_ids': []
    }

    # Constructor
    def __init__(self, parameters=None):
        super().__init__(parameters)

        # Simulation options
        # utilized in the fluxes and molecules function
        self.jit = self.parameters['jit']

        # Get constants
        self.n_avogadro = self.parameters['n_avogadro']
        self.cell_density = self.parameters['cell_density']

        # Create matrix and method
        # stoichMatrix: (94, 33), molecule counts are (94,).
        self.stoichMatrix = self.parameters['stoichMatrix']

        # fluxesAndMoleculesToSS: solves ODES to get to steady state based off
        # of cell density, volumes and molecule counts
        self.fluxesAndMoleculesToSS = self.parameters['fluxesAndMoleculesToSS']

        self.product_indices = [idx for idx in
            np.where(np.any(self.stoichMatrix > 0, axis=1))[0]]

        # Build views
        # moleculeNames: list of molecules that are being iterated over size: 94
        self.moleculeNames = self.parameters['moleculeNames']
        self.molecule_idx = None

        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed = self.seed)

        self.complex_ids = self.parameters['complex_ids']
        self.reaction_ids = self.parameters['reaction_ids']

    def ports_schema(self):
        return {
            'bulk': numpy_schema('bulk'),
            'listeners': {
                'mass': listener_schema({
                    'cell_mass': 0}),
                'equilibrium_listener': {**listener_schema({
                    'reaction_rates': ([0.] * len(self.reaction_ids),
                        self.reaction_ids)})}},
            'timestep': {'_default': self.parameters['time_step']}
        }

    def calculate_request(self, timestep, states):
        # At t=0, convert all strings to indices
        if self.molecule_idx is None:
            self.molecule_idx = bulk_name_to_idx(
                self.moleculeNames, states['bulk']['id'])

        # Get molecule counts
        moleculeCounts = counts(states['bulk'], self.molecule_idx)

        # Get cell mass and volume
        cellMass = (states['listeners']['mass']['cell_mass'] * units.fg).asNumber(units.g)
        cellVolume = cellMass / self.cell_density

        # Solve ODEs to steady state
        self.rxnFluxes, self.req = self.fluxesAndMoleculesToSS(
            moleculeCounts, cellVolume, self.n_avogadro, self.random_state,
            jit=self.jit,
            )

        # Request counts of molecules needed
        requests = {
            'bulk': [(self.molecule_idx, self.req.astype(int))]
        }
        return requests

    def evolve_state(self, timestep, states):
        # Get molecule counts
        moleculeCounts = counts(states['bulk'], self.molecule_idx)

        # Get counts of molecules allocated to this process
        rxnFluxes = self.rxnFluxes.copy()

        # If we didn't get allocated all the molecules we need, make do with
        # what we have (decrease reaction fluxes so that they make use of what
        # we have, but not more). Reduces at least one reaction every iteration
        # so the max number of iterations is the number of reactions that were
        # originally expected to occur + 1 to reach the break statement.
        max_iterations = int(np.abs(rxnFluxes).sum()) + 1
        for it in range(max_iterations):
            # Check if any metabolites will have negative counts with current reactions
            negative_metabolite_idxs = np.where(np.dot(self.stoichMatrix, rxnFluxes) + moleculeCounts < 0)[0]
            if len(negative_metabolite_idxs) == 0:
                break

            # Reduce reactions that consume metabolites with negative counts
            limited_rxn_stoich = self.stoichMatrix[negative_metabolite_idxs, :]
            fwd_rxn_idxs = np.where(np.logical_and(limited_rxn_stoich < 0, rxnFluxes > 0))[1]
            rev_rxn_idxs = np.where(np.logical_and(limited_rxn_stoich > 0, rxnFluxes < 0))[1]
            rxnFluxes[fwd_rxn_idxs] -= 1
            rxnFluxes[rev_rxn_idxs] += 1
            rxnFluxes[fwd_rxn_idxs] = np.fmax(0, rxnFluxes[fwd_rxn_idxs])
            rxnFluxes[rev_rxn_idxs] = np.fmin(0, rxnFluxes[rev_rxn_idxs])
        else:
            raise ValueError('Could not get positive counts in equilibrium with'
                ' allocated molecules.')

        # Increment changes in molecule counts
        deltaMolecules = np.dot(self.stoichMatrix, rxnFluxes).astype(int)

        update = {
            'bulk': [(self.molecule_idx, deltaMolecules)],
            'listeners': {
                'equilibrium_listener': {
                    'reaction_rates': deltaMolecules[
                        self.product_indices] / states['timestep']}}}

        return update


def test_equilibrium_listener():
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.total_time = 2
    sim.raw_output = False
    sim.build_ecoli()
    sim.run()
    listeners = sim.query()['agents']['0']['listeners']
    assert(type(listeners['equilibrium_listener']['reaction_rates'][0]) == list)
    assert(type(listeners['equilibrium_listener']['reaction_rates'][1]) == list)


if __name__ == '__main__':
    test_equilibrium_listener()
