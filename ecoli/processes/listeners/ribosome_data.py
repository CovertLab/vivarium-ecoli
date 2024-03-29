"""
======================
Ribosome Data Listener
======================
"""

import numpy as np
import warnings
from ecoli.library.schema import numpy_schema, listener_schema, attrs, bulk_name_to_idx
from vivarium.core.process import Step

from ecoli.processes.registries import topology_registry


NAME = 'ribosome_data_listener'
TOPOLOGY = {
    "listeners": ("listeners",),
    "active_ribosomes": ("unique", "active_ribosome"),
    "RNAs": ("unique", "RNA"),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", NAME),
}
topology_registry.register(
    NAME, TOPOLOGY
)

class RibosomeData(Step):
    """
    Listener for ribosome data.
    """
    name = NAME
    topology = TOPOLOGY

    defaults = {
        'n_monomers': [],
        'rRNA_cistron_tu_mapping_matrix': [],
        'rRNA_is_5S': [],
        'rRNA_is_16S': [],
        'rRNA_is_23S': [],
        'time_step': 1,
        'emit_unique': False,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.monomer_ids = self.parameters['monomer_ids']
        self.n_monomers = len(self.monomer_ids)
        self.rRNA_cistron_tu_mapping_matrix = self.parameters[
            'rRNA_cistron_tu_mapping_matrix']
        self.rRNA_is_5S = self.parameters['rRNA_is_5S']
        self.rRNA_is_16S = self.parameters['rRNA_is_16S']
        self.rRNA_is_23S = self.parameters['rRNA_is_23S']

    def ports_schema(self):
        n_rRNA_TUs = self.rRNA_cistron_tu_mapping_matrix.shape[1]
        ports = {
            'listeners': {
                'ribosome_data': listener_schema({
                    'n_ribosomes_per_transcript': ([0] * len(self.monomer_ids),
                        self.monomer_ids),
                    'n_ribosomes_on_partial_mRNA_per_transcript': (
                        [0] * len(self.monomer_ids), self.monomer_ids),
                    'total_rRNA_initiated': 0,
                    'total_rRNA_init_prob': 0.,
                    'rRNA5S_initiated': 0,
                    'rRNA16S_initiated': 0,
                    'rRNA23S_initiated': 0,
                    'rRNA5S_init_prob': 0.,
                    'rRNA16S_init_prob': 0.,
                    'rRNA23S_init_prob': 0.,
                    'mRNA_TU_index': [-1],
                    'n_ribosomes_on_each_mRNA': [-1],
                    'protein_mass_on_polysomes': [-1.],
                    'rRNA_initiated_TU': [0] * n_rRNA_TUs,
                    'rRNA_init_prob_TU': [0.] * n_rRNA_TUs
                })
            },
            'RNAs': numpy_schema('RNAs',
                emit=self.parameters['emit_unique']),
            'active_ribosomes': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),
            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']},
            'next_update_time': {
                '_default': self.parameters['time_step'],
                '_updater': 'set',
                '_divider': 'set'},
        }
        return ports
    
    def update_condition(self, timestep, states):
        """
        See :py:meth:`~ecoli.processes.partition.Requester.update_condition`.
        """
        if states['next_update_time'] <= states['global_time']:
            if states['next_update_time'] < states['global_time']:
                warnings.warn(f"{self.name} updated at t="
                    f"{states['global_time']} instead of t="
                    f"{states['next_update_time']}. Decrease the "
                    "timestep for the global clock process for more "
                    "accurate timekeeping.")
            return True
        return False

    def next_update(self, timestep, states):
        # Get attributes of RNAs and ribosomes
        (is_full_transcript_RNA, unique_index_RNA, can_translate,
         TU_index) = attrs(
            states['RNAs'],
            ['is_full_transcript', 'unique_index', 'can_translate', 'TU_index'])
        
        (protein_index_ribosomes, mRNA_index_ribosomes,
         massDiff_protein_ribosomes) = attrs(
            states['active_ribosomes'],
            ['protein_index', 'mRNA_index', 'massDiff_protein'])
        
        rRNA_initiated_TU = states['listeners']['ribosome_data'][
             'rRNA_initiated_TU']
        rRNA_init_prob_TU = states['listeners']['ribosome_data'][
                'rRNA_init_prob_TU']

        # Get mask for ribosomes that are translating proteins on partially
        # transcribed mRNAs
        ribosomes_on_nascent_mRNA_mask = np.isin(
            mRNA_index_ribosomes,
            unique_index_RNA[np.logical_not(is_full_transcript_RNA)])

        # Get counts of ribosomes for each type
        n_ribosomes_per_transcript = np.bincount(
            protein_index_ribosomes, minlength=self.n_monomers)
        n_ribosomes_on_partial_mRNA_per_transcript = np.bincount(
            protein_index_ribosomes[ribosomes_on_nascent_mRNA_mask],
            minlength=self.n_monomers)

        rRNA_cistrons_produced = self.rRNA_cistron_tu_mapping_matrix.dot(
            rRNA_initiated_TU)
        rRNA_cistrons_init_prob = self.rRNA_cistron_tu_mapping_matrix.dot(
            rRNA_init_prob_TU)
        total_rRNA_initiated = np.sum(rRNA_initiated_TU, dtype=int)
        total_rRNA_init_prob = np.sum(rRNA_init_prob_TU)
        rRNA5S_initiated = np.sum(rRNA_cistrons_produced[self.rRNA_is_5S], dtype=int)
        rRNA16S_initiated = np.sum(rRNA_cistrons_produced[self.rRNA_is_16S], dtype=int)
        rRNA23S_initiated = np.sum(rRNA_cistrons_produced[self.rRNA_is_23S], dtype=int)
        rRNA5S_init_prob = np.sum(rRNA_cistrons_init_prob[self.rRNA_is_5S])
        rRNA16S_init_prob = np.sum(rRNA_cistrons_init_prob[self.rRNA_is_16S])
        rRNA23S_init_prob = np.sum(rRNA_cistrons_init_prob[self.rRNA_is_23S])

        # Get fully transcribed translatable mRNA index
        is_full_mRNA = can_translate & is_full_transcript_RNA
        mRNA_unique_index = unique_index_RNA[is_full_mRNA]
        mRNA_TU_index = TU_index[is_full_mRNA]

        # Inverse indices from np.unique are better for np.bincount
        # because real indices can go up to 2**63
        unique_mRNA_index_ribosomes, reduced_mRNA_index_ribosomes = np.unique(
            mRNA_index_ribosomes, return_inverse=True)
        # Calculate mapping from inverse indices back to mRNA_unique_indices
        reduced_to_normal_mRNA_indices = bulk_name_to_idx(
            mRNA_unique_index, unique_mRNA_index_ribosomes)
        # Many mRNAs in mRNA_unique_indices will have no bound ribosomes
        # Have them point to last zero of lengthened np.bincount output
        no_ribosomes_mask = (unique_mRNA_index_ribosomes[
            reduced_to_normal_mRNA_indices] != mRNA_unique_index)
        reduced_to_normal_mRNA_indices[no_ribosomes_mask] = -1
        bincount_minlength = max(reduced_mRNA_index_ribosomes) + 2

        # Get counts of ribosomes attached to the same mRNA
        bincount_ribosome_on_mRNA = np.bincount(reduced_mRNA_index_ribosomes,
            minlength=bincount_minlength)
        n_ribosomes_on_each_mRNA = bincount_ribosome_on_mRNA[
            reduced_to_normal_mRNA_indices]

        # Get protein mass on each polysome
        protein_mass_on_polysomes = np.bincount(
            reduced_mRNA_index_ribosomes, weights=massDiff_protein_ribosomes,
            minlength=bincount_minlength)[reduced_to_normal_mRNA_indices]
        
        update = {
            'listeners': {
                'ribosome_data': {
                    'n_ribosomes_per_transcript': n_ribosomes_per_transcript,
                    'n_ribosomes_on_partial_mRNA_per_transcript': \
                        n_ribosomes_on_partial_mRNA_per_transcript,
                    'total_rRNA_initiated': total_rRNA_initiated,
                    'total_rRNA_init_prob': total_rRNA_init_prob,
                    'rRNA5S_initiated': rRNA5S_initiated,
                    'rRNA16S_initiated': rRNA16S_initiated,
                    'rRNA23S_initiated': rRNA23S_initiated,
                    'rRNA5S_init_prob': rRNA5S_init_prob,
                    'rRNA16S_init_prob': rRNA16S_init_prob,
                    'rRNA23S_init_prob': rRNA23S_init_prob,
                    'mRNA_TU_index': mRNA_TU_index,
                    'n_ribosomes_on_each_mRNA': n_ribosomes_on_each_mRNA,
                    'protein_mass_on_polysomes': protein_mass_on_polysomes,

                }
            },
            'next_update_time': states['global_time'] + states['timestep']
        }
        return update
