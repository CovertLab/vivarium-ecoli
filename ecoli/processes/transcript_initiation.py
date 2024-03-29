"""
=====================
Transcript Initiation
=====================

This process models the binding of RNA polymerase to each gene.
The number of RNA polymerases to activate in each time step is determined
such that the average fraction of RNA polymerases that are active throughout
the simulation matches measured fractions, which are dependent on the
cellular growth rate. This is done by assuming a steady state concentration
of active RNA polymerases.

TODO:
  - use transcription units instead of single genes
  - match sigma factors to promoters
"""

import numpy as np
import scipy.sparse
import matplotlib.pyplot as plt
from typing import cast

from vivarium.core.composition import simulate_process

from ecoli.library.schema import (
    create_unique_indexes, listener_schema,
    numpy_schema, counts, attrs, bulk_name_to_idx
)

from wholecell.utils import units
from wholecell.utils.random import stochasticRound
from wholecell.utils.unit_struct_array import UnitStructArray

from ecoli.library.data_predicates import (
    monotonically_decreasing, all_nonnegative)
from scipy.stats import chisquare

from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess


# Register default topology for this process, associating it with process name
NAME = 'ecoli-transcript-initiation'
TOPOLOGY = {
    "environment": ("environment",),
    "full_chromosomes": ("unique", "full_chromosome"),
    "RNAs": ("unique", "RNA"),
    "active_RNAPs": ("unique", "active_RNAP"),
    "promoters": ("unique", "promoter"),
    "bulk": ("bulk",),
    "listeners": ("listeners",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)


class TranscriptInitiation(PartitionedProcess):
    """ Transcript Initiation PartitionedProcess

    **Defaults:**
    
    - **fracActiveRnapDict** (``dict``): Dictionary with keys corresponding to
      media, values being the fraction of active RNA Polymerase (RNAP)
      for that media.
    - **rnaLengths** (``numpy.ndarray[int]``): lengths of RNAs for each transcription
      unit (TU), in nucleotides
    - **rnaPolymeraseElongationRateDict** (``dict``): Dictionary with keys
      corresponding to media, values being RNAP's elongation rate in
      that media, in nucleotides/s
    - **variable_elongation** (``bool``): Whether to add amplified elongation rates
      for rRNAs. False by default.
    - **make_elongation_rates** (``func``): Function for making elongation rates
      (see :py:meth:`~reconstruction.ecoli.dataclasses.process.transcription.Transcription.make_elongation_rates`).
      Takes PRNG, basal elongation rate, timestep, and ``variable_elongation``. 
      Returns an array of elongation rates for all genes.
    - **active_rnap_footprint_size** (``int``): Maximum physical footprint of RNAP
      in nucleotides to cap initiation probabilities
    - **basal_prob** (``numpy.ndarray[float]``): Baseline probability of synthesis for
      every TU.
    - **delta_prob** (``dict``): Dictionary with four keys, used to create a matrix
      encoding the effect of transcription factors (TFs) on transcription
      probabilities::

        {'deltaV' (np.ndarray[float]): deltas associated with the effects of
            TFs on TUs,
        'deltaI' (np.ndarray[int]): index of the affected TU for each delta,
        'deltaJ' (np.ndarray[int]): index of the acting TF for each delta,
        'shape' (tuple): (m, n) = (# of TUs, # of TFs)}

    - **perturbations** (``dict``): Dictionary of genetic perturbations (optional,
      can be empty)
    - **rna_data** (``numpy.ndarray``): Structured array with an entry for each TU,
      where entries look like::

            (id, deg_rate, length (nucleotides), counts_AGCU, mw
            (molecular weight), is_mRNA, is_miscRNA, is_rRNA, is_tRNA,
            is_23S_rRNA, is_16S_rRNA, is_5S_rRNA, is_ribosomal_protein,
            is_RNAP, gene_id, Km_endoRNase, replication_coordinate,
            direction)

    - **idx_rRNA** (``numpy.ndarray[int]``): indexes of TUs corresponding to rRNAs
    - **idx_mRNA** (``numpy.ndarray[int]``): indexes of TUs corresponding to mRNAs
    - **idx_tRNA** (``numpy.ndarray[int]``): indexes of TUs corresponding to tRNAs
    - **idx_rprotein** (``numpy.ndarray[int]``): indexes of TUs corresponding ribosomal
      proteins
    - **idx_rnap** (``numpy.ndarray[int]``): indexes of TU corresponding to RNAP
    - **rnaSynthProbFractions** (``dict``): Dictionary where keys are media types,
      values are sub-dictionaries with keys 'mRna', 'tRna', 'rRna', and
      values being probabilities of synthesis for each RNA type
    - **rnaSynthProbRProtein** (``dict``): Dictionary where keys are media types,
      values are arrays storing the (fixed) probability of synthesis for
      each rProtein TU, under that media condition.
    - **rnaSynthProbRnaPolymerase** (``dict``): Dictionary where keys are media
      types, values are arrays storing the (fixed) probability of
      synthesis for each RNAP TU, under that media condition.
    - **replication_coordinate** (``numpy.ndarray[int]``): Array with chromosome 
      coordinates for each TU
    - **transcription_direction** (``numpy.ndarray[bool]``): Array of transcription
      directions for each TU
    - **n_avogadro** (``unum.Unum``): Avogadro's number (constant)
    - **cell_density** (``unum.Unum``): Density of cell (constant)
    - **ppgpp** (``str``): id of ppGpp
    - **inactive_RNAP** (``str``): id of inactive RNAP
    - **synth_prob** (``Callable[[Unum, int], numpy.ndarrray[float]]``): 
      Function used in model of ppGpp regulation
      (see :py:func:`~reconstruction.ecoli.dataclasses.process.transcription.Transcription.synth_prob_from_ppgpp`).
      Takes ppGpp concentration (mol/volume) and copy number, returns 
      normalized synthesis probability for each gene
    - **copy_number** (``Callable[[Unum, int], numpy.ndarrray[float]]``): 
      see :py:func:`~reconstruction.ecoli.dataclasses.process.replication.Replication.get_average_copy_number`.
      Takes expected doubling time in minutes and chromosome coordinates of genes, 
      returns average copy number of each gene expected at doubling time
    - **ppgpp_regulation** (``bool``): Whether to include model of ppGpp regulation
    - **get_rnap_active_fraction_from_ppGpp** (``Callable[[Unum], float]``): 
      Returns elongation rate for a given ppGpp concentration
    - **seed** (``int``): random seed to initialize PRNG
    """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'fracActiveRnapDict': {},
        'rnaLengths': np.array([]),
        'rnaPolymeraseElongationRateDict': {},
        'variable_elongation': False,
        'make_elongation_rates': (lambda random, rate, timestep, variable:
            np.array([])),
        'active_rnap_foorprint_size': 1,
        'basal_prob': np.array([]),
        'delta_prob': {'deltaI': [], 'deltaJ': [], 'deltaV': [],
            'shape': tuple()},
        'get_delta_prob_matrix': None,
        'perturbations': {},
        'rna_data': {},
        'active_rnap_footprint_size': 24 * units.nt,
        'get_rnap_active_fraction_from_ppGpp': lambda x: 0.1,

        'idx_rRNA': np.array([]),
        'idx_mRNA': np.array([]),
        'idx_tRNA': np.array([]),
        'idx_rprotein': np.array([]),
        'idx_rnap': np.array([]),
        'rnaSynthProbFractions': {},
        'rnaSynthProbRProtein': {},
        'rnaSynthProbRnaPolymerase': {},
        'replication_coordinate': np.array([]),
        'transcription_direction': np.array([]),
        'n_avogadro': 6.02214076e+23 / units.mol,
        'cell_density': 1100 * units.g / units.L,
        'ppgpp': 'ppGpp',
        'inactive_RNAP': 'APORNAP-CPLX[c]',
        'synth_prob': lambda concentration, copy: 0.0,
        'copy_number': lambda x: x,
        'ppgpp_regulation': False,

        # attenuation
        'trna_attenuation': False,
        'attenuated_rna_indices': np.array([]),
        'attenuation_adjustments': np.array([]),

        # random seed
        'seed': 0,
        'emit_unique': False,
    }

    # Constructor
    def __init__(self, parameters=None):
        super().__init__(parameters)

        # Load parameters
        self.fracActiveRnapDict = self.parameters['fracActiveRnapDict']
        self.rnaLengths = self.parameters['rnaLengths']
        self.rnaPolymeraseElongationRateDict = self.parameters[
            'rnaPolymeraseElongationRateDict']
        self.variable_elongation = self.parameters['variable_elongation']
        self.make_elongation_rates = self.parameters['make_elongation_rates']
        self.active_rnap_footprint_size = self.parameters[
            'active_rnap_footprint_size']

        # Initialize matrices used to calculate synthesis probabilities
        self.basal_prob = self.parameters['basal_prob'].copy()
        self.trna_attenuation = self.parameters['trna_attenuation']
        if self.trna_attenuation:
            self.attenuated_rna_indices = self.parameters[
                'attenuated_rna_indices']
            self.attenuation_adjustments = self.parameters[
                'attenuation_adjustments']
            self.basal_prob[self.attenuated_rna_indices] += \
                self.attenuation_adjustments

        self.n_TUs = len(self.basal_prob)
        self.delta_prob = self.parameters['delta_prob']
        if self.parameters['get_delta_prob_matrix'] is not None:
            self.delta_prob_matrix = self.parameters[
                'get_delta_prob_matrix'](dense=True,
                    ppgpp=self.parameters['ppgpp_regulation'])
        else:
            # make delta_prob_matrix without adjustments
            self.delta_prob_matrix = scipy.sparse.csr_matrix(
                (self.delta_prob['deltaV'],
                 (self.delta_prob['deltaI'], self.delta_prob['deltaJ'])),
                shape=self.delta_prob['shape']
            ).toarray()

        # Determine changes from genetic perturbations
        self.genetic_perturbations = {}
        self.perturbations = self.parameters['perturbations']
        self.rna_data = self.parameters['rna_data']

        if len(self.perturbations) > 0:
            probability_indexes = [
                (index, self.perturbations[rna_data['id']])
                for index, rna_data in enumerate(self.rna_data)
                if rna_data['id'] in self.perturbations]

            self.genetic_perturbations = {
                'fixedRnaIdxs': [pair[0] for pair in probability_indexes],
                'fixedSynthProbs': [pair[1] for pair in probability_indexes]}

        # ID Groups
        self.idx_rRNA = self.parameters['idx_rRNA']
        self.idx_mRNA = self.parameters['idx_mRNA']
        self.idx_tRNA = self.parameters['idx_tRNA']
        self.idx_rprotein = self.parameters['idx_rprotein']
        self.idx_rnap = self.parameters['idx_rnap']

        # Synthesis probabilities for different categories of genes
        self.rnaSynthProbFractions = self.parameters['rnaSynthProbFractions']
        self.rnaSynthProbRProtein = self.parameters['rnaSynthProbRProtein']
        self.rnaSynthProbRnaPolymerase = self.parameters[
            'rnaSynthProbRnaPolymerase']

        # Coordinates and transcription directions of transcription units
        self.replication_coordinate = self.parameters['replication_coordinate']
        self.transcription_direction = self.parameters[
            'transcription_direction']

        self.inactive_RNAP = self.parameters['inactive_RNAP']

        # ppGpp control related
        self.n_avogadro = self.parameters['n_avogadro']
        self.cell_density = self.parameters['cell_density']
        self.ppgpp = self.parameters['ppgpp']
        self.synth_prob = self.parameters['synth_prob']
        self.copy_number = self.parameters['copy_number']
        self.ppgpp_regulation = self.parameters['ppgpp_regulation']
        self.get_rnap_active_fraction_from_ppGpp = self.parameters[
            'get_rnap_active_fraction_from_ppGpp']

        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed=self.seed)
        # Use separate random state instance to create unique indices
        # so results are directly comparable with wcEcoli
        self.unique_idx_random_state = np.random.RandomState(seed=self.seed)

        # Helper indices for Numpy indexing
        self.ppgpp_idx = None

    def ports_schema(self):
        return {
            'environment': {
                'media_id': {
                    '_default': '',
                    '_updater': 'set'}},

            'bulk': numpy_schema('bulk'),

            'full_chromosomes': numpy_schema('full_chromosomes',
                emit=self.parameters['emit_unique']),
            'promoters': numpy_schema('promoters',
                emit=self.parameters['emit_unique']),
            'RNAs': numpy_schema('RNAs',
                emit=self.parameters['emit_unique']),
            'active_RNAPs': numpy_schema('active_RNAPs',
                emit=self.parameters['emit_unique']),

            'listeners': {
                'mass': {
                    'cell_mass': {'_default': 0.0},
                    'dry_mass': {'_default': 0.0}},
                'rna_synth_prob': listener_schema({
                    'target_rna_synth_prob': [0.],
                    'actual_rna_synth_prob': [0.],
                    'tu_is_overcrowded': ([False] * self.n_TUs, self.rna_data['id']),
                    'total_rna_init': 0}),
                'ribosome_data': listener_schema({
                    'rRNA_initiated_TU': [0] * len(self.idx_rRNA),
                    'rRNA_init_prob_TU': [0.] * len(self.idx_rRNA),
                    'total_rna_init': 0}),
                'rnap_data': listener_schema({
                    'did_initialize': 0,
                    'rna_init_event': (0, self.rna_data['id'])})},
            'timestep': {'_default': self.parameters['time_step']}
        }

    def calculate_request(self, timestep, states):
        # At first update, convert all strings to indices
        if self.ppgpp_idx is None:
            bulk_ids = states['bulk']['id']
            self.ppgpp_idx = bulk_name_to_idx(self.ppgpp, bulk_ids)
            self.inactive_RNAP_idx = bulk_name_to_idx(
                self.inactive_RNAP, bulk_ids)

        # Get all inactive RNA polymerases
        requests = {
            'bulk': [(self.inactive_RNAP_idx, counts(
                states['bulk'], self.inactive_RNAP_idx))]
        }

        # Read current environment
        current_media_id = states['environment']['media_id']

        if states['full_chromosomes']['_entryState'].sum() > 0:
            # Get attributes of promoters
            TU_index, bound_TF = attrs(
                states['promoters'], ['TU_index', 'bound_TF'])

            if self.ppgpp_regulation:
                cell_mass = states['listeners']['mass']['cell_mass'] * units.fg
                cell_volume = cell_mass / self.cell_density
                counts_to_molar = 1 / (self.n_avogadro * cell_volume)
                ppgpp_conc = counts(states['bulk'],
                    self.ppgpp_idx) * counts_to_molar
                basal_prob, _ = self.synth_prob(ppgpp_conc, self.copy_number)
                if self.trna_attenuation:
                    basal_prob[self.attenuated_rna_indices] += \
                        self.attenuation_adjustments
                self.fracActiveRnap = self.get_rnap_active_fraction_from_ppGpp(
                    ppgpp_conc)
                ppgpp_scale = basal_prob[TU_index]
                # Use original delta prob if no ppGpp basal
                ppgpp_scale[ppgpp_scale == 0] = 1   
            else:
                basal_prob = self.basal_prob
                self.fracActiveRnap = self.fracActiveRnapDict[current_media_id]
                ppgpp_scale = 1

            # Calculate probabilities of the RNAP binding to each promoter
            self.promoter_init_probs = (basal_prob[TU_index] + ppgpp_scale *
                np.multiply(self.delta_prob_matrix[TU_index, :],
                    bound_TF).sum(axis=1))

            if len(self.genetic_perturbations) > 0:
                self._rescale_initiation_probs(
                    self.genetic_perturbations["fixedRnaIdxs"],
                    self.genetic_perturbations["fixedSynthProbs"],
                    TU_index)

            # Adjust probabilities to not be negative
            self.promoter_init_probs[self.promoter_init_probs < 0] = 0.0
            self.promoter_init_probs /= self.promoter_init_probs.sum()

            if not self.ppgpp_regulation:
                # Adjust synthesis probabilities depending on environment
                synthProbFractions = self.rnaSynthProbFractions[
                    current_media_id]

                # Create masks for different types of RNAs
                is_mrna = np.isin(TU_index, self.idx_mRNA)
                is_trna = np.isin(TU_index, self.idx_tRNA)
                is_rrna = np.isin(TU_index, self.idx_rRNA)
                is_rprotein = np.isin(TU_index, self.idx_rprotein)
                is_rnap = np.isin(TU_index, self.idx_rnap)
                is_fixed = is_trna | is_rrna | is_rprotein | is_rnap

                # Rescale initiation probabilities based on type of RNA
                self.promoter_init_probs[is_mrna] *= synthProbFractions[
                    "mRna"] / self.promoter_init_probs[is_mrna].sum()
                self.promoter_init_probs[is_trna] *= synthProbFractions[
                    "tRna"] / self.promoter_init_probs[is_trna].sum()
                self.promoter_init_probs[is_rrna] *= synthProbFractions[
                    "rRna"] / self.promoter_init_probs[is_rrna].sum()

                # Set fixed synthesis probabilities for RProteins and RNAPs
                self._rescale_initiation_probs(
                    np.concatenate((self.idx_rprotein, self.idx_rnap)),
                    np.concatenate((
                        self.rnaSynthProbRProtein[current_media_id],
                        self.rnaSynthProbRnaPolymerase[current_media_id]
                    )),
                    TU_index)

                assert self.promoter_init_probs[is_fixed].sum() < 1.0

                # Scale remaining synthesis probabilities accordingly
                scaleTheRestBy = (1. - self.promoter_init_probs[is_fixed].sum()
                    ) / self.promoter_init_probs[~is_fixed].sum()
                self.promoter_init_probs[~is_fixed] *= scaleTheRestBy

        # If there are no chromosomes in the cell, set all probs to zero
        else:
            self.promoter_init_probs = np.zeros(states['promoters'][
                '_entryState'].sum())

        self.rnaPolymeraseElongationRate = \
            self.rnaPolymeraseElongationRateDict[current_media_id]
        self.elongation_rates = self.make_elongation_rates(
            self.random_state,
            self.rnaPolymeraseElongationRate.asNumber(units.nt / units.s),
            1,  # want elongation rate, not lengths adjusted for time step
            self.variable_elongation)
        return requests

    def evolve_state(self, timestep, states):
        update = {
            'listeners': {
                'rna_synth_prob': {
                    'target_rna_synth_prob': np.zeros(self.n_TUs),
                    'actual_rna_synth_prob': np.zeros(self.n_TUs),
                    'tu_is_overcrowded': np.zeros(self.n_TUs),
                    'total_rna_init': 0
                },
                'ribosome_data': {
                    'total_rna_init': 0
                },
                'rnap_data': {
                    'did_initialize': 0,
                    'rna_init_event': np.zeros(self.n_TUs),
                }
            },
            'active_RNAPs': {},
            'full_chromosomes': {},
            'promoters': {},
            'RNAs': {},
        }

        # no synthesis if no chromosome
        if len(states['full_chromosomes']) == 0:
            return update

        # Get attributes of promoters
        TU_index, domain_index_promoters = attrs(
            states['promoters'], ['TU_index', 'domain_index'])

        n_promoters = states['promoters']['_entryState'].sum()
        # Construct matrix that maps promoters to transcription units
        TU_to_promoter = scipy.sparse.csr_matrix(
            (np.ones(n_promoters), (TU_index, np.arange(n_promoters))),
            shape=(self.n_TUs, n_promoters))

        # Compute target synthesis probabilities of each transcription unit
        target_TU_synth_probs = TU_to_promoter.dot(self.promoter_init_probs)
        update['listeners']['rna_synth_prob'][
            'target_rna_synth_prob'] = target_TU_synth_probs

        # Calculate RNA polymerases to activate based on probabilities
        # Note: ideally we should be using the actual TU synthesis probabilities
        # here, but the calculation of actual probabilities requires the number
        # of RNAPs to activate. The difference should be small.
        self.activationProb = self._calculateActivationProb(
            states['timestep'],
            self.fracActiveRnap,
            self.rnaLengths,
            (units.nt / units.s) * self.elongation_rates,
            target_TU_synth_probs)

        n_RNAPs_to_activate = np.int64(self.activationProb *
            counts(states['bulk'], self.inactive_RNAP_idx))

        if n_RNAPs_to_activate == 0:
            return update
        
        # Cap the initiation probabilities at the maximum level physically
        # allowed from the known RNAP footprint sizes
        max_p = (self.rnaPolymeraseElongationRate / self.active_rnap_footprint_size
            * (units.s) * states['timestep'] / n_RNAPs_to_activate).asNumber()
        is_overcrowded = (self.promoter_init_probs > max_p)

        while np.any(self.promoter_init_probs > max_p):
            self.promoter_init_probs[is_overcrowded] = max_p
            scale_the_rest_by = (
                (1. - self.promoter_init_probs[is_overcrowded].sum())
                / self.promoter_init_probs[~is_overcrowded].sum()
                )
            self.promoter_init_probs[~is_overcrowded] *= scale_the_rest_by
            is_overcrowded |= (self.promoter_init_probs > max_p)

        # Compute actual synthesis probabilities of each transcription unit
        actual_TU_synth_probs = TU_to_promoter.dot(self.promoter_init_probs)
        tu_is_overcrowded = TU_to_promoter.dot(is_overcrowded).astype(bool)
        update['listeners']['rna_synth_prob'][
            'actual_rna_synth_prob'] = actual_TU_synth_probs
        update['listeners']['rna_synth_prob'][
            'tu_is_overcrowded'] = tu_is_overcrowded

        # Sample a multinomial distribution of initiation probabilities to
        # determine what promoters are initialized
        n_initiations = self.random_state.multinomial(
            n_RNAPs_to_activate, self.promoter_init_probs)

        # Build array of transcription unit indexes for partially transcribed
        # RNAs and domain indexes for RNAPs
        TU_index_partial_RNAs = np.repeat(TU_index, n_initiations)
        domain_index_rnap = np.repeat(domain_index_promoters, n_initiations)

        # Build arrays of starting coordinates and transcription directions
        coordinates = self.replication_coordinate[TU_index_partial_RNAs]
        is_forward = self.transcription_direction[TU_index_partial_RNAs]

        # new RNAPs
        RNAP_indexes = create_unique_indexes(
            n_RNAPs_to_activate, self.unique_idx_random_state)
        update['active_RNAPs'].update({
            'add': {
                'unique_index': RNAP_indexes,
                'domain_index': domain_index_rnap,
                'coordinates': coordinates,
                'is_forward': is_forward,
            }
        })

        # Decrement counts of inactive RNAPs
        update['bulk'] = [(self.inactive_RNAP_idx, -n_initiations.sum())]

        # Add partially transcribed RNAs
        is_mRNA = np.isin(TU_index_partial_RNAs, self.idx_mRNA)
        rna_indices = create_unique_indexes(
            n_RNAPs_to_activate, self.unique_idx_random_state)
        update['RNAs'].update({
            'add': {
                'unique_index': rna_indices,
                'TU_index': TU_index_partial_RNAs,
                'transcript_length': np.zeros(cast(int, n_RNAPs_to_activate)),
                'is_mRNA': is_mRNA,
                'is_full_transcript': np.zeros(cast(int, n_RNAPs_to_activate),
                    dtype=bool),
                'can_translate': is_mRNA,
                'RNAP_index': RNAP_indexes,
            }
        })

        rna_init_event = TU_to_promoter.dot(n_initiations)
        rRNA_initiations = rna_init_event[self.idx_rRNA]

        # Write outputs to listeners
        update['listeners']['ribosome_data'] = {
            'rRNA_initiated_TU': rRNA_initiations.astype(int),
            'rRNA_init_prob_TU': rRNA_initiations / float(n_RNAPs_to_activate),
            'total_rna_init': n_RNAPs_to_activate}

        update['listeners']['rnap_data'] = {
            'did_initialize': n_RNAPs_to_activate,
            'rna_init_event': rna_init_event}
        
        update['listeners']['rna_synth_prob']['total_rna_init'
            ] = n_RNAPs_to_activate

        return update

    def _calculateActivationProb(self, timestep, fracActiveRnap, rnaLengths, rnaPolymeraseElongationRates, synthProb):
        """
        Calculate expected RNAP termination rate based on RNAP elongation rate
        - allTranscriptionTimes: Vector of times required to transcribe each
        transcript
        - allTranscriptionTimestepCounts: Vector of numbers of timesteps
        required to transcribe each transcript
        - averageTranscriptionTimeStepCounts: Average number of timesteps
        required to transcribe a transcript, weighted by synthesis
        probabilities of each transcript
        - expectedTerminationRate: Average number of terminations in one
        timestep for one transcript
        """
        allTranscriptionTimes = 1. / rnaPolymeraseElongationRates * rnaLengths
        timesteps = (1. / (timestep * units.s) * allTranscriptionTimes).asNumber()
        allTranscriptionTimestepCounts = np.ceil(timesteps)
        averageTranscriptionTimestepCounts = np.dot(
            synthProb, allTranscriptionTimestepCounts)
        expectedTerminationRate = 1. / averageTranscriptionTimestepCounts

        """
        Modify given fraction of active RNAPs to take into account early
        terminations in between timesteps
        - allFractionTimeInactive: Vector of probabilities an "active" RNAP
        will in effect be "inactive" because it has terminated during a
        timestep
        - averageFractionTimeInactive: Average probability of an "active" RNAP
        being in effect "inactive", weighted by synthesis probabilities
        - effectiveFracActiveRnap: New higher "goal" for fraction of active
        RNAP, considering that the "effective" fraction is lower than what the
        listener sees
        """
        allFractionTimeInactive = 1 - (
                1. / (timestep * units.s) * allTranscriptionTimes).asNumber() / allTranscriptionTimestepCounts
        averageFractionTimeInactive = np.dot(allFractionTimeInactive, synthProb)
        effectiveFracActiveRnap = fracActiveRnap / (1 - averageFractionTimeInactive)

        # Return activation probability that will balance out the expected termination rate
        activation_prob = effectiveFracActiveRnap * expectedTerminationRate / (
            1 - effectiveFracActiveRnap)

        if activation_prob > 1:
            activation_prob = 1

        return activation_prob

    def _rescale_initiation_probs(
            self, fixed_indexes, fixed_synth_probs, TU_index):
        """
        Rescales the initiation probabilities of each promoter such that the
        total synthesis probabilities of certain types of RNAs are fixed to
        a predetermined value. For instance, if there are two copies of
        promoters for RNA A, whose synthesis probability should be fixed to
        0.1, each promoter is given an initiation probability of 0.05.
        """
        for idx, synth_prob in zip(fixed_indexes, fixed_synth_probs):
            fixed_mask = (TU_index == idx)
            self.promoter_init_probs[fixed_mask] = synth_prob / fixed_mask.sum()


