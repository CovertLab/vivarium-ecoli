from warnings import warn
import numpy as np
from typing import Any

from vivarium.core.emitter import timeseries_from_data

from ecoli.analysis.tablereader_utils import (
    camel_case_to_underscored)

ANY_STRING = (bytes, str)

MAPPING = {
    'BulkMolecules': {
        # Use Blame to get ATP used per process
        # 'atpAllocatedFinal': None,
        'counts': ('bulk',),
        'atpRequested': ('listeners', 'atp', 'atp_requested',),
        'atpAllocatedInitial': ('listeners', 'atp', 'atp_allocated_initial',),
    },
    'EnzymeKinetics': {
        'actualFluxes': ('listeners', 'enzyme_kinetics', 'actual_fluxes'),
        'metaboliteCountsFinal': ('listeners', 'enzyme_kinetics', 'metabolite_counts_final'),
        'targetFluxesLower': ('listeners', 'enzyme_kinetics', 'target_fluxes_lower'),
        'metaboliteCountsInit': ('listeners', 'enzyme_kinetics', 'metabolite_counts_init'),
        'targetFluxesUpper': ('listeners', 'enzyme_kinetics', 'target_fluxes_upper'),
        'countsToMolar': ('listeners', 'enzyme_kinetics', 'counts_to_molar'),
        'enzymeCountsInit': ('listeners', 'enzyme_kinetics', 'enzyme_counts_init'),
        'targetFluxes': ('listeners', 'enzyme_kinetics', 'target_fluxes'),
        'targetAAConc': ('listeners', 'enzyme_kinetics', 'target_aa_conc'),
    },
    'GrowthLimits': {
        'aaAllocated': ('listeners', 'growth_limits', 'aa_allocated'),
        'aasUsed': ('listeners', 'growth_limits', 'aas_used'),
        'ntpRequestSize': ('listeners', 'growth_limits', 'ntp_request_size'),
        'aaPoolSize': ('listeners', 'growth_limits', 'aa_pool_size'),
        'activeRibosomeAllocated': ('listeners', 'growth_limits', 'active_ribosome_allocated'),
        'ntpUsed': ('listeners', 'growth_limits', 'ntp_used'),
        'aaRequestSize': ('listeners', 'growth_limits', 'aa_request_size'),
        'uncharged_trna_conc': ('listeners', 'growth_limits', 'uncharged_trna_conc'),
        'charged_trna_conc': ('listeners', 'growth_limits', 'charged_trna_conc'),
        'aa_conc': ('listeners', 'growth_limits', 'aa_conc'),
        'ribosome_conc': ('listeners', 'growth_limits', 'ribosome_conc'),
        'fraction_aa_to_elongate': ('listeners', 'growth_limits', 'fraction_aa_to_elongate'),
        'fraction_trna_charged': ('listeners', 'growth_limits', 'fraction_trna_charged'),
        'synthetase_conc': ('listeners', 'growth_limits', 'synthetase_conc'),
        'ppgpp_conc': ('listeners', 'growth_limits', 'ppgpp_conc'),
        'rela_conc': ('listeners', 'growth_limits', 'rela_conc'),
        'spot_conc': ('listeners', 'growth_limits', 'spot_conc'),
        'rela_syn': ('listeners', 'growth_limits', 'rela_syn'),
        'spot_syn': ('listeners', 'growth_limits', 'spot_syn'),
        'spot_deg': ('listeners', 'growth_limits', 'spot_deg'),
        'spot_deg_inhibited': ('listeners', 'growth_limits', 'spot_deg_inhibited'),
        'original_aa_supply': ('listeners', 'growth_limits', 'original_aa_supply'),
        'aa_supply': ('listeners', 'growth_limits', 'aa_supply'),
        'aa_synthesis': ('listeners', 'growth_limits', 'aa_synthesis'),
        'aa_import': ('listeners', 'growth_limits', 'aa_import'),
        'aa_export': ('listeners', 'growth_limits', 'aa_export'),
        'aa_supply_enzymes_fwd': ('listeners', 'growth_limits', 'aa_supply_enzymes_fwd'),
        'aa_supply_enzymes_rev': ('listeners', 'growth_limits', 'aa_supply_enzymes_rev'),
        'aa_importers': ('listeners', 'growth_limits', 'aa_importers'),
        'aa_exporters': ('listeners', 'growth_limits', 'aa_exporters'),
        'aa_supply_aa_conc': ('listeners', 'growth_limits', 'aa_supply_aa_conc'),
        'aa_supply_fraction_fwd': ('listeners', 'growth_limits', 'aa_supply_fraction_fwd'),
        'aa_supply_fraction_rev': ('listeners', 'growth_limits', 'aa_supply_fraction_rev'),
        'aa_in_media': ('listeners', 'growth_limits', 'aa_in_media'),
        'aaCountDiff': ('listeners', 'growth_limits', 'aa_count_diff'),
        'trnaCharged': ('listeners', 'growth_limits', 'trna_charged'),
        'fraction_trna_charged': ('listeners', 'growth_limits', 'fraction_trna_charged'),
        'net_charged': ('listeners', 'growth_limits', 'net_charged'),
        'ntpAllocated': ('listeners', 'growth_limits', 'ntp_allocated'),
        'ntpPoolSize': ('listeners', 'growth_limits', 'ntp_pool_size'),
    },
    'RNACounts': {
        'mRNA_counts': ('listeners', 'rna_counts', 'mRNA_counts'),
        'full_mRNA_counts': ('listeners', 'rna_counts', 'full_mRNA_counts'),
        'partial_mRNA_counts': ('listeners', 'rna_counts', 'partial_mRNA_counts'),
        'full_mRNA_cistron_counts': ('listeners', 'rna_counts', 'full_mRNA_cistron_counts'),
        'partial_mRNA_cistron_counts': ('listeners', 'rna_counts', 'partial_mRNA_cistron_counts'),
        'partial_rRNA_counts': ('listeners', 'rna_counts', 'partial_rRNA_counts'),
        'partial_rRNA_cistron_counts': ('listeners', 'rna_counts', 'partial_rRNA_cistron_counts'),
        'mRNA_counts': ('listeners', 'rna_counts', 'mRNA_counts'),
    },
    'RnapData': {
        'active_rnap_coordinates': ('listeners', 'rnap_data', 'active_rnap_coordinates'),
        'active_rnap_domain_indexes': ('listeners', 'rnap_data', 'active_rnap_domain_indexes'),
        'active_rnap_n_bound_ribosomes': ('listeners', 'rnap_data', 'active_rnap_n_bound_ribosomes'),
        'active_rnap_unique_indexes': ('listeners', 'rnap_data', 'active_rnap_unique_indexes'),
        'active_rnap_on_stable_RNA_indexes': ('listeners', 'rnap_data', 'active_rnap_on_stable_RNA_indexes'),
        'active_rnap_n_bound_ribosomes': ('listeners', 'rnap_data', 'active_rnap_n_bound_ribosomes'),
        'actualElongations': ('listeners', 'rnap_data', 'actual_elongations'),
        'codirectional_collision_coordinates': (
            'listeners', 'rnap_data', 'codirectional_collision_coordinates'),
        'didInitialize': ('listeners', 'rnap_data', 'did_initialize'),
        'didStall': ('listeners', 'rnap_data', 'did_stall'),
        'didTerminate': ('listeners', 'rnap_data', 'did_terminate'),
        'headon_collision_coordinates': (
            'listeners', 'rnap_data', 'headon_collision_coordinates'),
        'n_codirectional_collisions': ('listeners', 'rnap_data', 'n_codirectional_collisions'),
        'n_headon_collisions': ('listeners', 'rnap_data', 'n_headon_collisions'),
        'n_removed_ribosomes': ('listeners', 'rnap_data', 'n_removed_ribosomes'),
        'n_total_collisions': ('listeners', 'rnap_data', 'n_total_collisions'),
        'rnaInitEvent': ('listeners', 'rnap_data', 'rna_init_event'),
        'rna_init_event_per_cistron': ('listeners', 'rnap_data', 'rna_init_event_per_cistron'),
        'terminationLoss': ('listeners', 'rnap_data', 'termination_loss'),
    },
    'ComplexationListener': {
        'complexationEvents': ('listeners', 'complexation_listener', 'complexation_events'),
    },
    'EquilibriumListener': {
        'reactionRates': ('listeners', 'equilibrium_listener', 'reaction_rates'),
    },
    'Main': {
        # TODO: Figure out where to add listener for timestep
        # 'timeStepSec': None,
        'time': ('time',)
    },
    'ReplicationData': {
        'fork_coordinates': ('listeners', 'replication_data', 'fork_coordinates'),
        'free_DnaA_boxes': ('listeners', 'replication_data', 'free_DnaA_boxes'),
        'criticalInitiationMass': ('listeners', 'replication_data', 'critical_initiation_mass'),
        'fork_domains': ('listeners', 'replication_data', 'fork_domains'),
        'numberOfOric': ('listeners', 'replication_data', 'number_of_oric'),
        'criticalMassPerOriC': ('listeners', 'replication_data', 'critical_mass_per_oriC'),
        'fork_unique_index': ('listeners', 'replication_data', 'fork_unique_index'),
        'total_DnaA_boxes': ('listeners', 'replication_data', 'total_DnaA_boxes'),
    },
    'RnaSynthProb': {
        'nActualBound': ('listeners', 'rna_synth_prob', 'n_actual_bound'),
        'target_rna_synth_prob': ('listeners', 'rna_synth_prob', 'target_rna_synth_prob'),
        'actual_rna_synth_prob': ('listeners', 'rna_synth_prob', 'actual_rna_synth_prob'),
        'tu_is_overcrowded': ('listeners', 'rna_synth_prob', 'tu_is_overcrowded'),
        'actual_rna_synth_prob_per_cistron': ('listeners', 'rna_synth_prob', 'actual_rna_synth_prob_per_cistron'),
        'target_rna_synth_prob_per_cistron': ('listeners', 'rna_synth_prob', 'target_rna_synth_prob_per_cistron'),
        'expected_rna_init_per_cistron': ('listeners', 'rna_synth_prob', 'expected_rna_init_per_cistron'),
        'promoter_copy_number': ('listeners', 'rna_synth_prob', 'promoter_copy_number'),
        'bound_TF_coordinates': ('listeners', 'rna_synth_prob', 'bound_TF_coordinates'),
        'n_available_promoters': (
            'listeners', 'rna_synth_prob', 'n_available_promoters'),
        'bound_TF_domains': ('listeners', 'rna_synth_prob', 'bound_TF_domains'),
        'n_bound_TF_per_TU': ('listeners', 'rna_synth_prob', 'n_bound_TF_per_TU'),
        'n_bound_TF_per_cistron': ('listeners', 'rna_synth_prob', 'n_bound_TF_per_cistron'),
        'bound_TF_indexes': ('listeners', 'rna_synth_prob', 'bound_TF_indexes'),
        'nPromoterBound': ('listeners', 'rna_synth_prob', 'n_promoter_bound'),
        'gene_copy_number': ('listeners', 'rna_synth_prob', 'gene_copy_number'),
        'pPromoterBound': ('listeners', 'rna_synth_prob', 'p_promoter_bound'),
        'nActualBound': ('listeners', 'rna_synth_prob', 'n_actual_bound'),
    },
    'UniqueMoleculeCounts': {
        'DnaA_box': ('listeners', 'unique_molecule_counts', 'DnaA_box'),
        'RNA': ('listeners', 'unique_molecule_counts', 'RNA'),
        'active_RNAP': ('listeners', 'unique_molecule_counts', 'active_RNAP'),
        'active_replisome': ('listeners', 'unique_molecule_counts', 'active_replisome'),
        'active_ribosome': ('listeners', 'unique_molecule_counts', 'active_ribosome'),
        'chromosomal_segment': ('listeners', 'unique_molecule_counts', 'chromosomal_segment'),
        'chromosome_domain': ('listeners', 'unique_molecule_counts', 'chromosome_domain'),
        'full_chromosome': ('listeners', 'unique_molecule_counts', 'full_chromosome'),
        'gene': ('listeners', 'unique_molecule_counts', 'gene'),
        'oriC': ('listeners', 'unique_molecule_counts', 'oriC'),
        'promoter': ('listeners', 'unique_molecule_counts', 'promoter'),
    },
    'DnaSupercoiling': {
        'segment_domain_indexes': ('listeners', 'dna_supercoiling', 'segment_domain_indexes'),
        'segment_left_boundary_coordinates': ('listeners', 'dna_supercoiling', 'segment_left_boundary_coordinates'),
        'segment_right_boundary_coordinates': ('listeners', 'dna_supercoiling', 'segment_right_boundary_coordinates'),
        'segment_superhelical_densities': ('listeners', 'dna_supercoiling', 'segment_superhelical_densities'),
    },
    # Not implemented (can turn on profiling in vivarium Engine)
    # 'EvaluationTime': {
    #     'append_times': None,
    #     'merge_times': None,
    #     'append_total': None,
    #     'merge_total': None,
    #     'partition_times': None,
    #     'calculate_mass_times': None,
    #     'partition_total': None,
    #     'calculate_mass_total': None,
    #     'simulationStep': None,
    #     'calculate_request_times': None,
    #     'time': ('time', ),
    #     'calculate_request_total': None,
    #     'update_queries_times': None,
    #     'clock_time': None,
    #     'update_queries_total': None,
    #     'evolve_state_times': None,
    #     'update_times': None,
    #     'evolve_state_total': None,
    #     'update_total': None,
    #     'attributes': None
    # },
    'Mass': {
        'inner_membrane_mass': ('listeners', 'mass', 'inner_membrane_mass'),
        'proteinMass': ('listeners', 'mass', 'protein_mass'),
        'cellMass': ('listeners', 'mass', 'cell_mass'),
        'instantaneousGrowthRate': ('listeners', 'mass', 'instantaneous_growth_rate'),
        'rnaMass': ('listeners', 'mass', 'rna_mass'),
        'cellVolume': ('listeners', 'mass', 'volume'),
        'membrane_mass': ('listeners', 'mass', 'membrane_mass'),
        'rRnaMass': ('listeners', 'mass', 'rRna_mass'),
        'cytosol_mass': ('listeners', 'mass', 'cytosol_mass'),
        'mRnaMass': ('listeners', 'mass', 'mRna_mass'),
        'dnaMass': ('listeners', 'mass', 'dna_mass'),
        'outer_membrane_mass': ('listeners', 'mass', 'outer_membrane_mass'),
        'smallMoleculeMass': ('listeners', 'mass', 'smallMolecule_mass'),
        'dryMass': ('listeners', 'mass', 'dry_mass'),
        'periplasm_mass': ('listeners', 'mass', 'periplasm_mass'),
        'extracellular_mass': ('listeners', 'mass', 'extracellular_mass'),
        'pilus_mass': ('listeners', 'mass', 'pilus_mass'),
        'tRnaMass': ('listeners', 'mass', 'tRna_mass'),
        'flagellum_mass': ('listeners', 'mass', 'flagellum_mass'),
        # Not implemented (can turn on blame if interested)
        # 'processMassDifferences': None,
        'waterMass': ('listeners', 'mass', 'water_mass'),
        'growth': ('listeners', 'mass', 'growth'),
        'projection_mass': ('listeners', 'mass', 'projection_mass'),
    },
    'RibosomeData': {
        'aaCountInSequence': (
            'listeners', 'ribosome_data', 'aa_count_in_sequence'),
        'aaCounts': (
            'listeners', 'ribosome_data', 'aa_counts'),
        'actualElongationHist': (
            'listeners', 'ribosome_data', 'actual_elongation_hist'),
        'actualElongations': ('listeners', 'ribosome_data', 'actual_elongations'),
        'didInitialize': ('listeners', 'ribosome_data', 'did_initialize'),
        'didTerminate': ('listeners', 'ribosome_data', 'did_terminate'),
        'effectiveElongationRate': ('listeners', 'ribosome_data', 'effective_elongation_rate'),
        'elongationsNonTerminatingHist': (
            'listeners', 'ribosome_data', 'elongations_non_terminating_hist'),
        'n_ribosomes_on_partial_mRNA_per_transcript': ('listeners', 'ribosome_data', 'n_ribosomes_on_partial_mRNA_per_transcript'),
        'n_ribosomes_per_transcript': ('listeners', 'ribosome_data', 'n_ribosomes_per_transcript'),
        'numTrpATerminated': ('listeners', 'ribosome_data', 'num_trpA_terminated'),
        'actual_prob_translation_per_transcript': ('listeners',
                                         'ribosome_data',
                                         'actual_prob_translation_per_transcript'),
        'target_prob_translation_per_transcript': ('listeners',
                                         'ribosome_data',
                                         'target_prob_translation_per_transcript'),
        'processElongationRate': ('listeners', 'ribosome_data', 'process_elongation_rate'),
        'rRNA16S_initiated': ('listeners', 'ribosome_data', 'rRNA16S_initiated'),
        'rRNA23S_initiated': ('listeners', 'ribosome_data', 'rRNA23S_initiated'),
        'rRNA5S_initiated': ('listeners', 'ribosome_data', 'rRNA5S_initiated'),
        'rRNA16S_init_prob': ('listeners', 'ribosome_data', 'rRNA16S_init_prob'),
        'rRNA23S_init_prob': ('listeners', 'ribosome_data', 'rRNA23S_init_prob'),
        'rRNA5S_init_prob': ('listeners', 'ribosome_data', 'rRNA5S_init_prob'),
        'total_rna_init': ('listeners', 'ribosome_data', 'total_rna_init'),
        'total_rRNA_initiated': ('listeners', 'ribosome_data', 'total_rRNA_initiated'),
        'total_rRNA_init_prob': ('listeners', 'ribosome_data', 'total_rRNA_init_prob'),
        'translationSupply': (
            'listeners', 'ribosome_data', 'translation_supply'),
        'mRNA_is_overcrowded': ('listeners', 'ribosome_data', 'mRNA_is_overcrowded'),
        'n_ribosomes_on_each_mRNA': ('listeners', 'ribosome_data', 'n_ribosomes_on_each_mRNA'),
        'mRNA_TU_index': ('listeners', 'ribosome_data', 'mRNA_TU_index'),
        'protein_mass_on_polysomes': ('listeners', 'ribosome_data', 'protein_mass_on_polysomes'),
        'ribosome_init_event_per_monomer': ('listeners', 'ribosome_data', 'ribosome_init_event_per_monomer'),
    },
    'FBAResults': {
        'objectiveValue': ('listeners', 'fba_results', 'objective_value'),
        'catalyst_counts': ('listeners', 'fba_results', 'catalyst_counts'),
        'reactionFluxes': ('listeners', 'fba_results', 'reaction_fluxes'),
        'base_reaction_fluxes': ('listeners', 'fba_results', 'base_reaction_fluxes'),
        'coefficient': ('listeners', 'fba_results', 'coefficient'),
        'reducedCosts': ('listeners', 'fba_results', 'reduced_costs'),
        'conc_updates': ('listeners', 'fba_results', 'conc_updates'),
        'shadowPrices': ('listeners', 'fba_results', 'shadow_prices'),
        'constrained_molecules': (
            'listeners', 'fba_results', 'constrained_molecules'),
        'deltaMetabolites': (
            'listeners', 'fba_results', 'delta_metabolites'),
        'targetConcentrations': (
            'listeners', 'fba_results', 'target_concentrations'),
        'externalExchangeFluxes': (
            'listeners', 'fba_results', 'external_exchange_fluxes'),
        'homeostaticObjectiveValues': (
            'listeners', 'fba_results', 'homeostatic_objective_values'),
        'translation_gtp': ('listeners', 'fba_results', 'translation_gtp'),
        'kineticObjectiveValues': (
            'listeners', 'fba_results', 'kinetic_objective_values'),
        'unconstrained_molecules': (
            'listeners', 'fba_results', 'unconstrained_molecules'),
        'media_id': ('listeners', 'fba_results', 'media_id'),
        'uptake_constraints': (
            'listeners', 'fba_results', 'uptake_constraints'),
    },
    'MonomerCounts': {
        'monomerCounts': ('listeners', 'monomer_counts'),
    },
    'RnaDegradationListener': {
        'fragmentBasesDigested': (
            'listeners', 'rna_degradation_listener', 'fragment_bases_digested'),
        'countRnaDegraded': (
            'listeners', 'rna_degradation_listener', 'count_rna_degraded'),
        'count_RNA_degraded_per_cistron': (
            'listeners', 'rna_degradation_listener', 'count_RNA_degraded_per_cistron'),
        'nucleotidesFromDegradation': (
            'listeners', 'rna_degradation_listener', 'nucleotides_from_degradation'),
        'DiffRelativeFirstOrderDecay': (
            'listeners', 'rna_degradation_listener', 'diff_relative_first_order_decay'),
        'FractEndoRRnaCounts': (
            'listeners', 'rna_degradation_listener', 'fract_endo_rrna_counts'),
        'FractionActiveEndoRNases': (
            'listeners', 'rna_degradation_listener', 'fraction_active_endornases'),
    },
    'TranscriptElongationListener': {
        'attenuation_probability': (
            'listeners', 'transcript_elongation_listener', 'attenuation_probability'),
        'countRnaSynthesized': (
            'listeners', 'transcript_elongation_listener', 'count_rna_synthesized'),
        'counts_attenuated': (
            'listeners', 'transcript_elongation_listener', 'counts_attenuated'),
        'countNTPsUSed': (
            'listeners', 'transcript_elongation_listener', 'count_NTPs_used'),
    },
    'RnaMaturation': {
        'total_maturation_events': (
            'listeners', 'rna_maturation_listener', 'total_maturation_events'),
        'total_degraded_ntps': (
            'listeners', 'rna_maturation_listener', 'total_degraded_ntps'),
        'unprocessed_rnas_consumed': (
            'listeners', 'rna_maturation_listener', 'unprocessed_rnas_consumed'),
        'mature_rnas_generated': (
            'listeners', 'rna_maturation_listener', 'mature_rnas_generated'),
        'maturation_enzyme_counts': (
            'listeners', 'rna_maturation_listener', 'maturation_enzyme_counts'),
    }
}

