"""
=====================
Transcript Elongation
=====================

This process models nucleotide polymerization into RNA molecules
by RNA polymerases. Polymerization occurs across all polymerases
simultaneously and resources are allocated to maximize the progress
of all polymerases up to the limit of the expected polymerase elongation
rate and available nucleotides. The termination of RNA elongation occurs
once a RNA polymerase has reached the end of the annotated gene.
"""

from os import makedirs
import numpy as np
from copy import deepcopy
import warnings

from vivarium.core.engine import Engine

from wholecell.utils.random import stochasticRound
from wholecell.utils.polymerize import buildSequences, polymerize, computeMassIncrease
from wholecell.utils import units

from ecoli.library.schema import (
    counts, attrs, numpy_schema, bulk_name_to_idx, listener_schema)
from ecoli.library.data_predicates import monotonically_increasing
from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess
from ecoli.processes.unique_update import UniqueUpdate


# Register default topology for this process, associating it with process name
NAME = 'ecoli-transcript-elongation'
TOPOLOGY = {
    "environment": ("environment",),
    "RNAs": ("unique", "RNA"),
    "active_RNAPs": ("unique", "active_RNAP"),
    "bulk": ("bulk",),
    "bulk_total": ("bulk",),
    "listeners": ("listeners",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)


def make_elongation_rates(random, rates, timestep, variable):
    return rates


def get_attenuation_stop_probabilities(trna_conc):
    return np.array([])


class TranscriptElongation(PartitionedProcess):
    """ Transcript Elongation PartitionedProcess

    defaults:
        - max_time_step (float) : ???
        - rnaPolymeraseElongationRateDict (dict): Array with elongation rate
            set points for different media environments.
        - rnaIds (array[str]) : array of names for each TU
        - rnaLengths (array[int]) : array of lengths for each TU
            (in nucleotides?)
        - rnaSequences (2D array[int]) : Array with the nucleotide sequences
            of each TU. This is in the form of a 2D array where each row is a
            TU, and each column is a position in the TU's sequence. Nucleotides
            are stored as an index {0, 1, 2, 3}, and the row is padded with
            -1's on the right to indicate where the sequence ends.
        - ntWeights (array[float]): Array of nucleotide weights
        - endWeight (array[float]): ???,
        - replichore_lengths (array[int]): lengths of replichores
            (in nucleotides?),
        - is_mRNA (array[bool]): Mask for mRNAs
        - ppi (str): ID of PPI
        - inactive_RNAP (str): ID of inactive RNAP
        - ntp_ids list[str]: IDs of ntp's (A, C, G, U)
        - variable_elongation (bool): Whether to use variable elongation.
                                      False by default.
        - make_elongation_rates: Function to make elongation rates, of the
            form: lambda random, rates, timestep, variable: rates
    """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'max_time_step': 0.0,
        # Parameters
        'rnaPolymeraseElongationRateDict': {},
        'rnaIds': [],
        'rnaLengths': np.array([]),
        'rnaSequences': np.array([[]]),
        'ntWeights': np.array([]),
        'endWeight': np.array([]),
        'replichore_lengths': np.array([]),
        'n_fragment_bases': 0,
        'recycle_stalled_elongation': False,
        'submass_indices': {},
        # mask for mRNAs
        'is_mRNA': np.array([]),
        # Bulk molecules
        'inactive_RNAP': '',
        'ppi': '',
        'ntp_ids': [],
        'variable_elongation': False,
        'make_elongation_rates': make_elongation_rates,
        'fragmentBases': [],
        'polymerized_ntps': [],
        'charged_trnas': [],
        # Attenuation
        'trna_attenuation': False,
        'cell_density': 1100 * units.g / units.L,
        'n_avogadro':  6.02214076e+23 / units.mol,
        'get_attenuation_stop_probabilities': (
            get_attenuation_stop_probabilities),
        'attenuated_rna_indices': np.array([], dtype=int),
        'location_lookup': {},

        'seed': 0,
        'emit_unique': False,
        'time_step': 1
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        self.max_time_step = self.parameters['max_time_step']

        # Load parameters
        self.rnaPolymeraseElongationRateDict = self.parameters[
            'rnaPolymeraseElongationRateDict']
        self.rnaIds = self.parameters['rnaIds']
        self.rnaLengths = self.parameters['rnaLengths']
        self.rnaSequences = self.parameters['rnaSequences']
        self.ppi = self.parameters['ppi']
        self.inactive_RNAP = self.parameters['inactive_RNAP']
        self.fragmentBases = self.parameters['fragmentBases']
        self.charged_trnas = self.parameters['charged_trnas']
        self.ntp_ids = self.parameters['ntp_ids']
        self.ntWeights = self.parameters['ntWeights']
        self.endWeight = self.parameters['endWeight']
        self.replichore_lengths = self.parameters['replichore_lengths']
        self.chromosome_length = self.replichore_lengths.sum()
        self.n_fragment_bases = self.parameters['n_fragment_bases']
        self.recycle_stalled_elongation = self.parameters[
            'recycle_stalled_elongation']

        # Mask for mRNAs
        self.is_mRNA = self.parameters['is_mRNA']

        self.variable_elongation = self.parameters['variable_elongation']
        self.make_elongation_rates = self.parameters['make_elongation_rates']

        self.polymerized_ntps = self.parameters['polymerized_ntps']
        self.charged_trna_names = self.parameters['charged_trnas']

        # Attenuation
        self.trna_attenuation = self.parameters['trna_attenuation']
        self.cell_density = self.parameters['cell_density']
        self.n_avogadro = self.parameters['n_avogadro']
        self.stop_probabilities = self.parameters[
            'get_attenuation_stop_probabilities']
        self.attenuated_rna_indices = self.parameters['attenuated_rna_indices']
        self.attenuated_rna_indices_lookup = {idx: i
            for i, idx in enumerate(self.attenuated_rna_indices)}
        self.attenuated_rnas = self.rnaIds[self.attenuated_rna_indices]
        self.location_lookup = self.parameters['location_lookup']

        # random seed
        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed=self.seed)

        # Helper indices for Numpy indexing
        self.bulk_RNA_idx = None

    def ports_schema(self):
        return {
            'environment': {
                'media_id': {'_default': ''}
            },

            'RNAs': numpy_schema('RNAs',
                emit=self.parameters['emit_unique']),
            'active_RNAPs': numpy_schema('active_RNAPs',
                emit=self.parameters['emit_unique']),

            'bulk': numpy_schema('bulk'),
            'bulk_total': numpy_schema('bulk'),

            'listeners': {
                'mass': listener_schema({'cell_mass': 0.0}),
                'transcript_elongation_listener': listener_schema({
                    'count_NTPs_used': 0,
                    'count_rna_synthesized': (
                        [0] * len(self.rnaIds), self.rnaIds),
                    'attenuation_probability': (
                        [0.] * len(self.attenuated_rnas), self.attenuated_rnas),
                    'counts_attenuated': (
                        [0] * len(self.attenuated_rnas), self.attenuated_rnas)}),
                'growth_limits': listener_schema({
                    'ntp_used': ([0] * len(self.ntp_ids), self.ntp_ids),
                    'ntp_pool_size': ([0] * len(self.ntp_ids), self.ntp_ids),
                    'ntp_request_size': ([0] * len(self.ntp_ids), self.ntp_ids),
                    'ntp_allocated': ([0] * len(self.ntp_ids), self.ntp_ids)}),
                'rnap_data': listener_schema({
                    'actual_elongations': 0,
                    'did_terminate': 0,
                    'termination_loss': 0,
                    'did_stall': 0})
            },
            'timestep': {'_default': self.parameters['time_step']}
        }

    def calculate_request(self, timestep, states):
        # At first update, convert all strings to indices
        if self.bulk_RNA_idx is None:
            bulk_ids = states['bulk']['id']
            self.bulk_RNA_idx = bulk_name_to_idx(self.rnaIds, bulk_ids)
            self.ntps_idx = bulk_name_to_idx(self.ntp_ids, bulk_ids)
            self.ppi_idx = bulk_name_to_idx(self.ppi, bulk_ids)
            self.inactive_RNAP_idx = bulk_name_to_idx(
                self.inactive_RNAP, bulk_ids)
            self.fragmentBases_idx = bulk_name_to_idx(
                self.fragmentBases, bulk_ids)
            self.charged_trnas_idx = bulk_name_to_idx(
                self.charged_trnas, bulk_ids)

        # Calculate elongation rate based on the current media
        current_media_id = states['environment']['media_id']

        self.rnapElongationRate = self.rnaPolymeraseElongationRateDict[
            current_media_id].asNumber(units.nt / units.s)

        self.elongation_rates = self.make_elongation_rates(
            self.random_state,
            self.rnapElongationRate,
            states['timestep'],
            self.variable_elongation)

        # If there are no active RNA polymerases, return immediately
        if states['active_RNAPs']['_entryState'].sum() == 0:
            return {}

        # Determine total possible sequences of nucleotides that can be
        # transcribed in this time step for each partial transcript
        TU_indexes, transcript_lengths, is_full_transcript = attrs(
            states['RNAs'],
            ['TU_index', 'transcript_length', 'is_full_transcript'])
        is_partial_transcript = np.logical_not(is_full_transcript)
        TU_indexes_partial = TU_indexes[is_partial_transcript]
        transcript_lengths_partial = transcript_lengths[is_partial_transcript]

        sequences = buildSequences(
            self.rnaSequences,
            TU_indexes_partial,
            transcript_lengths_partial,
            self.elongation_rates)

        sequenceComposition = np.bincount(
            sequences[sequences != polymerize.PAD_VALUE], minlength = 4)

        # Calculate if any nucleotides are limited and request up to the number
        # in the sequences or number available
        ntpsTotal = counts(states['bulk'], self.ntps_idx)
        maxFractionalReactionLimit = np.fmin(
            1, ntpsTotal / sequenceComposition)

        requests = {'bulk': [(self.ntps_idx,
            (maxFractionalReactionLimit * sequenceComposition).astype(int))]}

        requests['listeners'] = {
            'growth_limits': {
                'ntp_pool_size': counts(states['bulk'], self.ntps_idx),
                'ntp_request_size': (maxFractionalReactionLimit 
                    * sequenceComposition).astype(int)
            }
        }

        return requests

    def evolve_state(self, timestep, states):
        ntpCounts = counts(states['bulk'], self.ntps_idx)

        # If there are no active RNA polymerases, return immediately
        if states['active_RNAPs']['_entryState'].sum() == 0:
            return {
                'listeners': {
                    'transcript_elongation_listener': {
                        'count_NTPs_used': 0,
                        'count_rna_synthesized': np.zeros(len(self.rnaIds))
                    },
                    'growth_limits': {
                        'ntp_used': np.zeros(len(self.ntp_ids)),
                        'ntp_allocated': ntpCounts,
                    },
                    'rnap_data': {
                        'actual_elongations': 0,
                        'did_terminate': 0,
                        'termination_loss': 0
                    }
                },
                'active_RNAPs': {},
                'RNAs': {}
            }

        # Get attributes from existing RNAs
        TU_index_all_RNAs, length_all_RNAs, is_full_transcript, \
            is_mRNA_all_RNAs, RNAP_index_all_RNAs = attrs(states['RNAs'],
                ['TU_index', 'transcript_length', 'is_full_transcript',
                'is_mRNA', 'RNAP_index'])

        update = {
            'listeners': {
                'growth_limits': {}}}

        # Determine sequences of RNAs that should be elongated
        is_partial_transcript = np.logical_not(is_full_transcript)
        partial_transcript_indexes = np.where(is_partial_transcript)[0]
        TU_index_partial_RNAs = TU_index_all_RNAs[is_partial_transcript]
        length_partial_RNAs = length_all_RNAs[is_partial_transcript]
        is_mRNA_partial_RNAs = is_mRNA_all_RNAs[is_partial_transcript]
        RNAP_index_partial_RNAs = RNAP_index_all_RNAs[is_partial_transcript]

        if self.trna_attenuation:
            cell_mass = states['listeners']['mass']['cell_mass']
            cellVolume = cell_mass * units.fg / self.cell_density
            counts_to_molar = 1 / (self.n_avogadro * cellVolume)
            attenuation_probability = self.stop_probabilities(counts_to_molar
                * counts(states['bulk_total'], self.charged_trnas_idx))
            prob_lookup = {tu: prob for tu, prob in zip(
                self.attenuated_rna_indices, attenuation_probability)}
            tu_stop_probability = np.array([prob_lookup.get(idx, 0) *
                (length < self.location_lookup.get(idx, 0)) for idx, length
                in zip(TU_index_partial_RNAs, length_partial_RNAs)
            ])
            rna_to_attenuate = stochasticRound(
                self.random_state, tu_stop_probability).astype(bool)
        else:
            attenuation_probability = np.zeros(len(self.attenuated_rna_indices))
            rna_to_attenuate = np.zeros(len(TU_index_partial_RNAs), bool)
        rna_to_elongate = ~rna_to_attenuate

        sequences = buildSequences(
            self.rnaSequences,
            TU_index_partial_RNAs,
            length_partial_RNAs,
            self.elongation_rates)

        # Polymerize transcripts based on sequences and available nucleotides
        reactionLimit = ntpCounts.sum()
        result = polymerize(
            sequences[rna_to_elongate],
            ntpCounts,
            reactionLimit,
            self.random_state,
            self.elongation_rates[TU_index_partial_RNAs][rna_to_elongate],
            self.variable_elongation
        )

        sequence_elongations = np.zeros_like(length_partial_RNAs)
        sequence_elongations[rna_to_elongate] = result.sequenceElongation
        ntps_used = result.monomerUsages
        did_stall_mask = result.sequences_limited_elongation

        # Calculate changes in mass associated with polymerization
        added_mass = computeMassIncrease(sequences, sequence_elongations,
            self.ntWeights)
        did_initialize = (length_partial_RNAs == 0) & (sequence_elongations > 0)
        added_mass[did_initialize] += self.endWeight

        # Calculate updated transcript lengths
        updated_transcript_lengths = length_partial_RNAs + sequence_elongations

        # Get attributes of active RNAPs
        coordinates, is_forward, RNAP_unique_index = attrs(
            states['active_RNAPs'],
            ['coordinates', 'is_forward', 'unique_index'])

        # Active RNAP count should equal partial transcript count
        assert len(RNAP_unique_index) == len(RNAP_index_partial_RNAs)

        # All partial RNAs must be linked to an RNAP
        assert (np.count_nonzero(RNAP_index_partial_RNAs == -1) == 0)

        # Get mapping indexes between partial RNAs to RNAPs
        partial_RNA_to_RNAP_mapping, _ = get_mapping_arrays(
            RNAP_index_partial_RNAs, RNAP_unique_index)

        # Rescale boolean array of directions to an array of 1's and -1's.
        # True is converted to 1, False is converted to -1.
        direction_rescaled = (2*(is_forward - 0.5)).astype(np.int64)

        # Compute the updated coordinates of RNAPs. Coordinates of RNAPs
        # moving in the positive direction are increased, whereas coordinates
        # of RNAPs moving in the negative direction are decreased.
        updated_coordinates = coordinates + np.multiply(direction_rescaled,
            sequence_elongations[partial_RNA_to_RNAP_mapping])

        # Reset coordinates of RNAPs that cross the boundaries between right
        # and left replichores
        updated_coordinates[
            updated_coordinates > self.replichore_lengths[0]
            ] -= self.chromosome_length
        updated_coordinates[
            updated_coordinates < -self.replichore_lengths[1]
            ] += self.chromosome_length

        # Update transcript lengths of RNAs and coordinates of RNAPs
        length_all_RNAs[is_partial_transcript] = updated_transcript_lengths

        # Update added submasses of RNAs. Masses of partial mRNAs are counted
        # as mRNA mass as they are already functional, but the masses of other
        # types of partial RNAs are counted as nonspecific RNA mass.
        added_nsRNA_mass_all_RNAs = np.zeros_like(
            TU_index_all_RNAs, dtype=np.float64)
        added_mRNA_mass_all_RNAs = np.zeros_like(
            TU_index_all_RNAs, dtype=np.float64)

        added_nsRNA_mass_all_RNAs[is_partial_transcript] = np.multiply(
            added_mass, np.logical_not(is_mRNA_partial_RNAs))
        added_mRNA_mass_all_RNAs[is_partial_transcript] = np.multiply(
            added_mass, is_mRNA_partial_RNAs)

        # Determine if transcript has reached the end of the sequence
        terminal_lengths = self.rnaLengths[TU_index_partial_RNAs]
        did_terminate_mask = (updated_transcript_lengths == terminal_lengths)
        terminated_RNAs = np.bincount(
            TU_index_partial_RNAs[did_terminate_mask],
            minlength = self.rnaSequences.shape[0])

        # Update is_full_transcript attribute of RNAs
        is_full_transcript_updated = is_full_transcript.copy()
        is_full_transcript_updated[
            partial_transcript_indexes[did_terminate_mask]] = True

        n_terminated = did_terminate_mask.sum()
        n_initialized = did_initialize.sum()
        n_elongations = ntps_used.sum()

        # Get counts of new bulk RNAs
        n_new_bulk_RNAs = terminated_RNAs.copy()
        n_new_bulk_RNAs[self.is_mRNA] = 0

        update['RNAs'] = {
            'set': {
                'transcript_length': length_all_RNAs,
                'is_full_transcript': is_full_transcript_updated,
                'massDiff_nonspecific_RNA': attrs(states['RNAs'],
                    ['massDiff_nonspecific_RNA'])[0] \
                        + added_nsRNA_mass_all_RNAs,
                'massDiff_mRNA': attrs(states['RNAs'],
                    ['massDiff_mRNA'])[0] + added_mRNA_mass_all_RNAs
            },
            'delete': partial_transcript_indexes[np.logical_and(
                did_terminate_mask, np.logical_not(is_mRNA_partial_RNAs))],
            
        }
        update['active_RNAPs'] = {
            'set': {
                'coordinates': updated_coordinates
            },
            'delete': np.where(did_terminate_mask[
                partial_RNA_to_RNAP_mapping])[0],
            
        }

        # Attenuation removes RNAs and RNAPs
        counts_attenuated = np.zeros(len(self.attenuated_rna_indices), dtype=int)
        if np.any(rna_to_attenuate):
            for idx in TU_index_partial_RNAs[rna_to_attenuate]:
                counts_attenuated[self.attenuated_rna_indices_lookup[idx]] += 1
            update['RNAs']['delete'] = np.append(
                update['RNAs']['delete'],
                partial_transcript_indexes[rna_to_attenuate]
            )
            update['active_RNAPs']['delete'] = np.append(
                update['active_RNAPs']['delete'],
                np.where(rna_to_attenuate[partial_RNA_to_RNAP_mapping])[0]
            )
        n_attenuated = rna_to_attenuate.sum()

        # Handle stalled elongation
        n_total_stalled = did_stall_mask.sum()
        if self.recycle_stalled_elongation and (n_total_stalled > 0):
            # Remove RNAPs that were bound to stalled elongation transcripts
            # and increment counts of inactive RNAPs
            update['active_RNAPs']['delete'] = np.append(
                update['active_RNAPs']['delete'],
                np.where(did_stall_mask[partial_RNA_to_RNAP_mapping])[0]
            )
            update['bulk'].append((self.inactive_RNAP_idx, n_total_stalled))

            # Remove partial transcripts from stalled elongation
            update['RNAs']['delete'] = np.append(
                update['RNAs']['delete'],
                partial_transcript_indexes[did_stall_mask]
            )
            stalled_sequence_lengths = updated_transcript_lengths[
                did_stall_mask]
            n_initiated_sequences = np.count_nonzero(stalled_sequence_lengths)

            if n_initiated_sequences > 0:
                # Get the full sequence of stalled transcripts
                stalled_sequences = buildSequences(
                    self.rnaSequences,
                    TU_index_partial_RNAs[did_stall_mask],
                    np.zeros(n_total_stalled, dtype=np.int64),
                    np.full(n_total_stalled, updated_transcript_lengths.max()))

                # Count the number of fragment bases in these transcripts up
                # until the stalled length
                base_counts = np.zeros(self.n_fragment_bases, dtype=np.int64)
                for sl, seq in zip(stalled_sequence_lengths,
                    stalled_sequences
                ):
                    base_counts += np.bincount(seq[:sl],
                        minlength=self.n_fragment_bases)

                # Increment counts of fragment NTPs and phosphates
                update['bulk'].append((self.fragmentBases_idx, base_counts))
                update['bulk'].append((self.ppi_idx, n_initiated_sequences))

        update.setdefault('bulk', []).append((self.ntps_idx, -ntps_used))
        update['bulk'].append((self.bulk_RNA_idx, n_new_bulk_RNAs))
        update['bulk'].append((self.inactive_RNAP_idx,
            n_terminated + n_attenuated))
        update['bulk'].append((self.ppi_idx, n_elongations - n_initialized))

        # Write outputs to listeners
        update['listeners']['transcript_elongation_listener'] = {
            "count_rna_synthesized": terminated_RNAs,
            "count_NTPs_used": n_elongations,
            "attenuation_probability": attenuation_probability,
            "counts_attenuated": counts_attenuated}
        update['listeners']['growth_limits'] = {
            "ntp_used": ntps_used}
        update['listeners']['rnap_data'] = {
            "actual_elongations": sequence_elongations.sum(),
            "did_terminate": did_terminate_mask.sum(),
            "termination_loss": (terminal_lengths - length_partial_RNAs)[
                did_terminate_mask].sum(),
            "did_stall": n_total_stalled}

        return update

    def isTimeStepShortEnough(self, inputTimeStep, timeStepSafetyFraction):
        return inputTimeStep <= self.max_time_step


