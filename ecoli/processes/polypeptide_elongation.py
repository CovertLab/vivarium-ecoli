"""
======================
Polypeptide Elongation
======================

This process models the polymerization of amino acids into polypeptides
by ribosomes using an mRNA transcript as a template. Elongation terminates
once a ribosome has reached the end of an mRNA transcript. Polymerization
occurs across all ribosomes simultaneously and resources are allocated to
maximize the progress of all ribosomes within the limits of the maximum ribosome
elongation rate, available amino acids and GTP, and the length of the transcript.
"""

from typing import Any, Callable, Optional, Tuple

from numba import njit
import numpy as np
from scipy.integrate import solve_ivp
from unum import Unum

# wcEcoli imports
from wholecell.utils.polymerize import (buildSequences, polymerize,
    computeMassIncrease)
from wholecell.utils.random import stochasticRound
from wholecell.utils import units

# vivarium imports
from vivarium.core.composition import simulate_process
from vivarium.library.dict_utils import deep_merge
from vivarium.library.units import units as vivunits
from vivarium.plots.simulation_output import plot_variables

# vivarium-ecoli imports
from ecoli.library.schema import (listener_schema, numpy_schema, counts, attrs,
    bulk_name_to_idx)
from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess


MICROMOLAR_UNITS = units.umol / units.L
"""Units used for all concentrations."""
REMOVED_FROM_CHARGING = {'L-SELENOCYSTEINE[c]'}
"""Amino acids to remove from charging when running with 
``steady_state_trna_charging``"""