class TableReaderError(Exception):
    """
    Base exception class for TableReader-associated exceptions.
    """
    pass


class VersionError(TableReaderError):
    """
    An error raised when the input files claim to be from a different format or
    version of the file specification.
    """
    pass


class DoesNotExistError(TableReaderError):
    """
    An error raised when a column or attribute does not seem to exist.
    """
    pass


class VariableLengthColumnError(TableReaderError):
    """
    An error raised when the user tries to access subcolumns of a variable
    length column.
    """
    pass


class TableReader(object):
    """
    Fake TableReader. In wcEcoli, the TableReader class was used to access data saved from simulations.
    This class provides a bridge in order to port analyses over to vivarium-ecoli without significant modification.
    Given a path within the wcEcoli output structure and timeseries data from a vivarium-ecoli experiment,
    this class provides a way to retrieve data as if it were structured in the same way as it is in wcEcoli.

    Parameters:
        path (str): which listener this TableReader would be reading from
        data (dict): data from a vivarium-ecoli experiment
        emitted_config (dict): configuration emitted at start of experiment
        timeseries_data (bool): whether data is a timeseries (e.g. passed through timeseries_from_data)
    """

    def __init__(self, path, data, emitted_config=None, timeseries_data=False):
        self._path = path

        # Store reference to the data
        if not timeseries_data:
            data = timeseries_from_data(data)
        self._data = data

        # Store reference to simulation metadata
        self.emitted_config = emitted_config

        # List the column file names.
        self._mapping = MAPPING[path]
        self._columnNames = {
            k for k in self._mapping.keys()}

    @property
    def path(self):
        # type: () -> str
        return self._path
    
    def readAttribute(self, name):
        # type: (str) -> Any
        """
        If emitted_config is supplied during initialization, this can be used
        to access listener metadata (e.g. bulk molecule names for count arrays)
        """
        viv_path = self._mapping[name]
        sim_metadata = self.emitted_config['metadata']['output_metadata'] or {}
        for key in viv_path:
            sim_metadata = sim_metadata.get(key, {})
        return sim_metadata

    def readColumn(self, name, indices=None, squeeze=True):
        # type: (str, Any, bool) -> np.ndarray
        """
        Load a full column (all rows). Each row entry is a 1-D NumPy array of
        subcolumns, so the initial result is a 2-D array row x subcolumn, which
        is optionally squeezed to arrays with lower dimensions if squeeze=True.
        In the case of fixed-length columns, this method can optionally read
        just a vertical slice of all those arrays -- the subcolumns at the
        given `indices`. For variable-length columns, np.nan is used as a
        filler value for the empty entries of each row.

        Parameters:
            name: The name of the column.
            indices: The subcolumn indices to select from each entry. This can
                be any value that works to index an ndarray along 1 dimension,
                or None for all the data. Specifying this argument
                for variable-length columns will throw an error.
            squeeze: If True, the resulting NumPy array is squeezed into a 0D,
                1D, or 2D array, depending on the number of rows and subcolumns
                it has.
                1 row x 1 subcolumn => 0D.
                n rows x 1 subcolumn or 1 row x m subcolumns => 1D.
                n rows x m subcolumns => 2D.

        Returns:
            ndarray: A writable 0D, 1D, or 2D array.
        """
        # Squeeze if flag is set to True
        viv_path = self._mapping[name]
        if callable(viv_path):
            result = viv_path(self._data)
        elif isinstance(viv_path, tuple):
            result = self._data
            for elem in viv_path:
                if callable(elem):
                    result = elem(result)
                else:
                    result = result[elem]
        else:
            # No explicit mapping defined, try heuristic mapping
            heuristic_path = ('listeners',
                              camel_case_to_underscored(self._path),
                              camel_case_to_underscored(name))

            warn(f'No explicit mapping defined from {self._path + "/" + name} to a path in vivarium data,\n'
                 f'trying heuristic mapping: {heuristic_path}.\n'
                 'If this works, consider adding an explicit mapping in tablereader.py!')

            result = self._data
            for elem in heuristic_path:
                result = result[elem]

        try:
            result = np.array(result).T
        # Handle non-homogeneous lengths (e.g. unique molecule coordinates)
        except:
            result = np.array(result, dtype=object)

        # extract indices
        if indices is not None:
            result = result[:, indices]

        if squeeze:
            result = result.squeeze()

        return result

    def columnNames(self):
        """
        Returns the names of all columns.
        """
        return list(self._columnNames)

    def close(self):
        """
        Does nothing.
        """
        pass