def test_transcript_initiation(return_data=False):
    def make_elongation_rates(random, base, time_step, variable_elongation=False):
        size = 9  # number of TUs
        lengths = time_step * np.full(size, base, dtype=np.int64)
        lengths = stochasticRound(random, lengths) if random else np.round(lengths)

        return lengths.astype(np.int64)

    rna_data = UnitStructArray(
        # id, deg_rate, len, counts, _ACGU mw, mRNA?, miscRNA?, rRNA?, tRNA?, 23S?, 16S?, 5S?, rProt?, RNAP?, geneid,
        # Km, coord, direction
        np.array([('16SrRNA', .002, 45, [10, 11, 12, 12], 13500, False, False, True, False, False, True, False, False,
                   False, '16SrRNA', 2e-4, 0, True),
                  ('23SrRNA', .002, 450, [100, 110, 120, 120], 135000, False, False, True, False, True, False, False,
                   False, False, '23SrRNA', 2e-4, 1000, True),
                  ('5SrRNA', .002, 600, [150, 150, 150, 150], 180000, False, False, True, False, False, False, True,
                   False, False, '5SrRNA', 2e-4, 2000, True),
                  ('rProtein', .002, 700, [175, 175, 175, 175], 210000, True, False, False, False, False, False, False,
                   True, False, 'rProtein', 2e-4, 3000, False),
                  ('RNAP', .002, 800, [200, 200, 200, 200], 240000, True, False, False, False, False, False, False,
                   False, True, 'RNAP', 2e-4, 4000, False),
                  ('miscProt', .002, 900, [225, 225, 225, 225], 270000, True, False, False, False, False, False, False,
                   False, False, 'miscProt', 2e-4, 5000, True),
                  ('tRNA1', .002, 1200, [300, 300, 300, 300], 360000, False, False, False, True, False, False, False,
                   False, False, 'tRNA1', 2e-4, 6000, False),
                  ('tRNA2', .002, 4000, [1000, 1000, 1000, 1000], 1200000, False, False, False, True, False, False,
                   False, False, False, 'tRNA2', 2e-4, 7000, False),
                  ('tRNA3', .002, 7000, [1750, 1750, 1750, 1750], 2100000, False, False, False, True, False, False,
                   False, False, False, 'tRNA3', 2e-4, 8000, True)
                  ],
                 dtype=[('id', '<U15'), ('deg_rate', '<f8'), ('length', '<i8'),
                        ('counts_ACGU', '<i8', (4,)), ('mw', '<f8'), ('is_mRNA', '?'),
                        ('is_miscRNA', '?'), ('is_rRNA', '?'), ('is_tRNA', '?'),
                        ('is_23S_rRNA', '?'), ('is_16S_rRNA', '?'), ('is_5S_rRNA', '?'),
                        ('is_ribosomal_protein', '?'), ('is_RNAP', '?'), ('gene_id', '<U8'),
                        ('Km_endoRNase', '<f8'), ('replication_coordinate', '<i8'), ('direction', '?')]),
        {'id': None, 'deg_rate': 1.0 / units.s, 'length': units.nt, 'counts_ACGU': units.nt,
         'mw': units.g / units.mol, 'is_mRNA': None, 'is_miscRNA': None, 'is_rRNA': None, 'is_tRNA': None,
         'is_23S_rRNA': None, 'is_16S_rRNA': None, 'is_5S_rRNA': None, 'is_ribosomal_protein': None,
         'is_RNAP': None, 'gene_id': None, 'Km_endoRNase': units.mol / units.L, 'replication_coordinate': None,
         'direction': None}
    )

    test_config = {
        'fracActiveRnapDict': {'minimal': 0.2},
        'rnaLengths': np.array([x[2] for x in rna_data.fullArray()]),
        'rnaPolymeraseElongationRateDict': {'minimal': 50 * units.nt / units.s},
        'make_elongation_rates': make_elongation_rates,
        'basal_prob': np.array([1e-7, 1e-7, 1e-7, 1e-7, 1e-6, 1e-6, 1e-6, 1e-5, 1e-5]),
        'delta_prob': {
            'deltaV': [-1e-3, -1e-5, -1e-6, 1e-7, 1e-6, 1e-6, 1e-5],
            'deltaI': [0, 1, 2, 3, 4, 5, 6],
            'deltaJ': [0, 1, 2, 3, 0, 1, 2],
            'shape': (9, 4)
        },
        'rna_data': rna_data,

        'idx_16SrRNA': np.array([0]),
        'idx_23SrRNA': np.array([1]),
        'idx_5SrRNA': np.array([2]),
        'idx_rRNA': np.array([0, 1, 2]),
        'idx_mRNA': np.array([3, 4, 5]),
        'idx_tRNA': np.array([6, 7, 8]),
        'idx_rprotein': np.array([3]),
        'idx_rnap': np.array([4]),
        'rnaSynthProbFractions': {'minimal': {'mRna': 0.25, 'tRna': 0.6, 'rRna': 0.15}},
        'rnaSynthProbRProtein': {'minimal': np.array([.06])},
        'rnaSynthProbRnaPolymerase': {'minimal': np.array([.04])},
        'replication_coordinate': np.array([x[-2] for x in rna_data.fullArray()]),
        'transcription_direction': np.array([x[-1] for x in rna_data.fullArray()]),
        'inactive_RNAP': 'APORNAP-CPLX[c]',

        'seed': 0,
        'time_step': 2,
        #'_schema' : {'molecules' : {'APORNAP-CPLX[c]' : {'_updater' : 'null'}}}
    }

    transcript_initiation = TranscriptInitiation(test_config)

    submass_dtypes = [('massDiff_DNA', '<f8'), ('massDiff_mRNA', '<f8'),
        ('massDiff_metabolite', '<f8'), ('massDiff_miscRNA', '<f8'),
        ('massDiff_nonspecific_RNA', '<f8'), ('massDiff_protein', '<f8'),
        ('massDiff_rRNA', '<f8'), ('massDiff_tRNA', '<f8'),
        ('massDiff_water', '<f8')]
    chromosome_dtypes  = [('_entryState', 'i1'), ('_globalIndex', '<i8'),
        ('division_time', '<f8'), ('domain_index', '<i4'),
        ('has_triggered_division', '?'), ('unique_index', '<i8')]
    rna_dtypes = [('RNAP_index', '<i8'), ('TU_index', '<i8'),
        ('_entryState', 'i1'), ('_globalIndex', '<i8'), ('can_translate', '?'),
        ('is_full_transcript', '?'), ('is_mRNA', '?'), ('unique_index', '<i8'),
        ('transcript_length', '<i8')]
    active_rnap_dtypes = [('_entryState', 'i1'), ('_globalIndex', '<i8'),
        ('coordinates', '<i8'), ('is_forward', '?'), ('domain_index', '<i4'),
        ('unique_index', '<i8')]
    promoter_dtypes = [('TU_index', '<i8'), ('_entryState', 'i1'),
        ('_globalIndex', '<i8'), ('bound_TF', '?', (4,)),
        ('coordinates', '<i8'), ('domain_index', '<i4'),
        ('unique_index', '<i8')]
    initial_state = {
        'environment': {'media_id': 'minimal'},
        'bulk': np.array([
            ('APORNAP-CPLX[c]', 1000),
            ('GUANOSINE-5DP-3DP[c]', 0),
            ('ppGpp', 0)
        ], dtype=[('id', 'U40'), ('count', int)]),
        'listeners': {
            'mass': {
                'cell_mass': 1000,
                'dry_mass': 350}
        }
    }
    unique_state = {
        'full_chromosome': np.array([
            (1, 0, 0, 0, False, 0) + (0,)*9,
        ], dtype=chromosome_dtypes+submass_dtypes),
        'promoter': np.array([
            (i, 1, i, [False]*4, subdata['replication_coordinate'], 0, i
                )  + (0,)*9
            for i, subdata in enumerate(rna_data)
        ], dtype=promoter_dtypes+submass_dtypes),
        'RNA': np.array([], dtype=rna_dtypes+submass_dtypes),
        'active_RNAP': np.array([], dtype=active_rnap_dtypes+submass_dtypes),
    }
    initial_state['unique'] = unique_state

    settings = {
        'total_time': 100,
        'initial_state': initial_state,
        'topology': TOPOLOGY}

    data_noTF = simulate_process(transcript_initiation, settings)

    # Also gather data where TFs are bound:

    # Assertions =========================================================
    # TODO(Michael):
    #  1) When no initiations occurred in a timestep, the inits_by_TU is a scalar 0
    #  2) Weird things happen when RNAP is limiting, including affecting RNA synth probs
    #     - for toy model, initial RNAPs <= 132 results in no initiation
    #     - 1000 initial RNAPs, run for t=1000s results in initiations without depletion of inactive RNAP
    #  3) rnaps in data['active_RNAPs'] seem to be missing direction, domain index data (likewise with rnas)
    #  4) Effect of TF binding is not tested in the toy model
    #     - simple test is to compare gene under up/down-regulation with data from same gene without regulation
    #  5) Test of fixed synthesis probabilties does not pass

    # Unpack data
    bulk_timeseries = np.array(data_noTF['bulk'])
    inactive_RNAP = bulk_timeseries[:, 0]
    d_inactive_RNAP = inactive_RNAP[1:] - inactive_RNAP[:-1]
    d_active_RNAP = np.array(data_noTF['listeners']['rnap_data']['did_initialize'][1:])

    inits_by_TU = np.stack(data_noTF['listeners']['rnap_data']['rna_init_event'][1:])

    rnap_inits = inits_by_TU[:, test_config['idx_rnap']]
    rprotein_inits = inits_by_TU[:, test_config['idx_rprotein']]

    # Sanity checks
    assert monotonically_decreasing(inactive_RNAP), "Inactive RNAPs are not monotonically decreasing"
    assert all_nonnegative(inactive_RNAP), "Inactive RNAPs fall below zero"
    assert all_nonnegative(inits_by_TU), "Negative initiations (!?)"
    assert monotonically_decreasing(d_active_RNAP), "Change in active RNAPs is not monotonically decreasing"
    assert all_nonnegative(d_active_RNAP), "One or more timesteps has decrease in active RNAPs"
    assert np.sum(d_active_RNAP) == np.sum(inits_by_TU), "# of active RNAPs does not match number of initiations"

    # Inactive RNAPs deplete as they are activated
    np.testing.assert_array_equal(-d_inactive_RNAP,
                                  d_active_RNAP,
                                  "Depletion of inactive RNAPs does not match counts of RNAPs activated.")

    # Fixed synthesis probability TUs (RNAP, rProtein) and non-fixed TUs synthesized in correct proportion
    expected = np.array([test_config['rnaSynthProbRProtein']['minimal'][0],
                         test_config['rnaSynthProbRnaPolymerase']['minimal'][0],
                         1])
    expected[2] -= expected[0] + expected[1]
    expected *= np.sum(inits_by_TU)

    actual = np.array([np.sum(rprotein_inits),
                       np.sum(rnap_inits),
                       np.sum(inits_by_TU) - np.sum(rprotein_inits) - np.sum(rnap_inits)])

    fixed_prob_test = chisquare(actual, f_exp=expected)

    assert fixed_prob_test.pvalue > 0.05, ("Distribution of RNA types synthesized does "
                                          "not (approximately) match set points for fixed synthesis"
                                          f"(p = {fixed_prob_test.pvalue} <= 0.05)")

    # mRNA, tRNA, rRNA synthesized in correct proportion
    RNA_dist = np.array([np.sum(inits_by_TU[:, test_config['idx_mRNA']]),
                         np.sum(inits_by_TU[:, test_config['idx_tRNA']]),
                         np.sum(inits_by_TU[:, test_config['idx_rRNA']])])
    RNA_dist /= sum(RNA_dist)

    RNA_synth_prob_test = chisquare(RNA_dist,
                                    [v for v in test_config['rnaSynthProbFractions']['minimal'].values()])

    assert (RNA_synth_prob_test.pvalue > 0.05), ("Distribution of RNA types synthesized does"
                                                 "not (approximately) match values set by media")

    if return_data:
        return test_config, data_noTF