# Register default topology for this process, associating it with process name
NAME = 'ecoli-polypeptide-elongation'
TOPOLOGY = {
    "environment": ("environment",),
    "boundary": ("boundary",),
    "listeners": ("listeners",),
    "active_ribosome": ("unique", "active_ribosome"),
    "bulk": ("bulk",),
    "polypeptide_elongation": ("process_state", "polypeptide_elongation"),
    # Non-partitioned counts
    "bulk_total": ("bulk",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)

DEFAULT_AA_NAMES = [
    'L-ALPHA-ALANINE[c]', 'ARG[c]', 'ASN[c]', 'L-ASPARTATE[c]', 'CYS[c]',
    'GLT[c]', 'GLN[c]', 'GLY[c]', 'HIS[c]', 'ILE[c]', 'LEU[c]', 'LYS[c]',
    'MET[c]', 'PHE[c]', 'PRO[c]', 'SER[c]', 'THR[c]', 'TRP[c]', 'TYR[c]',
    'L-SELENOCYSTEINE[c]', 'VAL[c]']


class PolypeptideElongation(PartitionedProcess):
    """ Polypeptide Elongation PartitionedProcess

    defaults:
        proteinIds: array length n of protein names
    """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'time_step': 1,
        'max_time_step': 2.0,
        'n_avogadro': 6.02214076e+23 / units.mol,
        'proteinIds': np.array([]),
        'proteinLengths': np.array([]),
        'proteinSequences': np.array([[]]),
        'aaWeightsIncorporated': np.array([]),
        'endWeight': np.array([2.99146113e-08]),
        'variable_elongation': False,
        'make_elongation_rates': (lambda random, rate, timestep, variable:
            np.array([])),
        'next_aa_pad': 1,
        'ribosomeElongationRate': 17.388824902723737,
        'translation_aa_supply': {'minimal': np.array([])},
        'import_threshold': 1e-05,
        'aa_from_trna': np.zeros(21),
        'gtpPerElongation': 4.2,
        'aa_supply_in_charging': False,
        'adjust_timestep_for_charging': False,
        'mechanistic_translation_supply': False,
        'mechanistic_aa_transport': False,
        'ppgpp_regulation': False,
        'disable_ppgpp_elongation_inhibition': False,
        'variable_elongation': False,
        'trna_charging': False,
        'translation_supply': False,
        'mechanistic_supply': False,
        'ribosome30S': 'ribosome30S',
        'ribosome50S': 'ribosome50S',
        'amino_acids': DEFAULT_AA_NAMES,
        'aa_exchange_names': DEFAULT_AA_NAMES,
        'basal_elongation_rate': 22.0,
        'ribosomeElongationRateDict': {
            'minimal': 17.388824902723737 * units.aa / units.s},
        'uncharged_trna_names': np.array([]),
        'aaNames': DEFAULT_AA_NAMES,
        'aa_enzymes': [],
        'proton': 'PROTON',
        'water': 'H2O',
        'cellDensity': 1100 * units.g / units.L,
        'elongation_max': 22 * units.aa / units.s,
        'aa_from_synthetase': np.array([[]]),
        'charging_stoich_matrix': np.array([[]]),
        'charged_trna_names': [],
        'charging_molecule_names': [],
        'synthetase_names': [],
        'ppgpp_reaction_names': [],
        'ppgpp_reaction_metabolites': [],
        'ppgpp_reaction_stoich': np.array([[]]),
        'ppgpp_synthesis_reaction': 'GDPPYPHOSKIN-RXN',
        'ppgpp_degradation_reaction': 'PPGPPSYN-RXN',
        'aa_importers': [],
        'amino_acid_export': None,
        'synthesis_index': 0,
        'aa_exporters': [],
        'get_pathway_enzyme_counts_per_aa': None,
        'import_constraint_threshold': 0,
        'unit_conversion': 0,
        'elong_rate_by_ppgpp': 0,
        'amino_acid_import': None,
        'degradation_index': 1,
        'amino_acid_synthesis': None, 
        'rela': 'RELA',
        'spot': 'SPOT',
        'ppgpp': 'ppGpp',
        'kS': 100.0,
        'KMtf': 1.0,
        'KMaa': 100.0,
        'krta': 1.0,
        'krtf': 500.0,
        'KD_RelA': 0.26,
        'k_RelA': 75.0,
        'k_SpoT_syn': 2.6,
        'k_SpoT_deg': 0.23,
        'KI_SpoT': 20.0,
        'aa_supply_scaling': lambda aa_conc, aa_in_media: 0,
        'seed': 0,
        'emit_unique': False,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        self.max_time_step = self.parameters['max_time_step']

        # Simulation options
        self.aa_supply_in_charging = self.parameters['aa_supply_in_charging']
        self.adjust_timestep_for_charging = self.parameters[
            'adjust_timestep_for_charging']
        self.mechanistic_translation_supply = self.parameters[
            'mechanistic_translation_supply']
        self.mechanistic_aa_transport = self.parameters[
            'mechanistic_aa_transport']
        self.ppgpp_regulation = self.parameters['ppgpp_regulation']
        self.disable_ppgpp_elongation_inhibition = self.parameters[
            'disable_ppgpp_elongation_inhibition']
        self.variable_elongation = self.parameters['variable_elongation']
        self.variable_polymerize = self.ppgpp_regulation or self.variable_elongation
        translation_supply = self.parameters['translation_supply']
        trna_charging = self.parameters['trna_charging']

        # Load parameters
        self.n_avogadro = self.parameters['n_avogadro']
        self.proteinIds = self.parameters['proteinIds']
        self.protein_lengths = self.parameters['proteinLengths']
        self.proteinSequences = self.parameters['proteinSequences']
        self.aaWeightsIncorporated = self.parameters['aaWeightsIncorporated']
        self.endWeight = self.parameters['endWeight']
        self.make_elongation_rates = self.parameters['make_elongation_rates']
        self.next_aa_pad = self.parameters['next_aa_pad']

        self.ribosome30S = self.parameters['ribosome30S']
        self.ribosome50S = self.parameters['ribosome50S']
        self.amino_acids = self.parameters['amino_acids']
        self.aa_exchange_names = self.parameters['aa_exchange_names']
        self.aa_environment_names = [aa[:-3] for aa in self.aa_exchange_names]
        self.aa_enzymes = self.parameters['aa_enzymes']

        self.ribosomeElongationRate = self.parameters['ribosomeElongationRate']

        # Amino acid supply calculations
        self.translation_aa_supply = self.parameters['translation_aa_supply']
        self.import_threshold = self.parameters['import_threshold']

        # Used for figure in publication
        self.trpAIndex = np.where(self.proteinIds ==
            "TRYPSYN-APROTEIN[c]")[0][0]

        self.elngRateFactor = 1.

        # Data structures for charging
        self.aa_from_trna = self.parameters['aa_from_trna']

        # Set modeling method
        # TODO: Test that these models all work properly
        if trna_charging:
            self.elongation_model = SteadyStateElongationModel(
                self.parameters, self)
        elif translation_supply:
            self.elongation_model = TranslationSupplyElongationModel(
                self.parameters, self)
        else:
            self.elongation_model = BaseElongationModel(self.parameters, self)

        # Growth associated maintenance energy requirements for elongations
        self.gtpPerElongation = self.parameters['gtpPerElongation']
        # Need to account for ATP hydrolysis for charging that has been
        # removed from measured GAM (ATP -> AMP is 2 hydrolysis reactions)
        # if charging reactions are not explicitly modeled
        if not trna_charging:
            self.gtpPerElongation += 2

        # basic molecule names
        self.proton = self.parameters['proton']
        self.water = self.parameters['water']
        self.rela = self.parameters['rela']
        self.spot = self.parameters['spot']
        self.ppgpp = self.parameters['ppgpp']
        self.aa_importers = self.parameters['aa_importers']
        self.aa_exporters = self.parameters['aa_exporters']
        # Numpy index for bulk molecule
        self.proton_idx = None

        # Names of molecules associated with tRNA charging
        self.ppgpp_reaction_metabolites = self.parameters[
            'ppgpp_reaction_metabolites']
        self.uncharged_trna_names = self.parameters['uncharged_trna_names']
        self.charged_trna_names = self.parameters['charged_trna_names']
        self.charging_molecule_names = self.parameters[
            'charging_molecule_names']
        self.synthetase_names = self.parameters['synthetase_names']

        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed = self.seed)

        self.zero_aa_exchange_rates = MICROMOLAR_UNITS / units.s * np.zeros(
            len(self.amino_acids))

    def ports_schema(self):
        return {
            'environment': {
                'media_id': {
                    '_default': '',
                    '_updater': 'set'},
                'exchange': {'*': {'_default': 0}}
            },
            'boundary': {
                'external': {
                    aa: {'_default': 0}
                    for aa in sorted(self.aa_environment_names)
                }
            },
            'listeners': {
                'mass': listener_schema({
                    'cell_mass': 0.0,
                    'dry_mass': 0.0}),

                'growth_limits': listener_schema({
                    'fraction_trna_charged': ([0.] * len(self.uncharged_trna_names),
                        self.uncharged_trna_names),
                    'aa_allocated': ([0] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_pool_size': ([0] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_request_size': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'active_ribosome_allocated': 0,
                    'net_charged': ([0] * len(self.uncharged_trna_names),
                        self.uncharged_trna_names),
                    'aas_used': ([0] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_count_diff': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    # Below only if trna_charging enbaled
                    'original_aa_supply': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_in_media': ([False] * len(self.amino_acids),
                        self.amino_acids),
                    'synthetase_conc': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'uncharged_trna_conc': (
                        [0.] * len(self.amino_acids), self.amino_acids),
                    'charged_trna_conc': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_conc': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'ribosome_conc': 0.,
                    'fraction_aa_to_elongate': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_supply': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_synthesis': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_import': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_export': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_importers': ([0] * len(self.aa_importers),
                        self.aa_importers),
                    'aa_exporters': ([0] * len(self.aa_exporters),
                        self.aa_exporters),
                    'aa_supply_enzymes_fwd': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_supply_enzymes_rev': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_supply_aa_conc': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_supply_fraction_fwd': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_supply_fraction_rev': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'ppgpp_conc': 0.,
                    'rela_conc': 0.,
                    'spot_conc': 0.,
                    'rela_syn': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'spot_syn': 0.,
                    'spot_deg': 0.,
                    'spot_deg_inhibited': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'trna_charged': ([0] * len(self.amino_acids),
                        self.amino_acids)}),

                'ribosome_data': listener_schema({
                    'translation_supply': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'effective_elongation_rate': 0.,
                    'aa_count_in_sequence': ([0] * len(self.amino_acids),
                        self.amino_acids),
                    'aa_counts': ([0.] * len(self.amino_acids),
                        self.amino_acids),
                    'actual_elongations': 0,
                    'actual_elongation_hist': [0] * 22,
                    'elongations_non_terminating_hist': [0] * 22,
                    'did_terminate': 0,
                    'termination_loss': 0,
                    'num_trpA_terminated': 0,
                    'process_elongation_rate': 0.})},

            'bulk': numpy_schema('bulk'),
            'bulk_total': numpy_schema('bulk'),

            'active_ribosome': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),

            'polypeptide_elongation': {
                'aa_count_diff': {
                    '_default': {},
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'empty_dict'},
                'gtp_to_hydrolyze': {
                    '_default': 0.,
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'zero',
                },
                'aa_exchange_rates': {
                    '_default': [0.],
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'zero'
                },
            },
            'timestep': {'_default': self.parameters['time_step']}
        }

    def calculate_request(self, timestep, states):
        """
        Set ribosome elongation rate based on simulation medium environment and elongation rate factor
        which is used to create single-cell variability in growth rate
        The maximum number of amino acids that can be elongated in a single timestep is set to 22
        intentionally as the minimum number of padding values on the protein sequence matrix is set to 22.
        If timesteps longer than 1.0s are used, this feature will lead to errors in the effective ribosome
        elongation rate.
        """

        if self.proton_idx is None:
            bulk_ids = states['bulk']['id']
            self.proton_idx = bulk_name_to_idx(self.proton, bulk_ids)
            self.water_idx = bulk_name_to_idx(self.water, bulk_ids)
            self.rela_idx = bulk_name_to_idx(self.rela, bulk_ids)
            self.spot_idx = bulk_name_to_idx(self.spot, bulk_ids)
            self.ppgpp_idx = bulk_name_to_idx(self.ppgpp, bulk_ids)
            self.monomer_idx = bulk_name_to_idx(self.proteinIds, bulk_ids)
            self.amino_acid_idx = bulk_name_to_idx(self.amino_acids, bulk_ids)
            self.aa_enzyme_idx = bulk_name_to_idx(self.aa_enzymes, bulk_ids)
            self.ppgpp_rxn_metabolites_idx = bulk_name_to_idx(
                self.ppgpp_reaction_metabolites, bulk_ids)
            self.uncharged_trna_idx = bulk_name_to_idx(
                self.uncharged_trna_names, bulk_ids)
            self.charged_trna_idx = bulk_name_to_idx(
                self.charged_trna_names, bulk_ids)
            self.charging_molecule_idx = bulk_name_to_idx(
                self.charging_molecule_names, bulk_ids)
            self.synthetase_idx = bulk_name_to_idx(
                self.synthetase_names, bulk_ids)
            self.ribosome30S_idx = bulk_name_to_idx(
                self.ribosome30S, bulk_ids)
            self.ribosome50S_idx = bulk_name_to_idx(
                self.ribosome50S, bulk_ids)
            self.aa_importer_idx = bulk_name_to_idx(
                self.aa_importers, bulk_ids)
            self.aa_exporter_idx = bulk_name_to_idx(
                self.aa_exporters, bulk_ids)

        # MODEL SPECIFIC: get ribosome elongation rate
        self.ribosomeElongationRate = self.elongation_model.elongation_rate(states)

        # If there are no active ribosomes, return immediately
        if states['active_ribosome']['_entryState'].sum() == 0:
            return {'listeners': {'ribosome_data': {}, 'growth_limits': {}}}

        # Build sequences to request appropriate amount of amino acids to
        # polymerize for next timestep
        proteinIndexes, peptideLengths, = attrs(states['active_ribosome'],
            ['protein_index', 'peptide_length'])

        self.elongation_rates = self.make_elongation_rates(
            self.random_state,
            self.ribosomeElongationRate,
            states['timestep'],
            self.variable_elongation)

        sequences = buildSequences(
            self.proteinSequences,
            proteinIndexes,
            peptideLengths,
            self.elongation_rates)

        sequenceHasAA = (sequences != polymerize.PAD_VALUE)
        aasInSequences = np.bincount(sequences[sequenceHasAA], minlength=21)

        # Calculate AA supply for expected doubling of protein
        dryMass = (states['listeners']['mass']['dry_mass'] * units.fg)
        current_media_id = states['environment']['media_id']
        translation_supply_rate = self.translation_aa_supply[current_media_id] \
            * self.elngRateFactor
        mol_aas_supplied = translation_supply_rate * dryMass * states['timestep'] * units.s
        self.aa_supply = units.strip_empty_units(mol_aas_supplied * self.n_avogadro)


        # MODEL SPECIFIC: Calculate AA request
        fraction_charged, aa_counts_for_translation, requests = \
            self.elongation_model.request(states, aasInSequences)

        # Write to listeners
        listeners = requests.setdefault('listeners', {})
        ribosome_data_listener = listeners.setdefault(
            'ribosome_data', {})
        ribosome_data_listener['translation_supply'
            ] = translation_supply_rate.asNumber()
        growth_limits_listener = requests['listeners'].setdefault(
            'growth_limits', {})
        growth_limits_listener['fraction_trna_charged'
            ] = np.dot(fraction_charged, self.aa_from_trna)
        growth_limits_listener['aa_pool_size'] = counts(states['bulk_total'],
            self.amino_acid_idx)
        growth_limits_listener['aa_request_size'] = aa_counts_for_translation

        return requests


    def evolve_state(self, timestep, states):
        """
        Set ribosome elongation rate based on simulation medium environment and elongation rate factor
        which is used to create single-cell variability in growth rate
        The maximum number of amino acids that can be elongated in a single timestep is set to 22
        intentionally as the minimum number of padding values on the protein sequence matrix is set to 22.
        If timesteps longer than 1.0s are used, this feature will lead to errors in the effective ribosome
        elongation rate.
        """

        update = {
            'listeners': {
                'ribosome_data': {},
                'growth_limits': {}},
            'polypeptide_elongation': {},
            'active_ribosome': {},
            'bulk': []
        }

        # Begin wcEcoli evolveState()
        # Set values for metabolism in case of early return
        update['polypeptide_elongation']['gtp_to_hydrolyze'] = 0
        update['polypeptide_elongation']['aa_count_diff'] = {}

        # Get number of active ribosomes
        n_active_ribosomes = states['active_ribosome']['_entryState'].sum()
        update['listeners']['growth_limits'][
            'active_ribosome_allocated'] = n_active_ribosomes
        update['listeners']['growth_limits']['aa_allocated'] = counts(
            states['bulk'], self.amino_acid_idx)

        # If there are no active ribosomes, return immediately
        if n_active_ribosomes == 0:
            return update
        
        # Polypeptide elongation requires counts to be updated in real-time
        # so make a writeable copy of bulk counts to do so
        states['bulk'] = counts(states['bulk'], range(len(states['bulk'])))

        # Build amino acids sequences for each ribosome to polymerize
        protein_indexes, peptide_lengths, positions_on_mRNA = attrs(
            states['active_ribosome'],
            ['protein_index', 'peptide_length', 'pos_on_mRNA'])

        all_sequences = buildSequences(
            self.proteinSequences,
            protein_indexes,
            peptide_lengths,
            self.elongation_rates + self.next_aa_pad)
        sequences = all_sequences[:, :-self.next_aa_pad].copy()

        if sequences.size == 0:
            return update

        # Calculate elongation resource capacity
        aaCountInSequence = np.bincount(sequences[
            (sequences != polymerize.PAD_VALUE)])
        total_aa_counts = counts(states['bulk'], self.amino_acid_idx)
        charged_trna_counts = counts(states['bulk'], self.charged_trna_idx)

        # MODEL SPECIFIC: Get amino acid counts
        aa_counts_for_translation = self.elongation_model.final_amino_acids(
            total_aa_counts, charged_trna_counts)

        # Using polymerization algorithm elongate each ribosome up to the limits
        # of amino acids, sequence, and GTP
        result = polymerize(
            sequences,
            aa_counts_for_translation,
            10000000,  # Set to a large number, the limit is now taken care of in metabolism
            self.random_state,
            self.elongation_rates[protein_indexes],
            variable_elongation=self.variable_polymerize)

        sequence_elongations = result.sequenceElongation
        aas_used = result.monomerUsages
        nElongations = result.nReactions

        next_amino_acid = all_sequences[np.arange(len(sequence_elongations)),
            sequence_elongations]
        next_amino_acid_count = np.bincount(next_amino_acid[
            next_amino_acid != polymerize.PAD_VALUE], minlength=21)

        # Update masses of ribosomes attached to polymerizing polypeptides
        added_protein_mass = computeMassIncrease(
            sequences,
            sequence_elongations,
            self.aaWeightsIncorporated)

        updated_lengths = peptide_lengths + sequence_elongations
        updated_positions_on_mRNA = positions_on_mRNA + 3*sequence_elongations

        didInitialize = (
            (sequence_elongations > 0) &
            (peptide_lengths == 0))

        added_protein_mass[didInitialize] += self.endWeight

        # Write current average elongation to listener
        currElongRate = (sequence_elongations.sum() /
            n_active_ribosomes) / states['timestep']

        # Ribosomes that reach the end of their sequences are terminated and
        # dissociated into 30S and 50S subunits. The polypeptide that they are
        # polymerizing is converted into a protein in BulkMolecules
        terminalLengths = self.protein_lengths[protein_indexes]

        didTerminate = (updated_lengths == terminalLengths)

        terminatedProteins = np.bincount(
            protein_indexes[didTerminate],
            minlength = self.proteinSequences.shape[0])

        protein_mass, = attrs(states['active_ribosome'], ['massDiff_protein'])
        update['active_ribosome'].update({
            'delete': np.where(didTerminate)[0],
            'set': {
                'massDiff_protein': protein_mass + added_protein_mass,
                'peptide_length': updated_lengths,
                'pos_on_mRNA': updated_positions_on_mRNA,
            }
        })

        update['bulk'].append((self.monomer_idx, terminatedProteins))
        states['bulk'][self.monomer_idx] += terminatedProteins

        nTerminated = didTerminate.sum()
        nInitialized = didInitialize.sum()

        update['bulk'].append((self.ribosome30S_idx, nTerminated))
        update['bulk'].append((self.ribosome50S_idx, nTerminated))
        states['bulk'][self.ribosome30S_idx] += nTerminated
        states['bulk'][self.ribosome50S_idx] += nTerminated

        # MODEL SPECIFIC: evolve
        net_charged, aa_count_diff, evolve_update = self.elongation_model.evolve(
            states,
            total_aa_counts,
            aas_used,
            next_amino_acid_count,
            nElongations,
            nInitialized)

        evolve_bulk_update = evolve_update.pop('bulk')
        update = deep_merge(update, evolve_update)
        update['bulk'].extend(evolve_bulk_update)

        update['polypeptide_elongation']['aa_count_diff'] = aa_count_diff
        # GTP hydrolysis is carried out in Metabolism process for growth
        # associated maintenance. This is passed to metabolism.
        update['polypeptide_elongation'][
            'gtp_to_hydrolyze'] = self.gtpPerElongation * nElongations

        # Write data to listeners
        update['listeners']['growth_limits']['net_charged'] = net_charged
        update['listeners']['growth_limits']['aas_used'] = aas_used
        update['listeners']['growth_limits']['aa_count_diff'] = [
            aa_count_diff.get(id_, 0) for id_ in self.amino_acids]
        
        ribosome_data_listener = update['listeners'].setdefault(
            'ribosome_data', {})
        ribosome_data_listener['effective_elongation_rate'] = currElongRate
        ribosome_data_listener['aa_count_in_sequence'] = aaCountInSequence
        ribosome_data_listener['aa_counts'] = aa_counts_for_translation
        ribosome_data_listener['actual_elongations'
            ] = sequence_elongations.sum()
        ribosome_data_listener['actual_elongation_hist'] = np.histogram(
            sequence_elongations, bins = np.arange(0,23))[0]
        ribosome_data_listener['elongations_non_terminating_hist'
            ] = np.histogram(sequence_elongations[~didTerminate],
                             bins=np.arange(0,23))[0]
        ribosome_data_listener['did_terminate'] = didTerminate.sum()
        ribosome_data_listener['termination_loss'] = (
            terminalLengths - peptide_lengths)[didTerminate].sum()
        ribosome_data_listener['num_trpA_terminated'] = terminatedProteins[
            self.trpAIndex]
        ribosome_data_listener['process_elongation_rate'] = (
            self.ribosomeElongationRate / states['timestep'])

        return update

    def isTimeStepShortEnough(self, inputTimeStep, timeStepSafetyFraction):
        model_specific = self.elongation_model.isTimeStepShortEnough(
            inputTimeStep, timeStepSafetyFraction)
        max_time_step = inputTimeStep <= self.max_time_step
        return model_specific and max_time_step
    

