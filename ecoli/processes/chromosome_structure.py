"""
====================
Chromosome Structure
====================

- Resolve collisions between molecules and replication forks on the chromosome.
- Remove and replicate promoters and motifs that are traversed by replisomes.
- Reset the boundaries and linking numbers of chromosomal segments.
"""
import numpy as np
import warnings
from vivarium.core.process import Step

from ecoli.processes.registries import topology_registry
from ecoli.library.schema import (create_unique_indexes, listener_schema,
    numpy_schema, attrs, bulk_name_to_idx)
from wholecell.utils.polymerize import buildSequences

# Register default topology for this process, associating it with process name
NAME = 'ecoli-chromosome-structure'
TOPOLOGY = {
    "bulk": ('bulk',),
    "listeners": ("listeners",),
    "active_replisomes": ("unique", "active_replisome",),
    "oriCs": ("unique", "oriC",),
    "chromosome_domains": ("unique", "chromosome_domain",),
    "active_RNAPs": ("unique", "active_RNAP"),
    "RNAs": ("unique", "RNA"),
    "active_ribosome": ("unique", "active_ribosome"),
    "full_chromosomes": ("unique", "full_chromosome",),
    "promoters": ("unique", "promoter"),
    "DnaA_boxes": ("unique", "DnaA_box"),
    "genes": ("unique", "gene"),
    # TODO(vivarium): Only include if superhelical density flag is passed
    # "chromosomal_segments": ("unique", "chromosomal_segment")
    "global_time": ("global_time",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", "chromosome_structure"),
}
topology_registry.register(NAME, TOPOLOGY)

class ChromosomeStructure(Step):
    """ Chromosome Structure Process """

    name = NAME
    topology = TOPOLOGY
    defaults = {
            # Load parameters
            'RNA_sequences': [],
            'protein_sequences': [],
            'n_TUs': 1,
            'n_TFs': 1,
            'n_amino_acids': 1,
            'n_fragment_bases': 1,
            'replichore_lengths': [0,0],
            'relaxed_DNA_base_pairs_per_turn': 1,
            'terC_index': 1,

            'calculate_superhelical_densities': False,

            # Get placeholder value for chromosome domains without children
            'no_child_place_holder': -1,

            # Load bulk molecule views
            'inactive_RNAPs': [],
            'fragmentBases': [],
            'ppi': 'ppi',
            'active_tfs': [],
            'ribosome_30S_subunit': '30S',
            'ribosome_50S_subunit': '50S',
            'amino_acids': [],
            'water': 'water',
            'seed': 0,
            'emit_unique': False
        }

    # Constructor
    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.rna_sequences = self.parameters['rna_sequences']
        self.protein_sequences = self.parameters['protein_sequences']
        self.n_TUs = self.parameters['n_TUs']
        self.n_TFs = self.parameters['n_TFs']
        self.n_amino_acids = self.parameters['n_amino_acids']
        self.n_fragment_bases = self.parameters['n_fragment_bases']
        replichore_lengths = self.parameters['replichore_lengths']
        self.min_coordinates = -replichore_lengths[1]
        self.max_coordinates = replichore_lengths[0]
        self.relaxed_DNA_base_pairs_per_turn = self.parameters[
            'relaxed_DNA_base_pairs_per_turn']
        self.terC_index = self.parameters['terC_index']

        self.n_mature_rnas = self.parameters['n_mature_rnas']
        self.mature_rna_ids = self.parameters['mature_rna_ids']
        self.mature_rna_end_positions = self.parameters[
            'mature_rna_end_positions']
        self.mature_rna_nt_counts = self.parameters['mature_rna_nt_counts']
        self.unprocessed_rna_index_mapping = self.parameters[
            'unprocessed_rna_index_mapping']

        # Load sim options
        self.calculate_superhelical_densities = self.parameters[
            'calculate_superhelical_densities']

        # Get placeholder value for chromosome domains without children
        self.no_child_place_holder = self.parameters['no_child_place_holder']

        self.inactive_RNAPs = self.parameters['inactive_RNAPs']
        self.fragmentBases = self.parameters['fragmentBases']
        self.ppi = self.parameters['ppi']
        self.active_tfs = self.parameters['active_tfs']
        self.ribosome_30S_subunit = self.parameters['ribosome_30S_subunit']
        self.ribosome_50S_subunit = self.parameters['ribosome_50S_subunit']
        self.amino_acids = self.parameters['amino_acids']
        self.water = self.parameters['water']

        self.inactive_RNAPs_idx = None

        self.emit_unique = self.parameters.get('emit_unique', True)

        self.chromosome_segment_index = 0
        self.promoter_index = 60000
        self.DnaA_box_index = 60000

        self.random_state = np.random.RandomState(
            seed=self.parameters['seed'])

    def ports_schema(self):
        ports = {
            'listeners': {
                'rnap_data': listener_schema({
                    'n_total_collisions': 0,
                    'n_headon_collisions': 0,
                    'n_codirectional_collisions': 0,
                    'headon_collision_coordinates': [-1],
                    'codirectional_collision_coordinates': [-1],
                    'n_removed_ribosomes': 0})},
            'bulk': numpy_schema('bulk'),

            # Unique molecules
            'active_replisomes': numpy_schema('active_replisomes',
                emit=self.parameters['emit_unique']),
            'oriCs': numpy_schema('oriCs',
                emit=self.parameters['emit_unique']),
            'chromosome_domains': numpy_schema('chromosome_domains',
                emit=self.parameters['emit_unique']),
            'active_RNAPs': numpy_schema('active_RNAPs',
                emit=self.parameters['emit_unique']),
            'RNAs': numpy_schema('RNAs',
                emit=self.parameters['emit_unique']),
            'active_ribosome': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),
            'full_chromosomes': numpy_schema('full_chromosomes',
                emit=self.parameters['emit_unique']),
            'promoters': numpy_schema('promoters',
                emit=self.parameters['emit_unique']),
            'DnaA_boxes': numpy_schema('DnaA_boxes',
                emit=self.parameters['emit_unique']),
            'genes': numpy_schema('genes',
                emit=self.parameters['emit_unique']),
            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']},
            'next_update_time': {
                '_default': self.parameters['time_step'],
                '_updater': 'set',
                '_divider': 'set'},
        }

        # TODO: Work on this functionality
        if self.calculate_superhelical_densities:
            ports['chromosomal_segments'] = {
                '*': {
                    'boundary_molecule_indexes': {'_default':
                        np.empty((0, 2), dtype=np.int64)},
                    'boundary_coordinates': {'_default':
                        np.empty((0, 2), dtype=np.int64)},
                    'domain_index': {'_default': 0},
                    'linking_number': {'_default': 0}}}

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
        # At t=0, convert all strings to indices
        if self.inactive_RNAPs_idx is None:
            self.fragmentBasesIdx = bulk_name_to_idx(
                self.fragmentBases, states['bulk']['id'])
            self.active_tfs_idx = bulk_name_to_idx(
                self.active_tfs, states['bulk']['id'])
            self.ribosome_30S_subunit_idx = bulk_name_to_idx(
                self.ribosome_30S_subunit, states['bulk']['id'])
            self.ribosome_50S_subunit_idx = bulk_name_to_idx(
                self.ribosome_50S_subunit, states['bulk']['id'])
            self.amino_acids_idx = bulk_name_to_idx(
                self.amino_acids, states['bulk']['id'])
            self.water_idx = bulk_name_to_idx(
                self.water, states['bulk']['id'])
            self.ppi_idx = bulk_name_to_idx(
                self.ppi, states['bulk']['id'])
            self.inactive_RNAPs_idx = bulk_name_to_idx(
                self.inactive_RNAPs, states['bulk']['id'])
            self.mature_rna_idx = bulk_name_to_idx(
                self.mature_rna_ids, states['bulk']['id'])

        # Read unique molecule attributes
        (replisome_domain_indexes, replisome_coordinates,
            replisome_unique_indexes) = attrs(states['active_replisomes'],
                ['domain_index', 'coordinates', 'unique_index'])
        (all_chromosome_domain_indexes, child_domains) = attrs(
            states['chromosome_domains'], ['domain_index', 'child_domains'])
        (RNAP_domain_indexes, RNAP_coordinates, RNAP_is_forward,
            RNAP_unique_indexes) = attrs(states['active_RNAPs'],
            ['domain_index', 'coordinates', 'is_forward', 'unique_index'])
        origin_domain_indexes, = attrs(states['oriCs'], ['domain_index'])
        mother_domain_indexes, = attrs(states['full_chromosomes'],
            ['domain_index'])
        (RNA_TU_indexes, transcript_lengths, RNA_RNAP_indexes,
            RNA_unique_indexes) = attrs(states['RNAs'], ['TU_index',
                'transcript_length', 'RNAP_index', 'unique_index'])
        (ribosome_protein_indexes, ribosome_peptide_lengths,
            ribosome_mRNA_indexes) = attrs(states['active_ribosome'],
                ['protein_index', 'peptide_length', 'mRNA_index'])
        (promoter_TU_indexes, promoter_domain_indexes, promoter_coordinates,
            promoter_bound_TFs) = attrs(states['promoters'],
                ['TU_index', 'domain_index', 'coordinates', 'bound_TF'])
        (gene_cistron_indexes, gene_domain_indexes,
            gene_coordinates) = attrs(states['genes'],
                ['cistron_index', 'domain_index', 'coordinates'])
        (DnaA_box_domain_indexes, DnaA_box_coordinates,
            DnaA_box_bound) = attrs(states['DnaA_boxes'],
                ['domain_index', 'coordinates', 'DnaA_bound'])

        # Build dictionary of replisome coordinates with domain indexes as keys
        replisome_coordinates_from_domains = {
            domain_index: replisome_coordinates[
                replisome_domain_indexes == domain_index]
            for domain_index in np.unique(replisome_domain_indexes)
            }


        def get_removed_molecules_mask(domain_indexes, coordinates):
            """
            Computes the boolean mask of unique molecules that should be
            removed based on the progression of the replication forks.
            """
            mask = np.zeros_like(domain_indexes, dtype=np.bool_)

            # Loop through all domains
            for domain_index in np.unique(domain_indexes):
                # Domain has active replisomes
                if domain_index in replisome_coordinates_from_domains:
                    domain_replisome_coordinates = \
                        replisome_coordinates_from_domains[domain_index]

                    # Get mask for molecules on this domain that are out of range
                    domain_mask = np.logical_and.reduce((
                        domain_indexes == domain_index,
                        coordinates > domain_replisome_coordinates.min(),
                        coordinates < domain_replisome_coordinates.max()))

                # Domain has no active replisomes
                else:
                    # Domain has child domains (has finished replicating)
                    if (child_domains[all_chromosome_domain_indexes ==
                        domain_index, 0] != self.no_child_place_holder
                    ):
                        # Remove all molecules on this domain
                        domain_mask = (domain_indexes == domain_index)
                    # Domain has not started replication
                    else:
                        continue

                mask[domain_mask] = True

            return mask


        # Build mask for molecules that should be removed
        removed_RNAPs_mask = get_removed_molecules_mask(
            RNAP_domain_indexes, RNAP_coordinates)
        removed_promoters_mask = get_removed_molecules_mask(
            promoter_domain_indexes, promoter_coordinates)
        removed_genes_mask = get_removed_molecules_mask(
            gene_domain_indexes, gene_coordinates)
        removed_DnaA_boxes_mask = get_removed_molecules_mask(
            DnaA_box_domain_indexes, DnaA_box_coordinates)

        # Get attribute arrays of remaining RNAPs
        remaining_RNAPs_mask = np.logical_not(removed_RNAPs_mask)
        remaining_RNAP_domain_indexes = RNAP_domain_indexes[
            remaining_RNAPs_mask]
        remaining_RNAP_coordinates = RNAP_coordinates[remaining_RNAPs_mask]
        remaining_RNAP_unique_indexes = RNAP_unique_indexes[
            remaining_RNAPs_mask]

        # Build masks for head-on and co-directional collisions between RNAPs
        # and replication forks
        RNAP_headon_collision_mask = np.logical_and(
            removed_RNAPs_mask,
            np.logical_xor(RNAP_is_forward, RNAP_coordinates > 0))
        RNAP_codirectional_collision_mask = np.logical_and(
            removed_RNAPs_mask, np.logical_not(RNAP_headon_collision_mask))

        n_total_collisions = np.count_nonzero(removed_RNAPs_mask)
        n_headon_collisions = np.count_nonzero(RNAP_headon_collision_mask)
        n_codirectional_collisions = np.count_nonzero(
            RNAP_codirectional_collision_mask)

        # Write values to listeners
        update = {
            'listeners': {
                'rnap_data': {
                    'n_total_collisions': n_total_collisions,
                    'n_headon_collisions': n_headon_collisions,
                    'n_codirectional_collisions': n_codirectional_collisions,
                    'headon_collision_coordinates': RNAP_coordinates[
                        RNAP_headon_collision_mask],
                    'codirectional_collision_coordinates': RNAP_coordinates[
                        RNAP_codirectional_collision_mask]
                }
            },
            'bulk': [],
            'active_replisomes': {},
            'oriCs': {},
            'chromosome_domains': {},
            'active_RNAPs': {},
            'RNAs': {},
            'active_ribosome': {},
            'full_chromosomes': {},
            'promoters': {},
            'genes': {},
            'DnaA_boxes': {},
        }

        if self.calculate_superhelical_densities:
            # Get attributes of existing segments
            (boundary_molecule_indexes, boundary_coordinates,
                segment_domain_indexes, linking_numbers) = attrs(
                states['chromosomal_segments'],
                ['boundary_molecule_indexes', 'boundary_coordinates',
                'domain_index', 'linking_number'])

            # Initialize new attributes of chromosomal segments
            all_new_boundary_molecule_indexes = np.empty(
                (0, 2), dtype=np.int64)
            all_new_boundary_coordinates = np.empty((0, 2), dtype=np.int64)
            all_new_segment_domain_indexes = np.array([], dtype=np.int32)
            all_new_linking_numbers = np.array([], dtype=np.float64)

            for domain_index in np.unique(all_chromosome_domain_indexes):
                # Skip domains that have completed replication
                if np.all(domain_index < mother_domain_indexes):
                    continue

                domain_spans_oriC = (domain_index in origin_domain_indexes)
                domain_spans_terC = (domain_index in mother_domain_indexes)

                # Get masks for segments and RNAPs in this domain
                segments_domain_mask = (
                    segment_domain_indexes == domain_index)
                RNAP_domain_mask = (
                    remaining_RNAP_domain_indexes == domain_index)

                # Parse attributes of segments in this domain
                boundary_molecule_indexes_this_domain = \
                    boundary_molecule_indexes[segments_domain_mask, :]
                boundary_coordinates_this_domain = \
                    boundary_coordinates[segments_domain_mask, :]
                linking_numbers_this_domain = \
                    linking_numbers[segments_domain_mask]

                # Parse attributes of remaining RNAPs in this domain
                new_molecule_coordinates_this_domain = \
                    remaining_RNAP_coordinates[RNAP_domain_mask]
                new_molecule_indexes_this_domain = \
                    remaining_RNAP_unique_indexes[RNAP_domain_mask]

                # Append coordinates and indexes of replisomes on this domain,
                # if any
                if not domain_spans_oriC:
                    replisome_domain_mask = (
                        replisome_domain_indexes == domain_index)

                    new_molecule_coordinates_this_domain = np.concatenate((
                        new_molecule_coordinates_this_domain,
                        replisome_coordinates[replisome_domain_mask]
                        ))
                    new_molecule_indexes_this_domain = np.concatenate((
                        new_molecule_indexes_this_domain,
                        replisome_unique_indexes[replisome_domain_mask]
                        ))

                # Append coordinates and indexes of parent domain replisomes,
                # if any
                if not domain_spans_terC:
                    parent_domain_index = all_chromosome_domain_indexes[
                        np.where(child_domains == domain_index)[0][0]]
                    replisome_parent_domain_mask = (
                        replisome_domain_indexes == parent_domain_index)

                    new_molecule_coordinates_this_domain = np.concatenate((
                        new_molecule_coordinates_this_domain,
                        replisome_coordinates[replisome_parent_domain_mask]
                        ))
                    new_molecule_indexes_this_domain = np.concatenate((
                        new_molecule_indexes_this_domain,
                        replisome_unique_indexes[replisome_parent_domain_mask]
                        ))

                # If there are no molecules left on this domain, continue
                if len(new_molecule_indexes_this_domain) == 0:
                    continue

                # Calculate attributes of new segments
                new_segment_attrs = self._compute_new_segment_attributes(
                    boundary_molecule_indexes_this_domain,
                    boundary_coordinates_this_domain,
                    linking_numbers_this_domain,
                    new_molecule_indexes_this_domain,
                    new_molecule_coordinates_this_domain,
                    domain_spans_oriC, domain_spans_terC
                    )

                # Append to existing array of new segment attributes
                all_new_boundary_molecule_indexes = np.vstack((
                    all_new_boundary_molecule_indexes,
                    new_segment_attrs["boundary_molecule_indexes"]))
                all_new_boundary_coordinates = np.vstack((
                    all_new_boundary_coordinates,
                    new_segment_attrs["boundary_coordinates"]))
                all_new_segment_domain_indexes = np.concatenate((
                    all_new_segment_domain_indexes,
                    np.full(len(new_segment_attrs["linking_numbers"]),
                        domain_index, dtype=np.int32)))
                all_new_linking_numbers = np.concatenate((
                    all_new_linking_numbers,
                    new_segment_attrs["linking_numbers"]))

            # Delete all existing chromosomal segments
            if len(boundary_molecule_indexes) > 0:
                update['chromosomal_segments'].update({
                    'delete': np.arange(len(boundary_molecule_indexes))})

            # Add new chromosomal segments
            n_segments = len(all_new_linking_numbers)

            if 'chromosomal_segments' in states and states[
                'chromosomal_segments']:
                self.chromosome_segment_index = int(max([int(index) for index
                    in list(states['chromosomal_segments'].keys())])) + 1

            update['chromosomal_segments'].update({'add': {
                'unique_index': np.arange(
                    self.chromosome_segment_index,
                    self.chromosome_segment_index + n_segments),
                'boundary_molecule_indexes': all_new_boundary_molecule_indexes,
                'boundary_coordinates': all_new_boundary_coordinates,
                'domain_index': all_new_segment_domain_indexes,
                'linking_number': all_new_linking_numbers}})

        # Get mask for RNAs that are transcribed from removed RNAPs
        removed_RNAs_mask = np.isin(
            RNA_RNAP_indexes, RNAP_unique_indexes[removed_RNAPs_mask])

        # Remove RNAPs and RNAs that have collided with replisomes
        if n_total_collisions > 0:
            if removed_RNAPs_mask.sum() > 0:
                update['active_RNAPs'].update({
                    'delete': np.where(removed_RNAPs_mask)[0]})
            if removed_RNAs_mask.sum() > 0:
                update['RNAs'].update({'delete': np.where(
                    removed_RNAs_mask)[0]})

            # Increment counts of inactive RNAPs
            update['bulk'].append((
                self.inactive_RNAPs_idx, n_total_collisions))

            # Get sequences of incomplete transcripts
            incomplete_sequence_lengths = transcript_lengths[
                removed_RNAs_mask]
            n_initiated_sequences = np.count_nonzero(
                incomplete_sequence_lengths)
            n_ppi_added = n_initiated_sequences

            if n_initiated_sequences > 0:
                incomplete_rna_indexes = RNA_TU_indexes[removed_RNAs_mask]

                incomplete_sequences = buildSequences(
                    self.rna_sequences,
                    incomplete_rna_indexes,
                    np.zeros(n_total_collisions, dtype=np.int64),
                    np.full(n_total_collisions,
                        incomplete_sequence_lengths.max()))

                mature_rna_counts = np.zeros(self.n_mature_rnas, dtype=np.int64)
                base_counts = np.zeros(self.n_fragment_bases, dtype=np.int64)

                for ri, sl, seq in zip(incomplete_rna_indexes,
                    incomplete_sequence_lengths, incomplete_sequences
                ):
                    # Check if incomplete RNA is an unprocessed RNA
                    if ri in self.unprocessed_rna_index_mapping:
                        # Find mature RNA molecules that would need to be added
                        # given the length of the incomplete RNA
                        mature_rna_end_pos = self.mature_rna_end_positions[
                            :, self.unprocessed_rna_index_mapping[ri]]
                        mature_rnas_produced = np.logical_and(
                            mature_rna_end_pos != 0, mature_rna_end_pos < sl)

                        # Increment counts of mature RNAs
                        mature_rna_counts += mature_rnas_produced

                        # Increment counts of fragment NTPs, but exclude bases
                        # that are part of the mature RNAs generated
                        base_counts += (
                            np.bincount(seq[:sl], minlength=self.n_fragment_bases)
                            - self.mature_rna_nt_counts[
                                mature_rnas_produced, :].sum(axis=0))

                        # Exclude ppi molecules that are part of mature RNAs
                        n_ppi_added -= mature_rnas_produced.sum()
                    else:
                        base_counts += np.bincount(
                            seq[:sl], minlength=self.n_fragment_bases)
                    base_counts += np.bincount(seq[:sl],
                        minlength=self.n_fragment_bases)

                # Increment counts of mature RNAs, fragment NTPs and phosphates
                update['bulk'].append((self.mature_rna_idx, mature_rna_counts))
                update['bulk'].append((self.fragmentBasesIdx, base_counts))
                update['bulk'].append((self.ppi_idx, n_ppi_added))

        # Get mask for ribosomes that are bound to nonexisting mRNAs
        remaining_RNA_unique_indexes = RNA_unique_indexes[
            np.logical_not(removed_RNAs_mask)]
        removed_ribosomes_mask = np.logical_not(np.isin(
            ribosome_mRNA_indexes, remaining_RNA_unique_indexes))
        n_removed_ribosomes = np.count_nonzero(removed_ribosomes_mask)

        # Remove ribosomes that are bound to missing RNA molecules. This
        # includes both RNAs removed by this function and RNAs removed
        # by other processes (e.g. RNA degradation).
        if n_removed_ribosomes > 0:
            update['active_ribosome'].update({
                'delete': np.where(removed_ribosomes_mask)[0]})

            # Increment counts of inactive ribosomal subunits
            update['bulk'].extend([
                (self.ribosome_30S_subunit_idx, n_removed_ribosomes),
                (self.ribosome_50S_subunit_idx, n_removed_ribosomes)])

            # Get amino acid sequences of incomplete polypeptides
            incomplete_sequence_lengths = ribosome_peptide_lengths[
                removed_ribosomes_mask]
            n_initiated_sequences = np.count_nonzero(
                incomplete_sequence_lengths)

            if n_initiated_sequences > 0:
                incomplete_sequences = buildSequences(
                    self.protein_sequences,
                    ribosome_protein_indexes[removed_ribosomes_mask],
                    np.zeros(n_removed_ribosomes, dtype=np.int64),
                    np.full(n_removed_ribosomes,
                        incomplete_sequence_lengths.max()))

                amino_acid_counts = np.zeros(
                    self.n_amino_acids, dtype=np.int64)

                for sl, seq in zip(incomplete_sequence_lengths,
                    incomplete_sequences
                ):
                    amino_acid_counts += np.bincount(
                        seq[:sl], minlength=self.n_amino_acids)

                # Increment counts of free amino acids and decrease counts of
                # free water molecules
                update['bulk'].append((self.amino_acids_idx,
                    amino_acid_counts))
                update['bulk'].append((self.water_idx, (n_initiated_sequences
                    - incomplete_sequence_lengths.sum())))

        # Write to listener
        update['listeners']['rnap_data'][
            'n_removed_ribosomes'] = n_removed_ribosomes


        def get_replicated_motif_attributes(old_coordinates, old_domain_indexes):
            """
            Computes the attributes of replicated motifs on the chromosome,
            given the old coordinates and domain indexes of the original motifs.
            """
            # Coordinates are simply repeated
            new_coordinates = np.repeat(old_coordinates, 2)

            # Domain indexes are set to the child indexes of the original index
            new_domain_indexes = child_domains[
                np.array([np.where(all_chromosome_domain_indexes == idx)[0][0]
                    for idx in old_domain_indexes]),
                :].flatten()

            return new_coordinates, new_domain_indexes


        #######################
        # Replicate promoters #
        #######################
        n_new_promoters = 2*np.count_nonzero(removed_promoters_mask)

        if n_new_promoters > 0:
            # Delete original promoters
            update['promoters'].update({
                'delete': np.where(removed_promoters_mask)[0]})

            # Add freed active tfs
            update['bulk'].append((self.active_tfs_idx,
                promoter_bound_TFs[removed_promoters_mask, :].sum(axis=0)))

            # Set up attributes for the replicated promoters
            promoter_TU_indexes_new = np.repeat(
                promoter_TU_indexes[removed_promoters_mask], 2)
            (promoter_coordinates_new,
                promoter_domain_indexes_new) = get_replicated_motif_attributes(
                    promoter_coordinates[removed_promoters_mask],
                    promoter_domain_indexes[removed_promoters_mask])

            # Add new promoters with new domain indexes
            promoter_indices = create_unique_indexes(
                n_new_promoters, self.random_state)
            update['promoters'].update({'add': {
                'unique_index': promoter_indices,
                'TU_index': promoter_TU_indexes_new,
                'coordinates': promoter_coordinates_new,
                'domain_index': promoter_domain_indexes_new,
                'bound_TF': np.zeros((n_new_promoters, self.n_TFs),
                    dtype=np.bool_)}})
            
        # Replicate genes
        n_new_genes = 2 * np.count_nonzero(removed_genes_mask)

        if n_new_genes > 0:
            # Delete original genes
            update['genes'].update({
                'delete': np.where(removed_genes_mask)[0]})

            # Set up attributes for the replicated genes
            gene_cistron_indexes_new = np.repeat(gene_cistron_indexes[removed_genes_mask], 2)
            gene_coordinates_new, gene_domain_indexes_new = get_replicated_motif_attributes(
                gene_coordinates[removed_genes_mask],
                gene_domain_indexes[removed_genes_mask])

            # Add new genes with new domain indexes
            gene_indices = create_unique_indexes(
                n_new_genes, self.random_state)
            update['genes'].update({'add': {
                'unique_index': gene_indices,
                'cistron_index': gene_cistron_indexes_new,
                'coordinates': gene_coordinates_new,
                'domain_index': gene_domain_indexes_new}})

        ########################
        # Replicate DnaA boxes #
        ########################
        n_new_DnaA_boxes = 2*np.count_nonzero(removed_DnaA_boxes_mask)

        if n_new_DnaA_boxes > 0:
            # Delete original DnaA boxes
            if removed_DnaA_boxes_mask.sum() > 0:
                update['DnaA_boxes'].update({
                    'delete': np.where(removed_DnaA_boxes_mask)[0]})

            # Set up attributes for the replicated boxes
            (DnaA_box_coordinates_new,
                DnaA_box_domain_indexes_new) = get_replicated_motif_attributes(
                    DnaA_box_coordinates[removed_DnaA_boxes_mask],
                    DnaA_box_domain_indexes[removed_DnaA_boxes_mask])

            # Add new DnaA boxes with new domain indexes
            DnaA_box_indices = create_unique_indexes(
                n_new_DnaA_boxes, self.random_state)
            dict_dna = {'add': {'unique_index': DnaA_box_indices,
                                'coordinates': DnaA_box_coordinates_new,
                                'domain_index': DnaA_box_domain_indexes_new,
                                'DnaA_bound': np.zeros(n_new_DnaA_boxes,
                                    dtype=np.bool_)}}
            update['DnaA_boxes'].update(dict_dna)

        update['next_update_time'] = states['global_time'] + states['timestep']
        return update


    def _compute_new_segment_attributes(self, 
        old_boundary_molecule_indexes: np.ndarray[int], 
        old_boundary_coordinates: np.ndarray[int], 
        old_linking_numbers: np.ndarray[int], new_molecule_indexes: np.ndarray[int], 
        new_molecule_coordinates: np.ndarray[int], spans_oriC: bool, 
        spans_terC: bool) -> dict[str, np.ndarray[int]]:
        """
        Calculates the updated attributes of chromosomal segments belonging to
        a specific chromosomal domain, given the previous and current
        coordinates of molecules bound to the chromosome.
        
        Args:
            old_boundary_molecule_indexes: (N, 2) array of unique
                indexes of molecules that formed the boundaries of each
                chromosomal segment in the previous timestep.
            old_boundary_coordinates: (N, 2) array of chromosomal
                coordinates of molecules that formed the boundaries of each
                chromosomal segment in the previous timestep.
            old_linking_numbers: (N,) array of linking numbers of each
                chromosomal segment in the previous timestep.
            new_molecule_indexes: (N,) array of unique indexes of all
                molecules bound to the domain at the current timestep.
            new_molecule_coordinates: (N,) array of chromosomal
                coordinates of all molecules bound to the domain at the current
                timestep.
            spans_oriC: True if the domain spans the origin.
            spans_terC: True if the domain spans the terminus.
        
        Returns:
            Dictionary of the following format::
                
                {
                    'boundary_molecule_indexes': (M, 2) array of unique
                        indexes of molecules that form the boundaries of new
                        chromosomal segments,
                    'boundary_coordinates': (M, 2) array of chromosomal 
                        coordinates of molecules that form the boundaries of 
                        new chromosomal segments,
                    'linking_numbers': (M,) array of linking numbers of new 
                        chromosomal segments
                }

        """
        # Sort old segment arrays by coordinates of left boundary
        old_coordinates_argsort = np.argsort(old_boundary_coordinates[:, 0])
        old_boundary_coordinates_sorted = old_boundary_coordinates[
            old_coordinates_argsort, :]
        old_boundary_molecule_indexes_sorted = old_boundary_molecule_indexes[
            old_coordinates_argsort, :]
        old_linking_numbers_sorted = old_linking_numbers[
            old_coordinates_argsort]

        # Sort new segment arrays by molecular coordinates
        new_coordinates_argsort = np.argsort(new_molecule_coordinates)
        new_molecule_coordinates_sorted = new_molecule_coordinates[
            new_coordinates_argsort]
        new_molecule_indexes_sorted = new_molecule_indexes[
            new_coordinates_argsort]

        # Domain does not span the origin
        if not spans_oriC:
            # A fragment spans oriC if two boundaries have opposite signs,
            # or both are equal to zero
            oriC_fragment_counts = np.count_nonzero(
                np.logical_not(np.logical_xor(
                    old_boundary_coordinates_sorted[:, 0] < 0,
                    old_boundary_coordinates_sorted[:, 1] > 0
                    )))

            # if oriC fragment did not exist in the domain in the previous
            # timestep, add a dummy fragment that covers the origin with
            # linking number zero. This is done to generalize the
            # implementation of this method.
            if oriC_fragment_counts == 0:
                # Index of first segment where left boundary is nonnegative
                oriC_fragment_index = np.argmax(
                    old_boundary_coordinates_sorted[:, 0] >= 0)

                # Get indexes of boundary molecules for this dummy segment
                oriC_fragment_boundary_molecule_indexes = np.array([
                    old_boundary_molecule_indexes_sorted[
                        oriC_fragment_index - 1, 1],
                    old_boundary_molecule_indexes_sorted[
                        oriC_fragment_index, 0]
                    ])

                # Insert dummy segment to array
                old_boundary_molecule_indexes_sorted = np.insert(
                    old_boundary_molecule_indexes_sorted,
                    oriC_fragment_index,
                    oriC_fragment_boundary_molecule_indexes,
                    axis=0)
                old_linking_numbers_sorted = np.insert(
                    old_linking_numbers_sorted,
                    oriC_fragment_index, 0)
            else:
                # There should not be more than one fragment that spans oriC
                assert oriC_fragment_counts == 1

        # Domain spans the terminus
        if spans_terC:
            # If the domain spans the terminus, dummy molecules are added to
            # each end of the chromosome s.t. the segment that spans terC is
            # split to two segments and we can maintain a linear representation
            # for the circular chromosome. These two segments are later
            # adjusted to have the same superhelical densities.
            new_molecule_coordinates_sorted = np.insert(
                new_molecule_coordinates_sorted,
                [0, len(new_molecule_coordinates_sorted)],
                [self.min_coordinates, self.max_coordinates]
                )

            new_molecule_indexes_sorted = np.insert(
                new_molecule_indexes_sorted,
                [0, len(new_molecule_indexes_sorted)], self.terC_index
                )

            # Add dummy molecule to old segments if they do not already exist
            if old_boundary_molecule_indexes_sorted[0, 0] != self.terC_index:
                old_boundary_molecule_indexes_sorted = np.vstack((
                    np.array([self.terC_index, 
                        old_boundary_molecule_indexes_sorted[0, 0]]),
                    old_boundary_molecule_indexes_sorted,
                    np.array([old_boundary_molecule_indexes_sorted[-1, 1],
                        self.terC_index])
                    ))
                old_linking_numbers_sorted = np.insert(
                    old_linking_numbers_sorted,
                    [0, len(old_linking_numbers_sorted)], 0
                    )

        # Recalculate linking numbers of each segment after accounting for
        # boundary molecules that were removed in the current timestep
        linking_numbers_after_removal = []
        right_boundaries_retained = np.isin(
            old_boundary_molecule_indexes_sorted[:, 1],
            new_molecule_indexes_sorted)

        # Add up linking numbers of each segment until each retained boundary
        ln_this_fragment = 0.
        for retained, ln in zip(right_boundaries_retained,
            old_linking_numbers_sorted
        ):
            ln_this_fragment += ln

            if retained:
                linking_numbers_after_removal.append(ln_this_fragment)
                ln_this_fragment = 0.

        # Number of segments should be equal to number of retained boundaries
        assert len(linking_numbers_after_removal) == \
            right_boundaries_retained.sum()

        # Redistribute linking numbers of the two terC segments such that the
        # segments have same superhelical densities
        if spans_terC and np.count_nonzero(right_boundaries_retained) > 1:
            # Get molecule indexes of the boundaries of the two terC segments
            # left and right of terC
            retained_boundary_indexes = np.where(right_boundaries_retained)[0]
            left_segment_boundary_index = old_boundary_molecule_indexes_sorted[
                retained_boundary_indexes[0], 1]
            right_segment_boundary_index = old_boundary_molecule_indexes_sorted[
                retained_boundary_indexes[-2], 1]

            # Get mapping from molecule index to chromosomal coordinates
            molecule_index_to_coordinates = {
                index: coordinates for index, coordinates
                in zip(new_molecule_indexes_sorted,
                new_molecule_coordinates_sorted)
            }

            # Distribute linking number between two segments proportional to
            # the length of each segment
            left_segment_length = molecule_index_to_coordinates[
                left_segment_boundary_index] - self.min_coordinates
            right_segment_length = self.max_coordinates \
                - molecule_index_to_coordinates[right_segment_boundary_index]
            full_segment_length = left_segment_length + right_segment_length
            full_linking_number = linking_numbers_after_removal[0] \
                + linking_numbers_after_removal[-1]

            linking_numbers_after_removal[0] = full_linking_number \
                * left_segment_length/full_segment_length
            linking_numbers_after_removal[-1] = full_linking_number \
                * right_segment_length/full_segment_length

        # Get mask for molecules that already existed in the previous timestep
        existing_molecules_mask = np.isin(
            new_molecule_indexes_sorted, old_boundary_molecule_indexes_sorted)

        # Get numbers and lengths of new segments that each segment will be
        # split into
        segment_split_sizes = np.diff(np.where(existing_molecules_mask)[0])
        segment_lengths = np.diff(new_molecule_coordinates_sorted)

        assert len(segment_split_sizes) == len(linking_numbers_after_removal)

        # Calculate linking numbers of each segment after accounting for new
        # boundaries that were added
        new_linking_numbers = []
        i = 0

        for ln, size in zip(linking_numbers_after_removal,
            segment_split_sizes
        ):
            if size == 1:
                new_linking_numbers.append(ln)
            else:
                # Split linking number proportional to length of segment
                total_length = segment_lengths[i:i + size].sum()
                new_linking_numbers.extend(
                    list(ln*segment_lengths[i:i + size]/total_length)
                    )
            i += size

        # Handle edge case where a domain was just initialized, and two
        # replisomes are bound to the origin
        if len(new_linking_numbers) == 0:
            new_linking_numbers = [0]

        # Build Mx2 array for boundary indexes and coordinates
        new_boundary_molecule_indexes = np.hstack((
            new_molecule_indexes_sorted[:-1, np.newaxis],
            new_molecule_indexes_sorted[1:, np.newaxis]
            ))
        new_boundary_coordinates = np.hstack((
            new_molecule_coordinates_sorted[:-1, np.newaxis],
            new_molecule_coordinates_sorted[1:, np.newaxis]
            ))
        new_linking_numbers = np.array(new_linking_numbers)

        # If domain does not span oriC, remove new segment that spans origin
        if not spans_oriC:
            oriC_fragment_mask = np.logical_not(np.logical_xor(
                new_boundary_coordinates[:, 0] < 0,
                new_boundary_coordinates[:, 1] > 0
                ))

            assert oriC_fragment_mask.sum() == 1

            new_boundary_molecule_indexes = new_boundary_molecule_indexes[
                np.logical_not(oriC_fragment_mask), :]
            new_boundary_coordinates = new_boundary_coordinates[
                np.logical_not(oriC_fragment_mask), :]
            new_linking_numbers = new_linking_numbers[
                np.logical_not(oriC_fragment_mask)]

        return {
            "boundary_molecule_indexes": new_boundary_molecule_indexes,
            "boundary_coordinates": new_boundary_coordinates,
            "linking_numbers": new_linking_numbers
            }