def _check_bulk_inputs(mol_names):
    """
    Use to check and adjust mol_names inputs for functions that read bulk
    molecules to get consistent argument handling in both functions.
    """

    # Wrap an array in a tuple to ensure correct dimensions
    if not isinstance(mol_names, tuple):
        mol_names = (mol_names,)

    # Check for string instead of array since it will cause mol_indices lookup to fail
    for names in mol_names:
        if isinstance(names, ANY_STRING):
            raise Exception('mol_names tuple must contain arrays not strings like {!r}'.format(names))

    return mol_names


def read_bulk_molecule_counts(data, mol_names):
    '''
    Reads a subset of molecule counts from BulkMolecules using the indexing method
    of readColumn. Should only be called once per simulation being analyzed with
    all molecules of interest.
    '''

    mol_names = _check_bulk_inputs(mol_names)

    bulk_reader = TableReader('BulkMolecules', data)
    bulk_molecule_names = bulk_reader.readColumn("objectNames")
    mol_indices = {mol: i for i, mol in enumerate(bulk_molecule_names)}

    lengths = [len(names) for names in mol_names]
    indices = np.hstack([[mol_indices[mol] for mol in names] for names in mol_names])
    bulk_counts = bulk_reader.readColumn('counts', indices, squeeze=False)

    start_slice = 0
    for length in lengths:
        counts = bulk_counts[:, start_slice:start_slice + length].squeeze()
        start_slice += length
        yield counts


def test_table_reader():
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file()
    sim.total_time = 4
    sim.emitter = 'timeseries'
    sim.divide = False
    sim.build_ecoli()
    sim.run()
    data = sim.query()
    emitted_config = {
        'state': sim.ecoli_experiment.state.get_config()
    }

    timeseries = timeseries_from_data(data)

    for listener, columns in MAPPING.items():
        tb = TableReader(listener, timeseries,
            emitted_config, timeseries_data=True)
        for column in columns.keys():
            col_data = tb.readColumn(column)
            col_metadata = tb.readAttribute(column)


if __name__ == "__main__":
    test_table_reader()