class BaseElongationModel(object):
    """
    Base Model: Request amino acids according to upcoming sequence, assuming
    max ribosome elongation.
    """
    def __init__(self, parameters, process):
        self.parameters = parameters
        self.process = process
        self.basal_elongation_rate = self.parameters['basal_elongation_rate']
        self.ribosomeElongationRateDict = self.parameters[
            'ribosomeElongationRateDict']

    def elongation_rate(self, states):
        """
        Sets ribosome elongation rate accordint to the media; returns 
        max value of 22 amino acids/second.
        """
        current_media_id = states['environment']['media_id']
        rate = self.process.elngRateFactor * self.ribosomeElongationRateDict[
            current_media_id].asNumber(units.aa / units.s)
        return np.min([self.basal_elongation_rate, rate])

    def amino_acid_counts(self, aasInSequences):
        return aasInSequences

    def request(self, states, aasInSequences):
        aa_counts_for_translation = self.amino_acid_counts(aasInSequences)

        requests = {
            'bulk': [(self.process.amino_acid_idx, aa_counts_for_translation)]
        }

        # Not modeling charging so set fraction charged to 0 for all tRNA
        fraction_charged = np.zeros(len(self.process.amino_acid_idx))

        return fraction_charged, aa_counts_for_translation, requests

    def final_amino_acids(self, total_aa_counts, charged_trna_counts):
        return total_aa_counts

    def evolve(self, states, total_aa_counts, aas_used,
        next_amino_acid_count, nElongations, nInitialized):
        # Update counts of amino acids and water to reflect polymerization
        # reactions
        net_charged = np.zeros(len(self.parameters['uncharged_trna_names']))
        return net_charged, {}, {
            'bulk': [(self.process.amino_acid_idx, -aas_used),
                (self.process.water_idx, nElongations - nInitialized)]
        }

    def isTimeStepShortEnough(self, inputTimeStep, timeStepSafetyFraction):
         return True


class TranslationSupplyElongationModel(BaseElongationModel):
    """
    Translation Supply Model: Requests minimum of 1) upcoming amino acid
    sequence assuming max ribosome elongation (ie. Base Model) and 2)
    estimation based on doubling the proteome in one cell cycle (does not
    use ribosome elongation, computed in Parca).
    """
    def __init__(self, parameters, process):
        super().__init__(parameters, process)

    def elongation_rate(self, states):
        """
        Sets ribosome elongation rate accordint to the media; returns 
        max value of 22 amino acids/second.
        """
        return self.basal_elongation_rate

    def amino_acid_counts(self, aasInSequences):
        # Check if this is required. It is a better request but there may be
        # fewer elongations.
        return np.fmin(self.process.aa_supply, aasInSequences)