def get_mapping_arrays(x, y):
    """
    Returns the array of indexes of each element of array x in array y, and
    vice versa. Assumes that the elements of x and y are unique, and
    set(x) == set(y).
    """
    def argsort_unique(idx):
        """
        Quicker argsort for arrays that are permutations of np.arange(n).
        """
        n = idx.size
        argsort_idx = np.empty(n, dtype=np.int64)
        argsort_idx[idx] = np.arange(n)
        return argsort_idx

    x_argsort = np.argsort(x)
    y_argsort = np.argsort(y)

    x_to_y = x_argsort[argsort_unique(y_argsort)]
    y_to_x = y_argsort[argsort_unique(x_argsort)]

    return x_to_y, y_to_x


def format_data(data, bulk_ids, rna_dtypes, rnap_dtypes, submass_dtypes):
    # Format unique and bulk data for assertions
    data['unique']['RNA'] = [np.array(list(map(tuple, zip(*val))),
        dtype=rna_dtypes + submass_dtypes) for val in data['unique']['RNA']]
    data['unique']['active_RNAP'] = [np.array(list(map(tuple, zip(*val))),
            dtype=rnap_dtypes + submass_dtypes)
        for val in data['unique']['active_RNAP']]
    bulk_timeseries = np.array(data['bulk'])
    data['bulk'] = {
        bulk_id: bulk_timeseries[:, i]
        for i, bulk_id in enumerate(bulk_ids)
    }
    return data


