"""
=======================
Monomer Counts Listener
=======================
"""

import numpy as np
from ecoli.library.schema import numpy_schema, counts, bulk_name_to_idx
from vivarium.core.process import Step

from ecoli.processes.registries import topology_registry


NAME = 'monomer_counts_listener'
TOPOLOGY = {
    "listeners": ("listeners",),
    "bulk": ("bulk",),
    "unique": ("unique",),
    "global_time": ("global_time",),
    "timestep": ("timestep",)
}
topology_registry.register(
    NAME, TOPOLOGY
)

class MonomerCounts(Step):
    """
    Listener for the counts of each protein monomer species.
    """
    name = NAME
    topology = TOPOLOGY

    defaults = {
        'bulk_molecule_ids': [],
        'unique_ids': [],
        'complexation_molecule_ids': [],
        'complexation_complex_ids': [],
        'equilibrium_molecule_ids': [],
        'equilibrium_complex_ids': [],
        'monomer_ids': [],
        'two_component_system_molecule_ids': [],
        'two_component_system_complex_ids': [],
        'ribosome_50s_subunits': [],
        'ribosome_30s_subunits': [],
        'rnap_subunits': [],
        'replisome_trimer_subunits': [],
        'replisome_monomer_subunits': [],
        'complexation_stoich': [],
        'equilibrium_stoich': [],
        'two_component_system_stoich': [],
        'emit_unique': False,
        'time_step': 1
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        # Get IDs of all bulk molecules
        self.bulk_molecule_ids = self.parameters['bulk_molecule_ids']

        # Get IDs of molecules involved in complexation and equilibrium
        self.complexation_molecule_ids = self.parameters[
            'complexation_molecule_ids']
        self.complexation_complex_ids = self.parameters[
            'complexation_complex_ids']
        self.equilibrium_molecule_ids = self.parameters[
            'equilibrium_molecule_ids']
        self.equilibrium_complex_ids = self.parameters[
            'equilibrium_complex_ids']
        self.monomer_ids = self.parameters['monomer_ids']

        # Get IDs of complexed molecules monomers involved in two
        # component system
        self.two_component_system_molecule_ids = self.parameters[
            'two_component_system_molecule_ids']
        self.two_component_system_complex_ids = self.parameters[
            'two_component_system_complex_ids']

        # Get IDs of ribosome subunits
        ribosome_50s_subunits = self.parameters['ribosome_50s_subunits']
        ribosome_30s_subunits = self.parameters['ribosome_30s_subunits']
        self.ribosome_subunit_ids = (
            ribosome_50s_subunits["subunitIds"].tolist() +
            ribosome_30s_subunits["subunitIds"].tolist())

        # Get IDs of RNA polymerase subunits
        rnap_subunits = self.parameters['rnap_subunits']
        self.rnap_subunit_ids = rnap_subunits["subunitIds"].tolist()

        # Get IDs of replisome subunits
        replisome_trimer_subunits = self.parameters[
            'replisome_trimer_subunits']
        replisome_monomer_subunits = self.parameters[
            'replisome_monomer_subunits']
        self.replisome_subunit_ids = replisome_trimer_subunits \
            + replisome_monomer_subunits

        # Get stoichiometric matrices for complexation, equilibrium, two
        # component system and the assembly of unique molecules
        self.complexation_stoich = self.parameters['complexation_stoich']
        self.equilibrium_stoich = self.parameters['equilibrium_stoich']
        self.two_component_system_stoich = self.parameters[
            'two_component_system_stoich']
        self.ribosome_stoich = np.hstack(
            (ribosome_50s_subunits["subunitStoich"],
             ribosome_30s_subunits["subunitStoich"]))
        self.rnap_stoich = rnap_subunits["subunitStoich"]
        self.replisome_stoich = np.hstack(
            (3 * np.ones(len(replisome_trimer_subunits)),
             np.ones(len(replisome_monomer_subunits))))

        # Helper indices for Numpy indexing
        self.monomer_idx = None

    def ports_schema(self):
        return {
            'listeners': {
                'monomer_counts': {
                    '_default': [],
                    '_updater': 'set',
                    '_emit': True,
                    '_properties': {'metadata': self.monomer_ids}}
            },
            'bulk': numpy_schema('bulk'),
            'unique': {
                'active_ribosome': numpy_schema('active_ribosome',
                    emit=self.parameters['emit_unique']),
                'active_RNAP': numpy_schema('active_RNAPs',
                    emit=self.parameters['emit_unique']),
                'active_replisome': numpy_schema('active_replisomes',
                    emit=self.parameters['emit_unique']),
            },
            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']}
        }
    
    def update_condition(self, timestep, states):
        return (states['global_time'] % states['timestep']) == 0

    def next_update(self, timestep, states):
        if self.monomer_idx is None:
            bulk_ids = states['bulk']['id']
            self.bulk_molecule_idx = bulk_name_to_idx(
                self.bulk_molecule_ids, bulk_ids)
            self.monomer_idx = bulk_name_to_idx(self.monomer_ids, bulk_ids)
            self.complexation_molecule_idx = bulk_name_to_idx(
                self.complexation_molecule_ids, bulk_ids)
            self.complexation_complex_idx = bulk_name_to_idx(
                self.complexation_complex_ids, bulk_ids)
            self.equilibrium_molecule_idx = bulk_name_to_idx(
                self.equilibrium_molecule_ids, bulk_ids)
            self.equilibrium_complex_idx = bulk_name_to_idx(
                self.equilibrium_complex_ids, bulk_ids)
            self.two_component_system_molecule_idx = bulk_name_to_idx(
                self.two_component_system_molecule_ids, bulk_ids)
            self.two_component_system_complex_idx = bulk_name_to_idx(
                self.two_component_system_complex_ids, bulk_ids)
            self.ribosome_subunit_idx = bulk_name_to_idx(
                self.ribosome_subunit_ids, bulk_ids)
            self.rnap_subunit_idx = bulk_name_to_idx(
                self.rnap_subunit_ids, bulk_ids)
            self.replisome_subunit_idx = bulk_name_to_idx(
                self.replisome_subunit_ids, bulk_ids)

        # Get current counts of bulk and unique molecules
        bulkMoleculeCounts = counts(states['bulk'], self.bulk_molecule_idx)
        n_active_ribosome = states['unique'][
            'active_ribosome']['_entryState'].sum()
        n_active_rnap = states['unique']['active_RNAP'][
            '_entryState'].sum()
        n_active_replisome = states['unique']['active_replisome'][
            '_entryState'].sum()

        # Account for monomers in bulk molecule complexes
        complex_monomer_counts = np.dot(self.complexation_stoich,
            np.negative(bulkMoleculeCounts[self.complexation_complex_idx]))
        equilibrium_monomer_counts = np.dot(self.equilibrium_stoich,
            np.negative(bulkMoleculeCounts[self.equilibrium_complex_idx]))
        two_component_monomer_counts = np.dot(self.two_component_system_stoich,
            np.negative(bulkMoleculeCounts[
                self.two_component_system_complex_idx]))

        bulkMoleculeCounts[self.complexation_molecule_idx] += \
            complex_monomer_counts.astype(np.int32)
        bulkMoleculeCounts[self.equilibrium_molecule_idx] += \
            equilibrium_monomer_counts.astype(np.int32)
        bulkMoleculeCounts[self.two_component_system_molecule_idx] += \
            two_component_monomer_counts.astype(np.int32)

        # Account for monomers in unique molecule complexes
        n_ribosome_subunit = n_active_ribosome * self.ribosome_stoich
        n_rnap_subunit = n_active_rnap * self.rnap_stoich
        n_replisome_subunit = n_active_replisome * self.replisome_stoich
        bulkMoleculeCounts[self.ribosome_subunit_idx] += \
            n_ribosome_subunit.astype(np.int32)
        bulkMoleculeCounts[self.rnap_subunit_idx] += \
            n_rnap_subunit.astype(np.int32)
        bulkMoleculeCounts[self.replisome_subunit_idx] += \
            n_replisome_subunit.astype(np.int32)

        # Update monomerCounts
        monomer_counts = bulkMoleculeCounts[self.monomer_idx]

        update = {
            'listeners': {
                'monomer_counts': monomer_counts
            }
        }
        return update


def test_monomer_counts_listener():
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.total_time = 2
    sim.raw_output = False
    sim.build_ecoli()
    sim.run()
    listeners = sim.query()['agents']['0']['listeners']
    assert(type(listeners['monomer_counts'][0]) == list)
    assert(type(listeners['monomer_counts'][1]) == list)


# python ecoli/processes/listeners/monomer_counts.py
if __name__ == '__main__':
    test_monomer_counts_listener()