class SteadyStateElongationModel(TranslationSupplyElongationModel):
    """
    Steady State Charging Model: Requests amino acids based on the
    Michaelis-Menten competitive inhibition model.
    """
    def __init__(self, parameters, process):
        super().__init__(parameters, process)

        # Cell parameters
        self.cellDensity = self.parameters['cellDensity']

        # Names of molecules associated with tRNA charging
        self.charged_trna_names = self.parameters['charged_trna_names']
        self.charging_molecule_names = self.parameters['charging_molecule_names']
        self.synthetase_names = self.parameters['synthetase_names']

        # Data structures for charging
        self.aa_from_synthetase = self.parameters['aa_from_synthetase']
        self.charging_stoich_matrix = self.parameters['charging_stoich_matrix']
        self.charging_molecules_not_aa = np.array([
            mol not in set(self.parameters['amino_acids'])
            for mol in self.charging_molecule_names
            ])

        # ppGpp synthesis
        self.ppgpp_reaction_metabolites = self.parameters[
            'ppgpp_reaction_metabolites']
        self.elong_rate_by_ppgpp = self.parameters['elong_rate_by_ppgpp']

        # Parameters for tRNA charging, ribosome elongation and ppGpp reactions
        self.charging_params = {
            'kS': self.parameters['kS'],
            'KMaa': self.parameters['KMaa'],
            'KMtf': self.parameters['KMtf'],
            'krta': self.parameters['krta'],
            'krtf': self.parameters['krtf'],
            'max_elong_rate': float(self.parameters['elongation_max'].asNumber(
                units.aa / units.s)),
            'charging_mask': np.array([
                aa not in REMOVED_FROM_CHARGING
                for aa in self.parameters['amino_acids']
                ]),
            'unit_conversion': self.parameters['unit_conversion']
        }
        self.ppgpp_params = {
            'KD_RelA': self.parameters['KD_RelA'],
            'k_RelA': self.parameters['k_RelA'],
            'k_SpoT_syn': self.parameters['k_SpoT_syn'],
            'k_SpoT_deg': self.parameters['k_SpoT_deg'],
            'KI_SpoT': self.parameters['KI_SpoT'],
            'ppgpp_reaction_stoich': self.parameters['ppgpp_reaction_stoich'],
            'synthesis_index': self.parameters['synthesis_index'],
            'degradation_index': self.parameters['degradation_index'],
        }

        # Amino acid supply calculations
        self.aa_supply_scaling = self.parameters['aa_supply_scaling']

        # Manage unstable charging with too long time step by setting
        # time_step_short_enough to False during updates. Other variables
        # manage when to trigger an adjustment and how quickly the time step
        # increases after being reduced
        self.time_step_short_enough = True
        self.max_time_step = self.process.max_time_step
        self.time_step_increase = 1.01
        self.max_amino_acid_adjustment = 0.05

        self.amino_acid_synthesis = self.parameters['amino_acid_synthesis']
        self.amino_acid_import = self.parameters['amino_acid_import']
        self.amino_acid_export = self.parameters['amino_acid_export']
        self.get_pathway_enzyme_counts_per_aa = self.parameters[
            'get_pathway_enzyme_counts_per_aa']
        
        # Comparing two values with units is faster than converting units
        # and comparing magnitudes
        self.import_constraint_threshold = self.parameters[
            'import_constraint_threshold'] * vivunits.mM
    
    def elongation_rate(self, states):
        if (self.process.ppgpp_regulation and 
            not self.process.disable_ppgpp_elongation_inhibition
        ):
            cell_mass = states['listeners']['mass']['cell_mass'] * units.fg
            cell_volume = cell_mass / self.cellDensity
            counts_to_molar = 1 / (self.process.n_avogadro * cell_volume)
            ppgpp_count = counts(states['bulk'], self.process.ppgpp_idx)
            ppgpp_conc = ppgpp_count * counts_to_molar
            rate = self.elong_rate_by_ppgpp(ppgpp_conc,
                self.basal_elongation_rate).asNumber(units.aa / units.s)
        else:
            rate = super().elongation_rate(states)
        return rate

    def request(self, states, aasInSequences):
        self.max_time_step = min(self.process.max_time_step,
                                 self.max_time_step * self.time_step_increase)

        # Conversion from counts to molarity
        cell_mass = states['listeners']['mass']['cell_mass'] * units.fg
        dry_mass = states['listeners']['mass']['dry_mass'] * units.fg
        cell_volume = cell_mass / self.cellDensity
        self.counts_to_molar = 1 / (self.process.n_avogadro * cell_volume)

        # ppGpp related concentrations
        ppgpp_conc = self.counts_to_molar * counts(
            states['bulk_total'], self.process.ppgpp_idx)
        rela_conc = self.counts_to_molar * counts(
            states['bulk_total'], self.process.rela_idx)
        spot_conc = self.counts_to_molar * counts(
            states['bulk_total'], self.process.spot_idx)

        # Get counts and convert synthetase and tRNA to a per AA basis
        synthetase_counts = np.dot(
            self.aa_from_synthetase,
            counts(states['bulk_total'], self.process.synthetase_idx))
        aa_counts = counts(states['bulk_total'], self.process.amino_acid_idx)
        uncharged_trna_array = counts(states['bulk_total'],
            self.process.uncharged_trna_idx)
        charged_trna_array = counts(states['bulk_total'],
            self.process.charged_trna_idx)
        uncharged_trna_counts = np.dot(self.process.aa_from_trna,
            uncharged_trna_array)
        charged_trna_counts = np.dot(self.process.aa_from_trna,
            charged_trna_array)
        ribosome_counts = states['active_ribosome']['_entryState'].sum()

        # Get concentration
        f = aasInSequences / aasInSequences.sum()
        synthetase_conc = self.counts_to_molar * synthetase_counts
        aa_conc = self.counts_to_molar * aa_counts
        uncharged_trna_conc = self.counts_to_molar * uncharged_trna_counts
        charged_trna_conc = self.counts_to_molar * charged_trna_counts
        ribosome_conc = self.counts_to_molar * ribosome_counts

        # Calculate amino acid supply
        aa_in_media = np.array([states['boundary']['external'][aa
            ] > self.import_constraint_threshold
            for aa in self.process.aa_environment_names])
        fwd_enzyme_counts, rev_enzyme_counts = self.get_pathway_enzyme_counts_per_aa(
            counts(states['bulk_total'], self.process.aa_enzyme_idx))
        importer_counts = counts(states['bulk_total'], self.process.aa_importer_idx)
        exporter_counts = counts(states['bulk_total'], self.process.aa_exporter_idx)
        synthesis, fwd_saturation, rev_saturation = self.amino_acid_synthesis(fwd_enzyme_counts, rev_enzyme_counts, aa_conc)
        import_rates = self.amino_acid_import(aa_in_media, dry_mass, aa_conc, importer_counts, self.process.mechanistic_aa_transport)
        export_rates = self.amino_acid_export(exporter_counts, aa_conc, self.process.mechanistic_aa_transport)
        exchange_rates = import_rates - export_rates

        supply_function = get_charging_supply_function(
            self.process.aa_supply_in_charging, self.process.mechanistic_translation_supply,
            self.process.mechanistic_aa_transport, self.amino_acid_synthesis,
            self.amino_acid_import, self.amino_acid_export, self.aa_supply_scaling,
            self.counts_to_molar, self.process.aa_supply, fwd_enzyme_counts, rev_enzyme_counts,
            dry_mass, importer_counts, exporter_counts, aa_in_media,
            )

        # Calculate steady state tRNA levels and resulting elongation rate
        self.charging_params['max_elong_rate'] = self.elongation_rate(states)
        (fraction_charged, v_rib, synthesis_in_charging, import_in_charging,
            export_in_charging) = calculate_trna_charging(
            synthetase_conc,
            uncharged_trna_conc,
            charged_trna_conc,
            aa_conc,
            ribosome_conc,
            f,
            self.charging_params,
            supply=supply_function,
            limit_v_rib=True,
            time_limit=states['timestep'])
        
        # Use the supply calculated from each sub timestep while solving the charging steady state
        if self.process.aa_supply_in_charging:
            conversion = 1 / self.counts_to_molar.asNumber(MICROMOLAR_UNITS
                ) / states['timestep']
            synthesis = conversion * synthesis_in_charging
            import_rates = conversion * import_in_charging
            export_rates = conversion * export_in_charging
            self.process.aa_supply = synthesis + import_rates - export_rates
        # Use the supply calculated from the starting amino acid concentrations only
        elif self.process.mechanistic_translation_supply:
            # Set supply based on mechanistic synthesis and supply
            self.process.aa_supply = states['timestep'] * (synthesis + exchange_rates)
        else:
            # Adjust aa_supply higher if amino acid concentrations are low
            # Improves stability of charging and mimics amino acid synthesis
            # inhibition and export
            self.process.aa_supply *= self.aa_supply_scaling(aa_conc, aa_in_media)

        aa_counts_for_translation = (v_rib * f * states['timestep'] /
            self.counts_to_molar.asNumber(MICROMOLAR_UNITS))

        total_trna = charged_trna_array + uncharged_trna_array
        final_charged_trna = stochasticRound(
            self.process.random_state, np.dot(fraction_charged,
            self.process.aa_from_trna * total_trna))

        # Request charged tRNA that will become uncharged
        charged_trna_request = charged_trna_array - final_charged_trna
        charged_trna_request[charged_trna_request < 0] = 0
        uncharged_trna_request = final_charged_trna - charged_trna_array
        uncharged_trna_request[uncharged_trna_request < 0] = 0
        self.uncharged_trna_to_charge = uncharged_trna_request

        self.aa_counts_for_translation = np.array(aa_counts_for_translation)

        fraction_trna_per_aa = total_trna / np.dot(np.dot(
            self.process.aa_from_trna, total_trna), self.process.aa_from_trna)
        total_charging_reactions = stochasticRound(self.process.random_state,
            np.dot(aa_counts_for_translation, self.process.aa_from_trna)
            * fraction_trna_per_aa + uncharged_trna_request)

        # Only request molecules that will be consumed in the charging reactions
        aa_from_uncharging = -self.charging_stoich_matrix @ charged_trna_request
        aa_from_uncharging[self.charging_molecules_not_aa] = 0
        requested_molecules = -np.dot(self.charging_stoich_matrix,
            total_charging_reactions) - aa_from_uncharging
        requested_molecules[requested_molecules < 0] = 0
        self.uncharged_trna_to_charge = uncharged_trna_request

        # ppGpp reactions based on charged tRNA
        request_ppgpp_metabolites = np.zeros(
            len(self.process.ppgpp_reaction_metabolites))
        if self.process.ppgpp_regulation:
            total_trna_conc = self.counts_to_molar * (
                uncharged_trna_counts + charged_trna_counts)
            updated_charged_trna_conc = total_trna_conc * fraction_charged
            updated_uncharged_trna_conc = (
                total_trna_conc - updated_charged_trna_conc)
            delta_metabolites, *_ = ppgpp_metabolite_changes(
                updated_uncharged_trna_conc, updated_charged_trna_conc,
                ribosome_conc, f, rela_conc, spot_conc, ppgpp_conc,
                self.counts_to_molar, v_rib, self.charging_params,
                self.ppgpp_params, states['timestep'], request=True,
                random_state=self.process.random_state,
            )

            request_ppgpp_metabolites = -delta_metabolites
            ppgpp_request = counts(states['bulk'], self.process.ppgpp_idx)

        return fraction_charged, aa_counts_for_translation, {
            'bulk': [
                (self.process.charging_molecule_idx,
                    requested_molecules.astype(int)),
                (self.process.charged_trna_idx,
                    charged_trna_request.astype(int)),
                # Request water for transfer of AA from tRNA for initial polypeptide.
                # This is severe overestimate assuming the worst case that every
                # elongation is initializing a polypeptide. This excess of water
                # shouldn't matter though.
                (self.process.water_idx, int(aa_counts_for_translation.sum())),
                (self.process.ppgpp_idx, ppgpp_request),
                (self.process.ppgpp_rxn_metabolites_idx,
                    request_ppgpp_metabolites.astype(int))],
            'listeners': {
                'growth_limits': {
                    'original_aa_supply': self.process.aa_supply,
                    'aa_in_media': aa_in_media,
                    'synthetase_conc': synthetase_conc.asNumber(MICROMOLAR_UNITS),
                    'uncharged_trna_conc': uncharged_trna_conc.asNumber(MICROMOLAR_UNITS),
                    'charged_trna_conc': charged_trna_conc.asNumber(MICROMOLAR_UNITS),
                    'aa_conc': aa_conc.asNumber(MICROMOLAR_UNITS),
                    'ribosome_conc': ribosome_conc.asNumber(MICROMOLAR_UNITS),
                    'fraction_aa_to_elongate': f,
                    'aa_supply': self.process.aa_supply,
                    'aa_synthesis': synthesis * states['timestep'],
                    'aa_import': import_rates * states['timestep'],
                    'aa_export': export_rates * states['timestep'],
                    'aa_supply_enzymes_fwd': fwd_enzyme_counts,
                    'aa_supply_enzymes_rev': rev_enzyme_counts,
                    'aa_importers': importer_counts,
                    'aa_exporters': exporter_counts,
                    'aa_supply_aa_conc': aa_conc.asNumber(units.mmol/units.L),
                    'aa_supply_fraction_fwd': fwd_saturation,
                    'aa_supply_fraction_rev': rev_saturation,
                    'ppgpp_conc': ppgpp_conc.asNumber(MICROMOLAR_UNITS),
                    'rela_conc': rela_conc.asNumber(MICROMOLAR_UNITS),
                    'spot_conc': spot_conc.asNumber(MICROMOLAR_UNITS)
                }
            },
            'polypeptide_elongation': {
                'aa_exchange_rates': self.counts_to_molar / units.s * (
                    import_rates - export_rates)
            }
        }

    def final_amino_acids(self, total_aa_counts, charged_trna_counts):
        charged_counts_to_uncharge = self.process.aa_from_trna @ charged_trna_counts
        return np.fmin(total_aa_counts + charged_counts_to_uncharge, self.aa_counts_for_translation)

    def evolve(self, states, total_aa_counts, aas_used, next_amino_acid_count, nElongations, nInitialized):
        update = {
            'bulk': [],
            'listeners': {},
        }

        # Get tRNA counts
        uncharged_trna = counts(states['bulk'], self.process.uncharged_trna_idx)
        charged_trna = counts(states['bulk'], self.process.charged_trna_idx)
        total_trna = uncharged_trna + charged_trna

        # Adjust molecules for number of charging reactions that occurred
        ## Determine limitations for charging and uncharging reactions
        charged_and_elongated_per_aa = np.fmax(0, (
            aas_used - self.process.aa_from_trna @ charged_trna))
        aa_for_charging = total_aa_counts - charged_and_elongated_per_aa
        n_aa_charged = np.fmin(aa_for_charging, np.dot(
            self.process.aa_from_trna, np.fmin(self.uncharged_trna_to_charge,
                                               uncharged_trna)))
        n_uncharged_per_aa = aas_used - charged_and_elongated_per_aa

        ## Calculate changes in tRNA based on limitations
        n_trna_charged = self.distribution_from_aa(n_aa_charged, uncharged_trna, True)
        n_trna_uncharged = self.distribution_from_aa(n_uncharged_per_aa, charged_trna, True)

        ## Determine reactions that are charged and elongated in same time step without changing
        ## charged or uncharged counts
        charged_and_elongated = self.distribution_from_aa(charged_and_elongated_per_aa, total_trna)

        ## Determine total number of reactions that occur
        total_uncharging_reactions = charged_and_elongated + n_trna_uncharged
        total_charging_reactions = charged_and_elongated + n_trna_charged
        net_charged = total_charging_reactions - total_uncharging_reactions
        charging_mol_delta = np.dot(self.charging_stoich_matrix,
                                    total_charging_reactions).astype(int)
        update['bulk'].append((self.process.charging_molecule_idx,
                               charging_mol_delta))
        states['bulk'][self.process.charging_molecule_idx] += charging_mol_delta

        ## Account for uncharging of tRNA during elongation
        update['bulk'].append((self.process.charged_trna_idx,
            -total_uncharging_reactions))
        update['bulk'].append((self.process.uncharged_trna_idx,
            total_uncharging_reactions))
        states['bulk'][self.process.charged_trna_idx
                       ] += -total_uncharging_reactions
        states['bulk'][self.process.uncharged_trna_idx
                       ] += total_uncharging_reactions

        # Update proton counts to reflect polymerization reactions and transfer of AA from tRNA
        # Peptide bond formation releases a water but transferring AA from tRNA consumes a OH-
        # Net production of H+ for each elongation, consume extra water for each initialization
        # since a peptide bond doesn't form
        update['bulk'].append((self.process.proton_idx, nElongations))
        update['bulk'].append((self.process.water_idx, -nInitialized))
        states['bulk'][self.process.proton_idx] += nElongations
        states['bulk'][self.process.water_idx] += -nInitialized

        # Create or degrade ppGpp
        # This should come after all countInc/countDec calls since it shares some molecules with
        # other views and those counts should be updated to get the proper limits on ppGpp reactions
        if self.process.ppgpp_regulation:
            v_rib = (nElongations * self.counts_to_molar).asNumber(
                MICROMOLAR_UNITS) / states['timestep']
            ribosome_conc = self.counts_to_molar * states[
                'active_ribosome']['_entryState'].sum()
            updated_uncharged_trna_counts = (counts(states['bulk_total'],
                self.process.uncharged_trna_idx) - net_charged)
            updated_charged_trna_counts = (counts(states['bulk_total'],
                self.process.charged_trna_idx) + net_charged)
            uncharged_trna_conc = self.counts_to_molar * np.dot(
                self.process.aa_from_trna, updated_uncharged_trna_counts)
            charged_trna_conc = self.counts_to_molar * np.dot(
                self.process.aa_from_trna, updated_charged_trna_counts)
            ppgpp_conc = self.counts_to_molar * counts(states['bulk_total'],
                self.process.ppgpp_idx)
            rela_conc = self.counts_to_molar * counts(states['bulk_total'],
                self.process.rela_idx)
            spot_conc = self.counts_to_molar * counts(states['bulk_total'],
                self.process.spot_idx)

            # Need to include the next amino acid the ribosome sees for certain
            # cases where elongation does not occur, otherwise f will be NaN
            aa_at_ribosome = aas_used + next_amino_acid_count
            f = aa_at_ribosome / aa_at_ribosome.sum()
            limits = counts(states['bulk'],
                self.process.ppgpp_rxn_metabolites_idx)
            (delta_metabolites, ppgpp_syn, ppgpp_deg, rela_syn, spot_syn,
                spot_deg, spot_deg_inhibited) = ppgpp_metabolite_changes(
                    uncharged_trna_conc, charged_trna_conc, ribosome_conc, f,
                    rela_conc, spot_conc, ppgpp_conc, self.counts_to_molar,
                    v_rib, self.charging_params, self.ppgpp_params,
                    states['timestep'], random_state=self.process.random_state,
                    limits=limits)

            update['listeners']['growth_limits'] = {
                'rela_syn': rela_syn,
                'spot_syn': spot_syn,
                'spot_deg': spot_deg,
                'spot_deg_inhibited': spot_deg_inhibited,
            }

            update['bulk'].append((self.process.ppgpp_rxn_metabolites_idx,
                delta_metabolites.astype(int)))
            states['bulk'][self.process.ppgpp_rxn_metabolites_idx
                           ] += delta_metabolites.astype(int)

        # Use the difference between (expected AA supply based on expected
        # doubling time and current DCW) and AA used to charge tRNA to update
        # the concentration target in metabolism during the next time step
        aa_used_trna = np.dot(self.process.aa_from_trna, total_charging_reactions)
        aa_diff = self.process.aa_supply - aa_used_trna
        if np.any(np.abs(aa_diff / counts(states['bulk_total'],
            self.process.amino_acid_idx)) > self.max_amino_acid_adjustment
        ):
            self.time_step_short_enough = False

        update['listeners']['growth_limits']['trna_charged'] = aa_used_trna.astype(int)

        return net_charged, {aa: diff for aa, diff in zip(
            self.process.amino_acids, aa_diff)}, update

    def distribution_from_aa(self, n_aa: np.ndarray[int], 
            n_trna: np.ndarray[int], limited: bool=False) -> np.ndarray[int]:
        '''
        Distributes counts of amino acids to tRNAs that are associated with
        each amino acid. Uses self.process.aa_from_trna mapping to distribute
        from amino acids to tRNA based on the fraction that each tRNA species
        makes up for all tRNA species that code for the same amino acid.
        Args:
            n_aa: counts of each amino acid to distribute to each tRNA
            n_trna: counts of each tRNA to determine the distribution
            limited: optional, if True, limits the amino acids
                distributed to each tRNA to the number of tRNA that are
                available (n_trna)
        Returns:
            Distributed counts for each tRNA
        '''

        # Determine the fraction each tRNA species makes up out of all tRNA of
        # the associated amino acid
        with np.errstate(invalid='ignore'):
            f_trna = n_trna / np.dot(np.dot(self.process.aa_from_trna, n_trna),
                self.process.aa_from_trna)
        f_trna[~np.isfinite(f_trna)] = 0

        trna_counts = np.zeros(f_trna.shape, np.int64)
        for count, row in zip(n_aa, self.process.aa_from_trna):
            idx = (row == 1)
            frac = f_trna[idx]

            counts = np.floor(frac * count)
            diff = int(count - counts.sum())

            # Add additional counts to get up to counts to distribute
            # Prevent adding over the number of tRNA available if limited
            if diff > 0:
                if limited:
                    for _ in range(diff):
                        frac[(n_trna[idx] - counts) == 0] = 0
                        # normalize for multinomial distribution
                        frac /= frac.sum()  
                        adjustment = self.process.random_state.multinomial(
                            1, frac)
                        counts += adjustment
                else:
                    adjustment = self.process.random_state.multinomial(
                        diff, frac)
                    counts += adjustment

            trna_counts[idx] = counts

        return trna_counts

    def isTimeStepShortEnough(self, inputTimeStep, timeStepSafetyFraction):
        short_enough = True

        # Needs to be less than the max time step to prevent oscillatory
        # behavior
        if inputTimeStep > self.max_time_step:
            short_enough = False

        # Decrease the max time step to get more stable charging
        if (not self.time_step_short_enough) and (
            self.process.adjust_timestep_for_charging
        ):
            self.max_time_step = inputTimeStep / 2
            self.time_step_short_enough = True
            short_enough = False

        return short_enough