def test_transcript_elongation():
    def make_elongation_rates(random, base, time_step, variable_elongation=False):
        size = 9  # number of TUs
        lengths = time_step * np.full(size, base, dtype=np.int64)
        lengths = stochasticRound(random, lengths) if random else np.round(lengths)

        return lengths.astype(np.int64)

    test_config = TranscriptElongation.defaults
    test_config['max_time_step'] = 2.0

    with open('data/elongation_sequences.npy', 'rb') as f:
        sequences = np.load(f)

    rna_schema = numpy_schema('RNAs', emit=True)
    rnap_schema = numpy_schema('active_RNAPs', emit=True)

    test_config = {
        'max_time_step': 2.0,
        'rnaPolymeraseElongationRateDict': {'minimal' : 49.24 * units.nt / units.s},
        'rnaIds': np.array(['16S rRNA', '23S rRNA', '5S rRNA', 'mRNA']),
        'rnaLengths': np.array([1542, 2905, 120, 1080]),
        'rnaSequences': sequences,
        'ntWeights': np.array([5.44990582e-07, 5.05094471e-07, 5.71557547e-07, 5.06728441e-07]),
        'endWeight': np.array([2.90509649e-07]),
        'replichore_lengths': np.array([2322985, 2316690]),
        'idx_16S_rRNA': np.array([0]),
        'idx_23S_rRNA': np.array([1]),
        'idx_5S_rRNA': np.array([2]),
        'is_mRNA': np.array([False, False, False, True]),
        'ppi': 'PPI[c]',
        'inactive_RNAP': 'APORNAP-CPLX[c]',
        'ntp_ids': ['ATP[c]', 'CTP[c]', 'GTP[c]', 'UTP[c]'],
        'variable_elongation': False,
        'make_elongation_rates': make_elongation_rates,
        'seed': 0,
        # Make sure to emit RNA and RNAP data
        '_schema': {'RNAs': rna_schema, 'active_RNAPs': rnap_schema}}

    transcript_elongation = TranscriptElongation(test_config)
    # Need to add UniqueUpdate Step so unique molecule are updated each timestep
    unique_topo = {
        'RNAs': ('unique', 'RNA'),
        'active_RNAPs': ('unique', 'active_RNAP')
    }
    unique_update = UniqueUpdate({'unique_topo': unique_topo})

    submass_dtypes = [('massDiff_DNA', '<f8'), ('massDiff_mRNA', '<f8'),
        ('massDiff_metabolite', '<f8'), ('massDiff_miscRNA', '<f8'),
        ('massDiff_nonspecific_RNA', '<f8'), ('massDiff_protein', '<f8'),
        ('massDiff_rRNA', '<f8'), ('massDiff_tRNA', '<f8'),
        ('massDiff_water', '<f8')]
    rna_dtypes = [('_entryState', np.bool_), ('unique_index', int),
        ('TU_index', int), ('transcript_length', int),
        ('is_mRNA', np.bool_), ('is_full_transcript', np.bool_),
        ('can_translate', np.bool_), ('RNAP_index', int)]
    rnap_dtypes = [('_entryState', np.bool_), ('unique_index', int),
        ('domain_index', int), ('coordinates', int), 
        ('is_forward', np.bool_)]
    initial_state = {
        'environment': {'media_id': 'minimal'},
        'unique': {
            'RNA': np.array([
                (1, i, i, 0, test_config['is_mRNA'][i], False, True, i) + \
                (0,) * 9
                for i in range(len(test_config['rnaIds']))
            ], dtype=rna_dtypes + submass_dtypes),

            'active_RNAP': np.array([
                (1, i, 2, i*1000, True) + (0,) * 9
                for i in range(4)
            ], dtype=rnap_dtypes + submass_dtypes),
        },

        'bulk': np.array([
            ('16S rRNA', 0),
            ('23S rRNA', 0),
            ('5S rRNA', 0),
            ('mRNA', 0),
            ('ATP[c]', 6178058),
            ('CTP[c]', 1152211),
            ('GTP[c]', 1369694),
            ('UTP[c]', 3024874),
            ('PPI[c]', 320771),
            ('APORNAP-CPLX[c]', 2768)
        ], dtype=[('id', 'U40'), ('count', int)]),
    }

    settings = {
        'processes': {
            'unique-update': unique_update,
            'ecoli-transcript-elongation': transcript_elongation,
        },
        'topology': {
            'unique-update': unique_topo,
            'ecoli-transcript-elongation': TOPOLOGY}}

    # Since unique numpy updater is an class method, internal
    # deepcopying in vivarium-core causes this warning to appear
    warnings.filterwarnings("ignore",
        message="Incompatible schema assignment at ")
    engine = Engine(**settings, initial_state=deepcopy(initial_state))
    engine.run_for(100)
    data = engine.emitter.get_timeseries()
    bulk_ids = initial_state['bulk']['id']
    data = format_data(data, bulk_ids, rna_dtypes,
        rnap_dtypes, submass_dtypes)

    plots(data)
    assertions(test_config, data)

    # Test running out of ntps

    initial_state['bulk'] = np.array([
            ('16S rRNA', 0),
            ('23S rRNA', 0),
            ('5S rRNA', 0),
            ('mRNA', 0),
            ('ATP[c]', 100),
            ('CTP[c]', 100),
            ('GTP[c]', 100),
            ('UTP[c]', 100),
            ('PPI[c]', 320771),
            ('APORNAP-CPLX[c]', 2768)
        ], dtype=[('id', 'U40'), ('count', int)])

    engine = Engine(**settings, initial_state=deepcopy(initial_state))
    engine.run_for(100)
    data = engine.emitter.get_timeseries()
    data = format_data(data, bulk_ids, rna_dtypes,
        rnap_dtypes, submass_dtypes)

    plots(data, "transcript_elongation_toymodel_100_ntps.png")
    assertions(test_config, data)

    # Test no ntps

    initial_state['bulk'] = np.array([
            ('16S rRNA', 0),
            ('23S rRNA', 0),
            ('5S rRNA', 0),
            ('mRNA', 0),
            ('ATP[c]', 0),
            ('CTP[c]', 0),
            ('GTP[c]', 0),
            ('UTP[c]', 0),
            ('PPI[c]', 320771),
            ('APORNAP-CPLX[c]', 2768)
        ], dtype=[('id', 'U40'), ('count', int)])

    # Since unique numpy updater is an class method, internal
    # deepcopying in vivarium-core causes this warning to appear
    warnings.filterwarnings("ignore",
        message="Incompatible schema assignment at ")
    engine = Engine(**settings, initial_state=deepcopy(initial_state))
    engine.run_for(100)
    data = engine.emitter.get_timeseries()
    data = format_data(data, bulk_ids, rna_dtypes,
        rnap_dtypes, submass_dtypes)

    plots(data, "transcript_elongation_toymodel_no_ntps.png")
    assertions(test_config, data)


