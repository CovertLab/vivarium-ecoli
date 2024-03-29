"""
======================
Polypeptide Initiation
======================

This process models the complementation of 30S and 50S ribosomal subunits
into 70S ribosomes on mRNA transcripts. This process is in many ways
analogous to the TranscriptInitiation process - the number of initiation
events per transcript is determined in a probabilistic manner and dependent
on the number of free ribosomal subunits, each mRNA transcript’s translation
efficiency, and the counts of each type of transcript.
"""

import numpy as np

from vivarium.core.composition import simulate_process
from ecoli.library.schema import (create_unique_indexes, numpy_schema, attrs,
    counts, bulk_name_to_idx, listener_schema)

from wholecell.utils import units
from wholecell.utils.fitting import normalize

from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess

# Register default topology for this process, associating it with process name
NAME = 'ecoli-polypeptide-initiation'
TOPOLOGY = {
    "environment": ("environment",),
    "listeners": ("listeners",),
    "active_ribosome": ("unique", "active_ribosome"),
    "RNA": ("unique", "RNA"),
    "bulk": ("bulk",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)


class PolypeptideInitiation(PartitionedProcess):
    """ Polypeptide Initiation PartitionedProcess """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'protein_lengths': [],
        'translation_efficiencies': [],
        'active_ribosome_fraction': {},
        'elongation_rates': {},
        'variable_elongation': False,
        'make_elongation_rates': lambda x: [],
        'rna_id_to_cistron_indexes': {},
        'cistron_start_end_pos_in_tu': {},
        'tu_ids': [],
        'active_ribosome_footprint_size': 24 * units.nt,
        'cistron_to_monomer_mapping': {},
        'cistron_tu_mapping_matrix': {},
        'monomer_index_to_cistron_index': {},
        'monomer_index_to_tu_indexes': {},
        'ribosome30S': 'ribosome30S',
        'ribosome50S': 'ribosome50S',
        'seed': 0,
        'monomer_ids': [],
        'emit_unique': False,
        'time_step': 1,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        # Load parameters
        self.protein_lengths = self.parameters['protein_lengths']
        self.translation_efficiencies = self.parameters[
            'translation_efficiencies']
        self.active_ribosome_fraction = self.parameters[
            'active_ribosome_fraction']
        self.ribosome_elongation_rates_dict = self.parameters[
            'elongation_rates']
        self.variable_elongation = self.parameters['variable_elongation']
        self.make_elongation_rates = self.parameters['make_elongation_rates']

        self.rna_id_to_cistron_indexes = self.parameters[
            'rna_id_to_cistron_indexes']
        self.cistron_start_end_pos_in_tu = self.parameters[
            'cistron_start_end_pos_in_tu']
        self.tu_ids = self.parameters['tu_ids']
        self.n_TUs = len(self.tu_ids)
        self.active_ribosome_footprint_size = self.parameters[
            'active_ribosome_footprint_size']

        # Get mapping from cistrons to protein monomers and TUs
        self.cistron_to_monomer_mapping = self.parameters[
            'cistron_to_monomer_mapping']
        self.cistron_tu_mapping_matrix = self.parameters[
            'cistron_tu_mapping_matrix']
        self.monomer_index_to_cistron_index = self.parameters[
            'monomer_index_to_cistron_index']
        self.monomer_index_to_tu_indexes = self.parameters[
            'monomer_index_to_tu_indexes']

        self.ribosome30S = self.parameters['ribosome30S']
        self.ribosome50S = self.parameters['ribosome50S']

        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed = self.seed)
        # Use separate random state instance to create unique indices
        # so results are directly comparable with wcEcoli
        self.unique_idx_random_state = np.random.RandomState(seed=self.seed)

        self.empty_update = {
            'listeners': {
                'ribosome_data': {
                    'ribosomes_initialized': 0,
                    'prob_translation_per_transcript': 0.0}}}
        
        self.monomer_ids = self.parameters['monomer_ids']
        
        # Helper indices for Numpy indexing
        self.ribosome30S_idx = None

    def ports_schema(self):
        return {
            'environment': {
                'media_id': {
                    '_default': '',
                    '_updater': 'set'}},
            'listeners': {
                'ribosome_data': listener_schema({
                    'did_initialize': 0,
                    'target_prob_translation_per_transcript': (
                        [0.] * len(self.monomer_ids), self.monomer_ids),
                    'actual_prob_translation_per_transcript': (
                        [0.] * len(self.monomer_ids), self.monomer_ids),
                    'mRNA_is_overcrowded': ([False] * len(self.monomer_ids),
                        self.monomer_ids),
                    'ribosome_init_event_per_monomer': (
                        [0] * len(self.monomer_ids), self.monomer_ids),
                    'effective_elongation_rate': 0.0}),
            },
            'active_ribosome': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),
            'RNA': numpy_schema('RNAs',
                emit=self.parameters['emit_unique']),
            'bulk': numpy_schema('bulk'),
            'timestep': {'_default': self.parameters['time_step']}
        }

    def calculate_request(self, timestep, states):
        if self.ribosome30S_idx is None:
            bulk_ids = states['bulk']['id']
            self.ribosome30S_idx = bulk_name_to_idx(self.ribosome30S, bulk_ids)
            self.ribosome50S_idx = bulk_name_to_idx(self.ribosome50S, bulk_ids)

        current_media_id = states['environment']['media_id']

        # requests = {'subunits': states['subunits']}
        requests = {'bulk': [
            (self.ribosome30S_idx, counts(
                states['bulk'], self.ribosome30S_idx)),
            (self.ribosome50S_idx, counts(
                states['bulk'], self.ribosome50S_idx))
        ]}

        self.fracActiveRibosome = self.active_ribosome_fraction[
            current_media_id]

        # Read ribosome elongation rate from last timestep
        self.ribosomeElongationRate = states['listeners']['ribosome_data'][
            'effective_elongation_rate']
        # If the ribosome elongation rate is zero (which is always the case for
        # the first timestep), set ribosome elongation rate to the one in
        # dictionary
        if self.ribosomeElongationRate == 0:
            self.ribosomeElongationRate = self.ribosome_elongation_rates_dict[
                current_media_id].asNumber(units.aa / units.s)
        self.elongation_rates = self.make_elongation_rates(
            self.random_state,
            self.ribosomeElongationRate,
            1,  # want elongation rate, not lengths adjusted for time step
            self.variable_elongation)

        # Ensure rates are never zero
        self.elongation_rates = np.fmax(self.elongation_rates, 1)
        return requests


    def evolve_state(self, timestep, states):
        # Calculate number of ribosomes that could potentially be initialized
        # based on counts of free 30S and 50S subunits
        inactive_ribosome_count = np.min([
            counts(states['bulk'], self.ribosome30S_idx),
            counts(states['bulk'], self.ribosome50S_idx)
        ])

        # Calculate actual number of ribosomes that should be activated based
        # on probabilities
        (TU_index_RNAs, transcript_lengths, can_translate, is_full_transcript,
            unique_index_RNAs) = attrs(states['RNA'], ['TU_index',
                'transcript_length', 'can_translate', 'is_full_transcript', 
                'unique_index'])
        TU_index_mRNAs = TU_index_RNAs[can_translate]
        length_mRNAs = transcript_lengths[can_translate]
        unique_index_mRNAs = unique_index_RNAs[can_translate]
        is_full_transcript_mRNAs = is_full_transcript[can_translate]
        is_incomplete_transcript_mRNAs = np.logical_not(
            is_full_transcript_mRNAs)

        # Calculate counts of each mRNA cistron from fully transcribed
        # transcription units
        TU_index_full_mRNAs = TU_index_mRNAs[is_full_transcript_mRNAs]
        TU_counts_full_mRNAs = np.bincount(
            TU_index_full_mRNAs, minlength=self.n_TUs)
        cistron_counts = self.cistron_tu_mapping_matrix.dot(
            TU_counts_full_mRNAs)

        # Calculate counts of each mRNA cistron from partially transcribed
        # transcription units
        TU_index_incomplete_mRNAs = TU_index_mRNAs[
            is_incomplete_transcript_mRNAs]
        length_incomplete_mRNAs = length_mRNAs[is_incomplete_transcript_mRNAs]

        for (TU_index, length) in zip(TU_index_incomplete_mRNAs,
                                      length_incomplete_mRNAs):
            cistron_indexes = self.rna_id_to_cistron_indexes(
                self.tu_ids[TU_index])
            cistron_start_positions = np.array([
                self.cistron_start_end_pos_in_tu[(cistron_index, TU_index)][0]
                for cistron_index in cistron_indexes
                ])

            cistron_counts[cistron_indexes] += length > cistron_start_positions

        # Calculate initiation probabilities for ribosomes based on mRNA counts
        # and associated mRNA translational efficiencies
        protein_init_prob = normalize(cistron_counts[
            self.cistron_to_monomer_mapping] * self.translation_efficiencies)
        target_protein_init_prob = protein_init_prob.copy()

        # Calculate actual number of ribosomes that should be activated based
        # on probabilities
        activation_prob = self.calculate_activation_prob(
            self.fracActiveRibosome,
            self.protein_lengths,
            self.elongation_rates,
            target_protein_init_prob,
            states['timestep'])

        n_ribosomes_to_activate = np.int64(activation_prob
            * inactive_ribosome_count)

        if n_ribosomes_to_activate == 0:
            update = dict(self.empty_update)
            update['active_ribosome'] = {}
            return self.empty_update
        
        # Cap the initiation probabilities at the maximum level physically
        # allowed from the known ribosome footprint sizes based on the
        # number of mRNAs
        max_p = (self.ribosomeElongationRate / self.active_ribosome_footprint_size
            * (units.s) * states['timestep'] / 
            n_ribosomes_to_activate).asNumber()
        max_p_per_protein = max_p*cistron_counts[self.cistron_to_monomer_mapping]
        is_overcrowded = (protein_init_prob > max_p_per_protein)

        while np.any(protein_init_prob > max_p_per_protein):
            protein_init_prob[is_overcrowded] = max_p_per_protein[
                is_overcrowded]
            assert protein_init_prob[~is_overcrowded].sum() != 0
            scale_the_rest_by = (
                (1. - protein_init_prob[is_overcrowded].sum())
                / protein_init_prob[~is_overcrowded].sum()
                )
            protein_init_prob[~is_overcrowded] *= scale_the_rest_by
            is_overcrowded |= (protein_init_prob > max_p_per_protein)

        # Compute actual transcription probabilities of each transcript
        actual_protein_init_prob = protein_init_prob.copy()

        # Sample multinomial distribution to determine which mRNAs have full
        # 70S ribosomes initialized on them
        n_new_proteins = self.random_state.multinomial(
            n_ribosomes_to_activate,
            protein_init_prob)

        # Build attributes for active ribosomes.
        # Each ribosome is assigned a protein index for the protein that
        # corresponds to the polypeptide it will polymerize. This is done in
        # blocks of protein ids for efficiency.
        protein_indexes = np.empty(n_ribosomes_to_activate, np.int64)
        mRNA_indexes = np.empty(n_ribosomes_to_activate, np.int64)
        positions_on_mRNA = np.empty(n_ribosomes_to_activate, np.int64)
        nonzero_count = (n_new_proteins > 0)
        start_index = 0

        for protein_index, protein_counts in zip(
                np.arange(n_new_proteins.size)[nonzero_count],
                n_new_proteins[nonzero_count]):

            # Set protein index
            protein_indexes[start_index:start_index + protein_counts] = protein_index
            
            cistron_index = self.monomer_index_to_cistron_index[protein_index]

            attribute_indexes = []
            cistron_start_positions = []

            for TU_index in self.monomer_index_to_tu_indexes[protein_index]:
                attribute_indexes_this_TU = np.where(
                    TU_index_mRNAs == TU_index)[0]
                cistron_start_position = self.cistron_start_end_pos_in_tu[
                    (cistron_index, TU_index)][0]
                is_transcript_long_enough = (length_mRNAs[
                    attribute_indexes_this_TU] >= cistron_start_position)

                attribute_indexes.extend(attribute_indexes_this_TU[
                    is_transcript_long_enough])
                cistron_start_positions.extend([cistron_start_position] * len(
                    attribute_indexes_this_TU[is_transcript_long_enough]))

            n_mRNAs = len(attribute_indexes)

            # Distribute ribosomes among these mRNAs
            n_ribosomes_per_RNA = self.random_state.multinomial(
                protein_counts, np.full(n_mRNAs, 1. / n_mRNAs))

            # Get unique indexes of each mRNA
            mRNA_indexes[start_index:start_index + protein_counts] = np.repeat(
                unique_index_mRNAs[attribute_indexes], n_ribosomes_per_RNA)
            
            positions_on_mRNA[start_index:start_index + protein_counts] = np.repeat(
                cistron_start_positions, n_ribosomes_per_RNA
                )

            start_index += protein_counts

        # Create active 70S ribosomes and assign their attributes
        ribosome_indices = create_unique_indexes(
            n_ribosomes_to_activate, self.unique_idx_random_state)
        update = {
            'bulk': [
                (self.ribosome30S_idx, -n_new_proteins.sum()),
                (self.ribosome50S_idx, -n_new_proteins.sum())
            ],
            'active_ribosome': {
                'add': {
                    'unique_index': ribosome_indices,
                    'protein_index': protein_indexes,
                    'peptide_length': np.zeros(n_ribosomes_to_activate,
                                               dtype=np.int64),
                    'mRNA_index': mRNA_indexes,
                    'pos_on_mRNA': positions_on_mRNA
                },
            },
            'listeners': {
                'ribosome_data': {
                    'did_initialize': n_new_proteins.sum(),
                    'ribosome_init_event_per_monomer': n_new_proteins,
                    'target_prob_translation_per_transcript': target_protein_init_prob,
                    'actual_prob_translation_per_transcript': actual_protein_init_prob,
                    'mRNA_is_overcrowded': is_overcrowded}}}

        return update

    def calculate_activation_prob(
            self,
            fracActiveRibosome,
            proteinLengths,
            ribosomeElongationRates,
            proteinInitProb,
            timeStepSec):
        """
        Calculates the expected ribosome termination rate based on the ribosome
        elongation rate

        Args:
            allTranslationTimes: Vector of times required to translate each
                protein
            allTranslationTimestepCounts: Vector of numbers of timesteps
                required to translate each protein
            averageTranslationTimeStepCounts: Average number of timesteps
                required to translate a protein, weighted by initiation
                probabilities
            expectedTerminationRate: Average number of terminations in one
                timestep for one protein
        """
        allTranslationTimes = 1. / ribosomeElongationRates * proteinLengths
        allTranslationTimestepCounts = np.ceil(allTranslationTimes / timeStepSec)
        averageTranslationTimestepCounts = np.dot(allTranslationTimestepCounts, proteinInitProb)
        expectedTerminationRate = 1.0 / averageTranslationTimestepCounts

        # Modify given fraction of active ribosomes to take into account early
        # terminations in between timesteps
        # allFractionTimeInactive: Vector of probabilities an "active" ribosome
        #   will in effect be "inactive" because it has terminated during a
        #   timestep
        # averageFractionTimeInactive: Average probability of an "active"
        #   ribosome being in effect "inactive", weighted by initiation
        #   probabilities
        # effectiveFracActiveRnap: New higher "goal" for fraction of active
        #   ribosomes, considering that the "effective" fraction is lower than
        #   what the listener sees
        allFractionTimeInactive = 1 - allTranslationTimes / timeStepSec / allTranslationTimestepCounts
        averageFractionTimeInactive = np.dot(allFractionTimeInactive, proteinInitProb)
        effectiveFracActiveRibosome = fracActiveRibosome * 1 / (1 - averageFractionTimeInactive)

        # Return activation probability that will balance out the expected
        # termination rate
        activationProb = effectiveFracActiveRibosome * expectedTerminationRate / (1 - effectiveFracActiveRibosome)

        # The upper bound for the activation probability is temporarily set to
        # 1.0 to prevent negative molecule counts. This will lower the fraction
        # of active ribosomes for timesteps longer than roughly 1.8s.
        if activationProb >= 1.0:
            activationProb = 1

        return activationProb