def ppgpp_metabolite_changes(
    uncharged_trna_conc: Unum, 
    charged_trna_conc: Unum,
    ribosome_conc: Unum, 
    f: np.ndarray[float], 
    rela_conc: Unum, 
    spot_conc: Unum, 
    ppgpp_conc: Unum, 
    counts_to_molar: Unum,
    v_rib: Unum, 
    charging_params: dict[str, Any], 
    ppgpp_params: dict[str, Any], 
    time_step: float,
    request: bool=False, 
    limits: np.ndarray[float]=None, 
    random_state: np.random.RandomState=None) -> tuple[
        np.ndarray[int], int, int, Unum, Unum, Unum, Unum]:
    '''
    Calculates the changes in metabolite counts based on ppGpp synthesis and
    degradation reactions.
    
    Args:
        uncharged_trna_conc: concentration (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`) 
            of uncharged tRNA associated with each amino acid
        charged_trna_conc: concentration (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`) 
            of charged tRNA associated with each amino acid
        ribosome_conc: concentration (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`) 
            of active ribosomes
        f: fraction of each amino acid to be incorporated
            to total amino acids incorporated
        rela_conc: concentration (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`) of RelA
        spot_conc: concentration (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`) of SpoT
        ppgpp_conc: concentration (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`) of ppGpp
        counts_to_molar: conversion factor
            from counts to molarity (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`)
        v_rib: rate of amino acid incorporation at the ribosome (units of uM/s)
        charging_params: parameters used in charging equations
        ppgpp_params: parameters used in ppGpp reactions
        time_step: length of the current time step
        request: if True, only considers reactant stoichiometry,
            otherwise considers reactants and products. For use in
            calculateRequest. GDP appears as both a reactant and product
            and the request can be off the actual use if not handled in this
            manner.
        limits: counts of molecules that are available to prevent
            negative total counts as a result of delta_metabolites.
            If None, no limits are placed on molecule changes.
        random_state: random state for the process
    Returns: 
        7-element tuple containing 
        
        - **delta_metabolites**: the change in counts of each metabolite
          involved in ppGpp reactions
        - **n_syn_reactions**: the number of ppGpp synthesis reactions
        - **n_deg_reactions**: the number of ppGpp degradation reactions
        - **v_rela_syn**: rate of synthesis from RelA per amino
          acid tRNA species
        - **v_spot_syn**: rate of synthesis from SpoT
        - **v_deg**: rate of degradation from SpoT
        - **v_deg_inhibited**: rate of degradation from SpoT per
          amino acid tRNA species
    '''

    if random_state is None:
        random_state = np.random.RandomState()

    uncharged_trna_conc = uncharged_trna_conc.asNumber(MICROMOLAR_UNITS)
    charged_trna_conc = charged_trna_conc.asNumber(MICROMOLAR_UNITS)
    ribosome_conc = ribosome_conc.asNumber(MICROMOLAR_UNITS)
    rela_conc = rela_conc.asNumber(MICROMOLAR_UNITS)
    spot_conc = spot_conc.asNumber(MICROMOLAR_UNITS)
    ppgpp_conc = ppgpp_conc.asNumber(MICROMOLAR_UNITS)
    counts_to_micromolar = counts_to_molar.asNumber(MICROMOLAR_UNITS)

    numerator = 1 + charged_trna_conc / charging_params['krta'] + uncharged_trna_conc / charging_params['krtf']
    saturated_charged = charged_trna_conc / charging_params['krta'] / numerator
    saturated_uncharged = uncharged_trna_conc / charging_params['krtf'] / numerator
    if v_rib == 0:
        ribosome_conc_a_site = f * ribosome_conc
    else:
        ribosome_conc_a_site = f * v_rib / (saturated_charged * charging_params['max_elong_rate'])
    ribosomes_bound_to_uncharged = ribosome_conc_a_site * saturated_uncharged

    # Handle rare cases when tRNA concentrations are 0
    # Can result in inf and nan so assume a fraction of ribosomes
    # bind to the uncharged tRNA if any tRNA are present or 0 if not
    mask = ~np.isfinite(ribosomes_bound_to_uncharged)
    ribosomes_bound_to_uncharged[mask] = ribosome_conc * f[mask] * np.array(
        uncharged_trna_conc[mask] + charged_trna_conc[mask] > 0)

    # Calculate active fraction of RelA
    competitive_inhibition = 1 + ribosomes_bound_to_uncharged / ppgpp_params['KD_RelA']
    inhibition_product = np.product(competitive_inhibition)
    with np.errstate(divide='ignore'):
        frac_rela = 1 / (ppgpp_params['KD_RelA'] / ribosomes_bound_to_uncharged * inhibition_product / competitive_inhibition + 1)

    # Calculate rates for synthesis and degradation
    v_rela_syn = ppgpp_params['k_RelA'] * rela_conc * frac_rela
    v_spot_syn = ppgpp_params['k_SpoT_syn'] * spot_conc
    v_syn = v_rela_syn.sum() + v_spot_syn
    max_deg = ppgpp_params['k_SpoT_deg'] * spot_conc * ppgpp_conc
    fractions = uncharged_trna_conc / ppgpp_params['KI_SpoT']
    v_deg =  max_deg / (1 + fractions.sum())
    v_deg_inhibited = (max_deg - v_deg) * fractions / fractions.sum()

    # Convert to discrete reactions
    n_syn_reactions = stochasticRound(random_state, v_syn * time_step / counts_to_micromolar)[0]
    n_deg_reactions = stochasticRound(random_state, v_deg * time_step / counts_to_micromolar)[0]

    # Only look at reactant stoichiometry if requesting molecules to use
    if request:
        ppgpp_reaction_stoich = np.zeros_like(ppgpp_params['ppgpp_reaction_stoich'])
        reactants = ppgpp_params['ppgpp_reaction_stoich'] < 0
        ppgpp_reaction_stoich[reactants] = ppgpp_params['ppgpp_reaction_stoich'][reactants]
    else:
        ppgpp_reaction_stoich = ppgpp_params['ppgpp_reaction_stoich']

    # Calculate the change in metabolites and adjust to limits if provided
    # Possible reactions are adjusted down to limits if the change in any
    # metabolites would result in negative counts
    max_iterations = int(n_deg_reactions + n_syn_reactions + 1)
    old_counts = None
    for it in range(max_iterations):
        delta_metabolites = (ppgpp_reaction_stoich[:, ppgpp_params['synthesis_index']] * n_syn_reactions
            + ppgpp_reaction_stoich[:, ppgpp_params['degradation_index']] * n_deg_reactions)

        if limits is None:
            break
        else:
            final_counts = delta_metabolites + limits

            if np.all(final_counts >= 0) or (old_counts is not None and np.all(final_counts == old_counts)):
                break

            limited_index = np.argmin(final_counts)
            if ppgpp_reaction_stoich[limited_index, ppgpp_params['synthesis_index']] < 0:
                limited = np.ceil(final_counts[limited_index] / ppgpp_reaction_stoich[limited_index, ppgpp_params['synthesis_index']])
                n_syn_reactions -= min(limited, n_syn_reactions)
            if ppgpp_reaction_stoich[limited_index, ppgpp_params['degradation_index']] < 0:
                limited = np.ceil(final_counts[limited_index] / ppgpp_reaction_stoich[limited_index, ppgpp_params['degradation_index']])
                n_deg_reactions -= min(limited, n_deg_reactions)

            old_counts = final_counts
    else:
        raise ValueError('Failed to meet molecule limits with ppGpp reactions.')

    return delta_metabolites, n_syn_reactions, n_deg_reactions, v_rela_syn, v_spot_syn, v_deg, v_deg_inhibited