def plots(actual_update, filename="transcript_elongation_toymodel.png"):
    import matplotlib.pyplot as plt

    # unpack update
    rnas_synthesized = actual_update['listeners'][
        'transcript_elongation_listener']['count_rna_synthesized']
    ntps_used = actual_update['listeners']['growth_limits']['ntp_used']

    plt.figure()

    plt.subplot(2, 1, 1)
    plt.plot(range(len(rnas_synthesized)), rnas_synthesized)
    plt.xlabel("TU")
    plt.ylabel("Count")
    plt.title("Counts synthesized")

    plt.subplot(2, 1, 2)
    t = np.array(actual_update['time'])
    width = 0.25
    for i, ntp in enumerate(np.array(ntps_used).transpose()):
        plt.bar(t + (i-2) * width, ntp, width, label=str(i))
    plt.ylabel('Count')
    plt.title('NTP Counts Used')
    plt.legend()

    plt.subplots_adjust(hspace=0.5)
    plt.gcf().set_size_inches(10, 6)
    makedirs(f"out/migration", exist_ok=True)
    plt.savefig(f"out/migration/{filename}")


def assertions(config, actual_update):
    # unpack update
    trans_lengths = np.array([r['transcript_length'] for r in actual_update['unique']['RNA']]).T
    rnas_synthesized = actual_update['listeners']['transcript_elongation_listener']['count_rna_synthesized']
    bulk_16SrRNA = actual_update['bulk']['16S rRNA']
    bulk_5SrRNA = actual_update['bulk']['5S rRNA']
    bulk_23SrRNA = actual_update['bulk']['23S rRNA']
    bulk_mRNA = actual_update['bulk']['mRNA']

    ntps_used = actual_update['listeners']['growth_limits']['ntp_used']
    total_ntps_used = actual_update['listeners']['transcript_elongation_listener']['count_NTPs_used']

    RNAP_coordinates = np.array([v['coordinates'] for v in actual_update['unique']['active_RNAP']]).T
    RNAP_elongations = actual_update['listeners']['rnap_data']['actual_elongations']
    terminations = actual_update['listeners']['rnap_data']['did_terminate']

    ppi = actual_update['bulk']['PPI[c]']
    inactive_RNAP = actual_update['bulk']['APORNAP-CPLX[c]']


    # transcript lengths are monotonically increasing
    assert np.all(list(map(lambda x: monotonically_increasing(x[x!=0]), trans_lengths)))

    # RNAP positions are monotonically increasing
    assert np.all(list(map(lambda x: monotonically_increasing(x[x!=0]), RNAP_coordinates)))

    # RNAP positions match transcript lengths?
    RNAP_coordinates_realigned = []
    for v in RNAP_coordinates:
        diff = np.array(v) - v[0]
        RNAP_coordinates_realigned.append(diff[diff >= 0])
    for t_length, expect, mRNA in zip(trans_lengths, RNAP_coordinates_realigned, config['is_mRNA']):
        t_length = t_length[:len(expect)]
        np.testing.assert_array_equal(t_length, expect)

    # bulk RNAs monotonically increasing?
    assert monotonically_increasing(bulk_16SrRNA)
    assert monotonically_increasing(bulk_5SrRNA)
    assert monotonically_increasing(bulk_23SrRNA)
    assert monotonically_increasing(bulk_mRNA)

    # Change in PPI matches #elongations - #initiations
    d_ppi = np.array(ppi)[1:] - ppi[:-1]
    expected_d_ppi = RNAP_elongations[1:]
    # Hacky way to find number of initiations
    # Assuming initiations only happen in the first time step (valid for toy model,
    # where there is no initiation process)
    expected_d_ppi[0] -= np.sum((np.array([t[0] for t in trans_lengths]) == 0) & (np.array([t[1] for t in trans_lengths]) > 0))

    np.testing.assert_array_equal(d_ppi, expected_d_ppi)

    # Change in APORNAP-CPLX matches terminations
    d_inactive_RNAP = np.array(inactive_RNAP[1:]) - inactive_RNAP[:-1]
    np.testing.assert_array_equal(d_inactive_RNAP, terminations[1:])

    # RNAP elongations matches total ntps used at each timestep
    np.testing.assert_array_equal(RNAP_elongations, [sum(v) for v in ntps_used])

    # terminations match rnas_synthesized
    assert all(np.array([sum(v) for v in rnas_synthesized]) == terminations)
    d_16SrRNA = np.array(bulk_16SrRNA)[1:] - bulk_16SrRNA[:-1]
    d_5SrRNA = np.array(bulk_5SrRNA)[1:] - bulk_5SrRNA[:-1]
    d_23SrRNA = np.array(bulk_23SrRNA)[1:] - bulk_23SrRNA[:-1]
    np.testing.assert_array_equal(d_16SrRNA, np.array(rnas_synthesized)[:, config['idx_16S_rRNA']].transpose()[0, 1:])
    np.testing.assert_array_equal(d_5SrRNA, np.array(rnas_synthesized)[:, config['idx_5S_rRNA']].transpose()[0, 1:])
    np.testing.assert_array_equal(d_23SrRNA, np.array(rnas_synthesized)[:, config['idx_23S_rRNA']].transpose()[0, 1:])

    # bulk NTP counts decrease by numbers used
    ntps_arr = np.array([actual_update['bulk'][ntp] for ntp in ['ATP[c]', 'CTP[c]', 'GTP[c]', 'UTP[c]']])
    d_ntps = ntps_arr[:, 1:] - ntps_arr[:, :-1]

    np.testing.assert_array_equal(d_ntps, -np.array(ntps_used[1:]).transpose())

    # total NTPS used matches sum of ntps_used,
    # total of each type of NTP used matches rna sequences
    assert np.all(np.sum(ntps_used, axis=1) == np.array(total_ntps_used))
    actual = np.sum(ntps_used, axis=0)
    n_term = np.sum(rnas_synthesized, axis=0)
    sequence_ntps = np.array([[sum(seq==0), sum(seq==1), sum(seq==2), sum(seq==3)]
                              for seq in config['rnaSequences']])
    expect = np.array([np.array(seq) * n
                       for seq, n in zip(sequence_ntps, n_term)
                       ]).sum(axis=0)
    partial = np.array([0 < t_length[-1] < final_length   # length > 0 and not completed
                        for t_length, final_length
                        in zip(trans_lengths, config['rnaLengths'])])

    for a, e, is_partial in zip(actual, expect, partial):
        if is_partial:
            assert a >= e
        else:
            assert a == e


if __name__ == "__main__":
    test_transcript_elongation()