def test_polypeptide_initiation():
    def make_elongation_rates(
            self,
            random,
            base,
            time_step,
            variable_elongation=False):
        return base

    all_mRNA_ids = ['wRNA', 'xRNA', 'yRNA', 'zRNA']
    protein_lengths = np.array([25, 9, 12, 29])
    test_config = {
        'protein_lengths': protein_lengths,
        'translation_efficiencies': normalize(np.array([0.1, 0.2, 0.3, 0.4])),
        'active_ribosome_fraction': {'minimal': 0.05},
        'variable_elongation': False,
        'make_elongation_rates': make_elongation_rates,
        'rna_id_to_cistron_indexes': lambda rna_id: all_mRNA_ids.index(rna_id),
        'cistron_start_end_pos_in_tu': {
            (idx, idx): (0, protein_length*3 + 3)
            for idx, protein_length in enumerate(protein_lengths)},
        'tu_ids': all_mRNA_ids,
        'cistron_to_monomer_mapping': np.arange(4),
        'cistron_tu_mapping_matrix': np.identity(4),
        'monomer_index_to_cistron_index': {i: i for i in range(4)},
        'monomer_index_to_tu_indexes': {i: (i,) for i in range(4)},
        'protein_index_to_TU_index': np.arange(4),
        'all_TU_ids': ['wRNA', 'xRNA', 'yRNA', 'zRNA'],
        'all_mRNA_ids': ['wRNA', 'xRNA', 'yRNA', 'zRNA'],
        'ribosome30S': '30S',
        'ribosome50S': '50S',
        'seed': 0}

    polypeptide_initiation = PolypeptideInitiation(test_config)

    state = {
        'environment': {
            'media_id': 'minimal'},
        'listeners': {
            'ribosome_data': {
                'effective_elongation_rate': 25}},
        'bulk': np.array([
            ('30S', 2000),
            ('50S', 3000),
        ], dtype=[('id', 'U40'), ('count', int)]),
        'RNA': np.array([
            (1, 0, True, 0, 103, True),
            (1, 0, True, 1, 103, True),
            (1, 1, True, 2, 39, True),
            (1, 2, True, 3, 51, True),
            (1, 2, True, 4, 51, True),
            (1, 3, True, 4, 119, True),
        ], dtype=[('_entryState', np.bool_), ('TU_index', int),
            ('can_translate', np.bool_), ('unique_index', int),
            ('transcript_length', int), ('is_full_transcript', np.bool_)])
    }

    settings = {
        'total_time': 10,
        'initial_state': state}

    data = simulate_process(polypeptide_initiation, settings)

    print(data)


if __name__ == "__main__":
    test_polypeptide_initiation()