def calculate_trna_charging(
    synthetase_conc: Unum, 
    uncharged_trna_conc: Unum, 
    charged_trna_conc: Unum, 
    aa_conc: Unum, 
    ribosome_conc: float,
    f: Unum, 
    params: dict[str, Any], 
    supply: Callable=None, 
    time_limit: float=1000, 
    limit_v_rib: bool=False, 
    use_disabled_aas: bool=False
    ) -> tuple[Unum, float, Unum, Unum, Unum]:
    '''
    Calculates the steady state value of tRNA based on charging and 
    incorporation through polypeptide elongation. The fraction of 
    charged/uncharged is also used to determine how quickly the 
    ribosome is elongating. All concentrations are given in units of 
    :py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`.
    
    Args:
        synthetase_conc: concentration of synthetases associated with 
            each amino acid
        uncharged_trna_conc: concentration of uncharged tRNA associated 
            with each amino acid
        charged_trna_conc: concentration of charged tRNA associated with 
            each amino acid
        aa_conc: concentration of each amino acid
        ribosome_conc: concentration of active ribosomes
        f: fraction of each amino acid to be incorporated to total amino 
            acids incorporated
        params: parameters used in charging equations
        supply: function to get the rate of amino acid supply (synthesis 
            and import) based on amino acid concentrations. If None, amino 
            acid concentrations remain constant during charging
        time_limit: time limit to reach steady state
        limit_v_rib: if True, v_rib is limited to the number of amino acids 
            that are available
        use_disabled_aas: if False, amino acids in 
            :py:data:`~ecoli.processes.polypeptide_elongation.REMOVED_FROM_CHARGING` 
            are excluded from charging
    
    Returns:
        5-element tuple containing
        - **new_fraction_charged**: fraction of total tRNA that is charged for each
          amino acid species
        - **v_rib**: ribosomal elongation rate in units of uM/s
        - **total_synthesis**: the total amount of amino acids synthesized during charging
          in units of MICROMOLAR_UNITS. Will be zeros if supply function is not given.
        - **total_import**: the total amount of amino acids imported during charging
          in units of MICROMOLAR_UNITS. Will be zeros if supply function is not given.
        - **total_export**: the total amount of amino acids exported during charging
          in units of MICROMOLAR_UNITS. Will be zeros if supply function is not given.
    '''

    def negative_check(trna1: np.ndarray[float], trna2: np.ndarray[float]):
        '''
        Check for floating point precision issues that can lead to small
        negative numbers instead of 0. Adjusts both species of tRNA to
        bring concentration of trna1 to 0 and keep the same total concentration.
        
        Args:
            trna1: concentration of one tRNA species (charged or uncharged)
            trna2: concentration of another tRNA species (charged or uncharged)
        '''

        mask = trna1 < 0
        trna2[mask] = trna1[mask] + trna2[mask]
        trna1[mask] = 0

    def dcdt(t: float, c: np.ndarray[float]) -> np.ndarray[float]:
        '''
        Function for solve_ivp to integrate
        
        Args:
            c: 1D array of concentrations of uncharged and charged tRNAs
                dims: 2 * number of amino acids (uncharged tRNA come first, then charged)
            t: time of integration step
        
        Returns:
            Array of dc/dt for tRNA concentrations
                dims: 2 * number of amino acids (uncharged tRNA come first, then charged)
        '''

        v_charging, dtrna, daa = dcdt_jit(t, c, n_aas_masked, n_aas, mask,
            params['kS'], synthetase_conc, params['KMaa'], params['KMtf'],
            f, params['krta'], params['krtf'], params['max_elong_rate'],
            ribosome_conc, limit_v_rib, aa_rate_limit, v_rib_max)

        if supply is None:
            v_synthesis = np.zeros(n_aas)
            v_import = np.zeros(n_aas)
            v_export = np.zeros(n_aas)
        else:
            aa_conc = c[2*n_aas_masked:2*n_aas_masked+n_aas]
            v_synthesis, v_import, v_export = supply(unit_conversion * aa_conc)
            v_supply = v_synthesis + v_import - v_export
            daa[mask] = v_supply[mask] - v_charging

        return np.hstack((-dtrna, dtrna, daa, v_synthesis, v_import, v_export))

    # Convert inputs for integration
    synthetase_conc = synthetase_conc.asNumber(MICROMOLAR_UNITS)
    uncharged_trna_conc = uncharged_trna_conc.asNumber(MICROMOLAR_UNITS)
    charged_trna_conc = charged_trna_conc.asNumber(MICROMOLAR_UNITS)
    aa_conc = aa_conc.asNumber(MICROMOLAR_UNITS)
    ribosome_conc = ribosome_conc.asNumber(MICROMOLAR_UNITS)
    unit_conversion = params['unit_conversion']

    # Remove disabled amino acids from calculations
    n_total_aas = len(aa_conc)
    if use_disabled_aas:
        mask = np.ones(n_total_aas, bool)
    else:
        mask = params['charging_mask']
    synthetase_conc = synthetase_conc[mask]
    original_uncharged_trna_conc = uncharged_trna_conc[mask]
    original_charged_trna_conc = charged_trna_conc[mask]
    original_aa_conc = aa_conc[mask]
    f = f[mask]

    n_aas = len(aa_conc)
    n_aas_masked = len(original_aa_conc)

    # Limits for integration
    aa_rate_limit = original_aa_conc / time_limit
    trna_rate_limit = original_charged_trna_conc / time_limit
    v_rib_max = max(0, ((aa_rate_limit + trna_rate_limit) / f).min())

    # Integrate rates of charging and elongation
    c_init = np.hstack((original_uncharged_trna_conc, original_charged_trna_conc,
        aa_conc, np.zeros(n_aas), np.zeros(n_aas), np.zeros(n_aas)))
    sol = solve_ivp(dcdt, [0, time_limit], c_init, method='BDF')
    c_sol = sol.y.T

    # Determine new values from integration results
    final_uncharged_trna_conc = c_sol[-1, :n_aas_masked]
    final_charged_trna_conc = c_sol[-1, n_aas_masked:2*n_aas_masked]
    total_synthesis = c_sol[-1, 2*n_aas_masked+n_aas:2*n_aas_masked+2*n_aas]
    total_import = c_sol[-1, 2*n_aas_masked+2*n_aas:2*n_aas_masked+3*n_aas]
    total_export = c_sol[-1, 2*n_aas_masked+3*n_aas:2*n_aas_masked+4*n_aas]

    negative_check(final_uncharged_trna_conc, final_charged_trna_conc)
    negative_check(final_charged_trna_conc, final_uncharged_trna_conc)

    fraction_charged = final_charged_trna_conc / (final_uncharged_trna_conc + final_charged_trna_conc)
    numerator_ribosome = 1 + np.sum(f * (params['krta'] / final_charged_trna_conc + final_uncharged_trna_conc / final_charged_trna_conc * params['krta'] / params['krtf']))
    v_rib = params['max_elong_rate'] * ribosome_conc / numerator_ribosome
    if limit_v_rib:
        v_rib_max = max(0, ((original_aa_conc + (original_charged_trna_conc - final_charged_trna_conc)) / time_limit / f).min())
        v_rib = min(v_rib, v_rib_max)

    # Replace SEL fraction charged with average
    new_fraction_charged = np.zeros(n_total_aas)
    new_fraction_charged[mask] = fraction_charged
    new_fraction_charged[~mask] = fraction_charged.mean()

    return new_fraction_charged, v_rib, total_synthesis, total_import, total_export