def run_plot(config, data):
    N = len(data['time'])
    timestep = config['time_step']
    inits_by_TU = np.stack(data['listeners']['rnap_data']['rna_init_event'][1:])
    synth_probs = np.array(data['listeners']['rna_synth_prob']['rna_synth_prob'][1:])

    # plot synthesis probabilities over time
    plt.subplot(2, 2, 1)
    prev = np.zeros(N - 1)
    for TU in range(synth_probs.shape[1]):
        plt.bar(data['time'][1:], synth_probs[:, TU], bottom=prev, width=timestep)
        prev += synth_probs[:, TU]
    plt.xlabel('Time (s)')
    plt.ylabel('Probability of Synthesis')
    plt.title('Theoretical Synthesis Probabilities over Time')

    # plot actual probability of synthesis for each RNA
    plt.subplot(2, 2, 2)
    probs = np.sum(inits_by_TU, axis=0) / np.sum(inits_by_TU)
    prev = 0
    for i in range(len(probs)):
        prob = probs[i]
        plt.bar([0], [prob], bottom=prev, width=1)
        plt.text(i / len(probs) - 0.5, prev + prob/2, config['rna_data'][i][0])
        prev += prob
    plt.tick_params(axis='x', which='both', bottom=False, top=False, labelbottom=False)
    plt.ylabel("Probability")
    plt.title("Actual Probability of Synthesis by TU")

    # plot which RNAs are transcribed
    plt.subplot(2, 2, 3)

    prev = np.zeros(N - 1)
    for TU in range(inits_by_TU.shape[1]):
        plt.bar(data['time'][1:], inits_by_TU[:, TU], bottom=prev, width=timestep)
        prev += inits_by_TU[:, TU]
    plt.xlabel("Time (s)")
    plt.ylabel("Transcripts")
    plt.title("Transcripts over time, for all TUs")

    # plot which RNAs are transcribed, grouped by mRNA/tRNA/rRNA
    plt.subplot(2, 2, 4)

    grouped_inits = np.concatenate([[np.sum(inits_by_TU[:, config['idx_tRNA']], axis=1)],
                                    [np.sum(inits_by_TU[:, config['idx_mRNA']], axis=1)],
                                    [np.sum(inits_by_TU[:, config['idx_rRNA']], axis=1)]]).T

    prev = np.zeros(N - 1)
    for TU in range(grouped_inits.shape[1]):
        plt.bar(data['time'][1:], grouped_inits[:, TU], bottom=prev, width=timestep)
        prev += grouped_inits[:, TU]
    plt.xlabel("Time (s)")
    plt.ylabel("Transcripts")
    plt.title("Transcripts over time by RNA type")
    plt.legend(["tRNA", "mRNA", "rRNA"], bbox_to_anchor=(1.05, 1), loc='upper left', borderaxespad=0.)

    plt.subplots_adjust(hspace=0.5, wspace=0.25)
    plt.gcf().set_size_inches(10, 6)
    plt.savefig("out/migration/transcript_initiation_toy_model.png")


def main():
    config, data = test_transcript_initiation(return_data=True)
    run_plot(config, data)


if __name__ == "__main__":
    main()
