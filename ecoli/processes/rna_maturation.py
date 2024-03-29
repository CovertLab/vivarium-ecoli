"""
RnaMaturation process
=====================
- Converts unprocessed tRNA/rRNA molecules into mature tRNA/rRNAs
- Consolidates the different variants of 23S, 16S, and 5S rRNAs into the single
  variant that is used for ribosomal subunits
"""
import numpy as np

from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess
from ecoli.library.schema import (
    listener_schema, numpy_schema, counts, bulk_name_to_idx)

# Register default topology for this process, associating it with process name
NAME = 'ecoli-rna-maturation'
TOPOLOGY = {
    "bulk": ("bulk",),
    "bulk_total": ("bulk",),
    "listeners": ("listeners",)
}
topology_registry.register(NAME, TOPOLOGY)


class RnaMaturation(PartitionedProcess):
    """ RnaMaturation """

    name = NAME
    topology = TOPOLOGY

    # Constructor
    def __init__(self, parameters=None):
        super().__init__(parameters)
        # Get matrices and vectors that describe maturation reactions
        self.stoich_matrix = self.parameters['stoich_matrix']
        self.enzyme_matrix = self.parameters['enzyme_matrix']
        self.n_required_enzymes = self.parameters['n_required_enzymes']
        self.degraded_nt_counts = self.parameters['degraded_nt_counts']
        self.n_ppi_added = self.parameters['n_ppi_added']

        # Calculate number of NMPs that should be added when consolidating rRNA
        # molecules
        self.main_23s_rRNA_id = self.parameters['main_23s_rRNA_id']
        self.main_16s_rRNA_id = self.parameters['main_16s_rRNA_id']
        self.main_5s_rRNA_id = self.parameters['main_5s_rRNA_id']

        self.variant_23s_rRNA_ids = self.parameters['variant_23s_rRNA_ids']
        self.variant_16s_rRNA_ids = self.parameters['variant_16s_rRNA_ids']
        self.variant_5s_rRNA_ids = self.parameters['variant_5s_rRNA_ids']

        self.delta_nt_counts_23s = self.parameters['delta_nt_counts_23s']
        self.delta_nt_counts_16s = self.parameters['delta_nt_counts_16s']
        self.delta_nt_counts_5s = self.parameters['delta_nt_counts_5s']

        # Bulk molecule IDs
        self.unprocessed_rna_ids = self.parameters['unprocessed_rna_ids']
        self.mature_rna_ids = self.parameters['mature_rna_ids']
        self.rna_maturation_enzyme_ids = self.parameters[
            'rna_maturation_enzyme_ids']
        self.fragment_bases = self.parameters['fragment_bases']
        self.ppi = self.parameters['ppi']
        self.water = self.parameters['water']
        self.nmps = self.parameters['nmps']
        self.proton = self.parameters['proton']

        # Numpy indices for bulk molecules
        self.ppi_idx = None

    def ports_schema(self):
        return {
            'bulk': numpy_schema('bulk'),
            'bulk_total': numpy_schema('bulk'),
            'listeners': {
                'rna_maturation_listener': listener_schema({
                    'total_maturation_events': 0,
                    'total_degraded_ntps': 0,
                    'unprocessed_rnas_consumed': (
                        [0] * len(self.unprocessed_rna_ids),
                        self.unprocessed_rna_ids),
                    'mature_rnas_generated': ([0] * len(self.mature_rna_ids),
                        self.mature_rna_ids),
                    'maturation_enzyme_counts': (
                        [0] * len(self.rna_maturation_enzyme_ids),
                        self.rna_maturation_enzyme_ids)})
            },
        }

    def calculate_request(self, timestep, states):
        # Get bulk indices
        if self.ppi_idx is None:
            bulk_ids = states['bulk']['id']
            self.unprocessed_rna_idx = bulk_name_to_idx(
                self.unprocessed_rna_ids, bulk_ids)
            self.mature_rna_idx = bulk_name_to_idx(
                self.mature_rna_ids, bulk_ids)
            self.rna_maturation_enzyme_idx = bulk_name_to_idx(
                self.rna_maturation_enzyme_ids, bulk_ids)
            self.fragment_base_idx = bulk_name_to_idx(
                self.fragment_bases, bulk_ids)
            self.ppi_idx = bulk_name_to_idx(self.ppi, bulk_ids)
            self.water_idx = bulk_name_to_idx(self.water, bulk_ids)
            self.nmps_idx = bulk_name_to_idx(self.nmps, bulk_ids)
            self.proton_idx = bulk_name_to_idx(self.proton, bulk_ids)
            self.main_23s_rRNA_idx = bulk_name_to_idx(
                self.main_23s_rRNA_id, bulk_ids)
            self.main_16s_rRNA_idx = bulk_name_to_idx(
                self.main_16s_rRNA_id, bulk_ids)
            self.main_5s_rRNA_idx = bulk_name_to_idx(
                self.main_5s_rRNA_id, bulk_ids)
            self.variant_23s_rRNA_idx = bulk_name_to_idx(
                self.variant_23s_rRNA_ids, bulk_ids)
            self.variant_16s_rRNA_idx = bulk_name_to_idx(
                self.variant_16s_rRNA_ids, bulk_ids)
            self.variant_5s_rRNA_idx = bulk_name_to_idx(
                self.variant_5s_rRNA_ids, bulk_ids)


        unprocessed_rna_counts = counts(states['bulk_total'], self.unprocessed_rna_idx)
        variant_23s_rRNA_counts = counts(states['bulk_total'], self.variant_23s_rRNA_idx)
        variant_16s_rRNA_counts = counts(states['bulk_total'], self.variant_16s_rRNA_idx)
        variant_5s_rRNA_counts = counts(states['bulk_total'], self.variant_5s_rRNA_idx)
        self.enzyme_availability = counts(
            states['bulk_total'], self.rna_maturation_enzyme_idx).astype(bool)

        # Determine which maturation reactions to turn off based on enzyme
        # availability
        reaction_is_off = (
            self.enzyme_matrix.dot(self.enzyme_availability)
            < self.n_required_enzymes
        )
        unprocessed_rna_counts[reaction_is_off] = 0

        # Calculate NMPs, water, and proton needed to balance mass
        n_added_bases_from_maturation = np.dot(
            self.degraded_nt_counts.T, unprocessed_rna_counts)
        n_added_bases_from_consolidation = (
            self.delta_nt_counts_23s.T.dot(variant_23s_rRNA_counts)
            + self.delta_nt_counts_16s.T.dot(variant_16s_rRNA_counts)
            + self.delta_nt_counts_5s.T.dot(variant_5s_rRNA_counts)
        )
        n_added_bases = n_added_bases_from_maturation + n_added_bases_from_consolidation
        n_total_added_bases = int(n_added_bases.sum())

        # Request all unprocessed RNAs, ppis that need to be added to the
        # 5'-ends of mature RNAs, all variant rRNAs, and NMPs/water/protons
        # needed to balance mass
        request = {
            'bulk': [
                (self.unprocessed_rna_idx, unprocessed_rna_counts),
                (self.ppi_idx, self.n_ppi_added.dot(unprocessed_rna_counts)),
                (self.variant_23s_rRNA_idx, variant_23s_rRNA_counts),
                (self.variant_16s_rRNA_idx, variant_16s_rRNA_counts),
                (self.variant_5s_rRNA_idx, variant_5s_rRNA_counts),
                (self.nmps_idx, np.abs(-n_added_bases).astype(int)),
            ]
        }

        if n_total_added_bases > 0:
            request['bulk'].append((self.water_idx, n_total_added_bases))
        else:
            request['bulk'].append((self.proton_idx, -n_total_added_bases))

        return request

    def evolve_state(self, timestep, states):
        # Create copy of bulk counts so can update in real-time
        states['bulk'] = counts(states['bulk'], range(len(states['bulk'])))

        # Get counts of unprocessed RNAs
        unprocessed_rna_counts = counts(states['bulk'], self.unprocessed_rna_idx)

        # Calculate numbers of mature RNAs and fragment bases that are generated
        # upon maturation
        n_mature_rnas = self.stoich_matrix.dot(unprocessed_rna_counts)
        n_added_bases_from_maturation = np.dot(
            self.degraded_nt_counts.T, unprocessed_rna_counts)

        states['bulk'][self.mature_rna_idx] += n_mature_rnas
        states['bulk'][self.unprocessed_rna_idx] += -unprocessed_rna_counts
        ppi_update = self.n_ppi_added.dot(unprocessed_rna_counts)
        states['bulk'][self.ppi_idx] += -ppi_update
        update = {
            'bulk': [
                (self.mature_rna_idx, n_mature_rnas),
                (self.unprocessed_rna_idx, -unprocessed_rna_counts),
                (self.ppi_idx, -ppi_update),
            ],
            'listeners': {
                'rna_maturation_listener': {
                    'total_maturation_events': unprocessed_rna_counts.sum(),
                    'total_degraded_ntps': n_added_bases_from_maturation.sum(dtype=int),
                    'unprocessed_rnas_consumed': unprocessed_rna_counts,
                    'mature_rnas_generated': n_mature_rnas,
                    'maturation_enzyme_counts': counts(
                        states['bulk_total'], self.rna_maturation_enzyme_idx)
                }
            }
        }

        # Get counts of variant rRNAs
        variant_23s_rRNA_counts = counts(states['bulk'], self.variant_23s_rRNA_idx)
        variant_16s_rRNA_counts = counts(states['bulk'], self.variant_16s_rRNA_idx)
        variant_5s_rRNA_counts = counts(states['bulk'], self.variant_5s_rRNA_idx)

        # Calculate number of NMPs that should be added to balance out the mass
        # difference during the consolidation
        n_added_bases_from_consolidation = (
            self.delta_nt_counts_23s.T.dot(variant_23s_rRNA_counts)
            + self.delta_nt_counts_16s.T.dot(variant_16s_rRNA_counts)
            + self.delta_nt_counts_5s.T.dot(variant_5s_rRNA_counts)
            )

        # Evolve states
        update['bulk'].extend([
            (self.main_23s_rRNA_idx, variant_23s_rRNA_counts.sum()),
            (self.main_16s_rRNA_idx, variant_16s_rRNA_counts.sum()),
            (self.main_5s_rRNA_idx, variant_5s_rRNA_counts.sum()),
            (self.variant_23s_rRNA_idx, -variant_23s_rRNA_counts),
            (self.variant_16s_rRNA_idx, -variant_16s_rRNA_counts),
            (self.variant_5s_rRNA_idx, -variant_5s_rRNA_counts)
        ])

        # Consume or add NMPs to balance out mass
        n_added_bases = (n_added_bases_from_maturation +
                         n_added_bases_from_consolidation).astype(int)
        n_total_added_bases = n_added_bases.sum()

        update['bulk'].extend([
            (self.nmps_idx, n_added_bases),
            (self.water_idx, -n_total_added_bases),
            (self.proton_idx, n_total_added_bases)])

        return update