@njit(error_model='numpy')
def dcdt_jit(t, c, n_aas_masked, n_aas, mask,
    kS, synthetase_conc, KMaa, KMtf,
    f, krta, krtf, max_elong_rate,
    ribosome_conc, limit_v_rib, aa_rate_limit, v_rib_max
):
    uncharged_trna_conc = c[:n_aas_masked]
    charged_trna_conc = c[n_aas_masked:2*n_aas_masked]
    aa_conc = c[2*n_aas_masked:2*n_aas_masked+n_aas]
    masked_aa_conc = aa_conc[mask]

    v_charging = (kS * synthetase_conc * uncharged_trna_conc * masked_aa_conc / (KMaa[mask] * KMtf[mask])
        / (1 + uncharged_trna_conc/KMtf[mask] + masked_aa_conc/KMaa[mask] + uncharged_trna_conc*masked_aa_conc/KMtf[mask]/KMaa[mask]))
    numerator_ribosome = 1 + np.sum(f * (krta / charged_trna_conc + uncharged_trna_conc / charged_trna_conc * krta / krtf))
    v_rib = max_elong_rate * ribosome_conc / numerator_ribosome

    # Handle case when f is 0 and charged_trna_conc is 0
    if not np.isfinite(v_rib):
        v_rib = 0

    # Limit v_rib and v_charging to the amount of available amino acids
    if limit_v_rib:
        v_charging = np.fmin(v_charging, aa_rate_limit)
        v_rib = min(v_rib, v_rib_max)

    dtrna = v_charging - v_rib*f
    daa = np.zeros(n_aas)

    return v_charging, dtrna, daa


def get_charging_supply_function(
        supply_in_charging: bool,
        mechanistic_supply: bool,
        mechanistic_aa_transport: bool,
        amino_acid_synthesis: Callable,
        amino_acid_import: Callable,
        amino_acid_export: Callable,
        aa_supply_scaling: Callable,
        counts_to_molar: Unum,
        aa_supply: np.ndarray[float],
        fwd_enzyme_counts: np.ndarray[int],
        rev_enzyme_counts: np.ndarray[int],
        dry_mass: Unum,
        importer_counts: np.ndarray[int],
        exporter_counts: np.ndarray[int],
        aa_in_media: np.ndarray[bool],
        ) -> Optional[
            Callable[[np.ndarray[float]], 
            Tuple[Unum, Unum, Unum]]]:
    """
    Get a function mapping internal amino acid concentrations to the amount of
    amino acid supply expected.
    
    Args:
        supply_in_charging: True if using aa_supply_in_charging option
        mechanistic_supply: True if using mechanistic_translation_supply option
        mechanistic_aa_transport: True if using mechanistic_aa_transport option
        amino_acid_synthesis: function to provide rates of synthesis for amino
            acids based on the internal state
        amino_acid_import: function to provide import rates for amino
            acids based on the internal and external state
        amino_acid_export: function to provide export rates for amino
            acids based on the internal state
        aa_supply_scaling: function to scale the amino acid supply based
            on the internal state
        counts_to_molar: conversion factor for counts to molar 
            (:py:data:`~ecoli.processes.polypeptide_elongation.MICROMOLAR_UNITS`)
        aa_supply: rate of amino acid supply expected
        fwd_enzyme_counts: enzyme counts in forward reactions for each amino acid
        rev_enzyme_counts: enzyme counts in loss reactions for each amino acid
        dry_mass: dry mass of the cell with mass units
        importer_counts: counts for amino acid importers
        exporter_counts: counts for amino acid exporters
        aa_in_media: True for each amino acid that is present in the media
    Returns:
        Function that provides the amount of supply (synthesis, import, export)
        for each amino acid based on the internal state of the cell
    """

    # Create functions that are only dependent on amino acid concentrations for more stable
    # charging and amino acid concentrations.  If supply_in_charging is not set, then
    # setting None will maintain constant amino acid concentrations throughout charging.
    supply_function = None
    if supply_in_charging:
        counts_to_molar = counts_to_molar.asNumber(MICROMOLAR_UNITS)
        zeros = counts_to_molar * np.zeros_like(aa_supply)
        if mechanistic_supply:
            if mechanistic_aa_transport:
                supply_function = lambda aa_conc: (
                    counts_to_molar * amino_acid_synthesis(fwd_enzyme_counts, rev_enzyme_counts, aa_conc)[0],
                    counts_to_molar * amino_acid_import(aa_in_media, dry_mass, aa_conc, importer_counts, mechanistic_aa_transport),
                    counts_to_molar * amino_acid_export(exporter_counts, aa_conc, mechanistic_aa_transport),
                    )
            else:
                supply_function = lambda aa_conc: (
                    counts_to_molar * amino_acid_synthesis(fwd_enzyme_counts, rev_enzyme_counts, aa_conc)[0],
                    counts_to_molar * amino_acid_import(aa_in_media, dry_mass, aa_conc, importer_counts, mechanistic_aa_transport),
                    zeros,
                    )
        else:
            supply_function = lambda aa_conc: (
                counts_to_molar * aa_supply * aa_supply_scaling(aa_conc, aa_in_media),
                zeros,
                zeros,
                )

    return supply_function


def test_polypeptide_elongation(return_data=False):
    def make_elongation_rates(random, base, time_step, variable_elongation=False):
        size = 1
        lengths = time_step * np.full(size, base, dtype=np.int64)
        lengths = stochasticRound(random, lengths) if random else np.round(lengths)
        return lengths.astype(np.int64)

    test_config = {
        'time_step': 2,
        'proteinIds': np.array(['TRYPSYN-APROTEIN[c]']),
        'ribosome30S': 'CPLX0-3953[c]',
        'ribosome50S': 'CPLX0-3962[c]',
        'make_elongation_rates': make_elongation_rates,
        'proteinLengths': np.array([245]),  # this is the length of proteins in proteinSequences
        'translation_aa_supply': {
            'minimal': (units.mol / units.fg / units.min) * np.array([
                6.73304301e-21, 3.63835219e-21, 2.89772671e-21, 3.88086822e-21,
                5.04645651e-22, 4.45295877e-21, 2.64600664e-21, 5.35711230e-21,
                1.26817689e-21, 3.81168405e-21, 5.66834531e-21, 4.30576056e-21,
                1.70428208e-21, 2.24878356e-21, 2.49335033e-21, 3.47019761e-21,
                3.83858460e-21, 6.34564026e-22, 1.86880523e-21, 1.40959498e-27,
                5.20884460e-21])},
        'proteinSequences': np.array([[
            12, 10, 18, 9, 13, 1, 10, 9, 9, 16, 20, 9, 18, 15, 9, 10, 20, 4, 20, 13, 7, 15, 9, 18, 4, 10, 13, 15, 14, 1,
            2, 14, 11, 8, 20, 0, 16, 13, 7, 8, 12, 13, 7, 1, 10, 0, 14, 10, 13, 7, 10, 11, 20, 5, 4, 1, 11, 14, 16, 3,
            0, 5, 15, 18, 7, 2, 0, 9, 18, 9, 0, 2, 8, 6, 2, 2, 18, 3, 12, 20, 16, 0, 15, 2, 9, 20, 6, 14, 14, 16, 20,
            16, 20, 7, 11, 11, 15, 10, 10, 17, 9, 14, 13, 13, 7, 6, 10, 18, 17, 10, 16, 7, 2, 10, 10, 9, 3, 1, 2, 2, 1,
            16, 11, 0, 8, 7, 16, 9, 0, 5, 20, 20, 2, 8, 13, 11, 11, 1, 1, 9, 15, 9, 17, 12, 13, 14, 5, 7, 16, 1, 15, 1,
            7, 1, 7, 10, 10, 14, 13, 11, 16, 7, 0, 13, 8, 0, 0, 9, 0, 0, 7, 20, 14, 9, 9, 14, 20, 4, 20, 15, 16, 16, 15,
            2, 11, 9, 2, 10, 2, 1, 10, 8, 2, 7, 10, 20, 9, 20, 5, 12, 10, 14, 14, 9, 3, 20, 15, 6, 18, 7, 11, 3, 6, 20,
            1, 5, 10, 0, 0, 8, 4, 1, 15, 9, 12, 5, 6, 11, 9, 0, 5, 10, 3, 11, 5, 20, 0, 5, 1, 5, 0, 0, 7, 11, 20, -1,
            -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1,
            -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1]]).astype(np.int8)
    }

    polypep_elong = PolypeptideElongation(test_config)

    initial_state = {
        'environment': {'media_id': 'minimal'},
        'bulk': np.array([
            ('CPLX0-3953[c]', 100),
            ('CPLX0-3962[c]', 100),
            ('TRYPSYN-APROTEIN[c]', 0),
            ('RELA', 0),
            ('SPOT', 0),
            ('H2O', 0),
            ('PROTON', 0),
            ('ppGpp', 0),
            ] + [(aa, 100) for aa in DEFAULT_AA_NAMES],
            dtype=[('id', 'U40'), ('count', int)]),
        'unique': {
            'active_ribosome': np.array([
                (1, 1, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0)
            ], dtype=[
                ('_entryState', np.bool_), ('unique_index', int),
                ('protein_index', int), ('peptide_length', int),
                ('pos_on_mRNA', int), ('massDiff_DNA', '<f8'),
                ('massDiff_mRNA', '<f8'), ('massDiff_metabolite', '<f8'),
                ('massDiff_miscRNA', '<f8'), ('massDiff_nonspecific_RNA',
                '<f8'), ('massDiff_protein', '<f8'), ('massDiff_rRNA',
                '<f8'), ('massDiff_tRNA', '<f8'), ('massDiff_water', '<f8')])
        },
        'listeners': {
            'mass': {
                'dry_mass': 350.0
            }
        }
    }
    
    settings = {
        'total_time': 200,
        'initial_state': initial_state,
        'topology': TOPOLOGY}
    data = simulate_process(polypep_elong, settings)

    if return_data:
        return data, test_config


def run_plot(data, config):

    # plot a list of variables
    bulk_ids = ['CPLX0-3953[c]', 'CPLX0-3962[c]', 'TRYPSYN-APROTEIN[c]',
        'RELA', 'SPOT', 'H2O', 'PROTON', 'ppGpp'] + [
            aa for aa in DEFAULT_AA_NAMES]
    variables = [(bulk_id,) for bulk_id in bulk_ids]

    # format data
    bulk_timeseries = np.array(data['bulk'])
    for i, bulk_id in enumerate(bulk_ids):
        data[bulk_id] = bulk_timeseries[:, i]

    plot_variables(
        data,
        variables=variables,
        out_dir='out/processes/polypeptide_elongation',
        filename='variables'
    )


def main():
    data, config = test_polypeptide_elongation(return_data=True)
    run_plot(data, config)


if __name__ == '__main__':
    main()
