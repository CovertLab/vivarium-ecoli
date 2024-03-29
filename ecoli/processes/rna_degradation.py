"""
===============
RNA Degradation
===============

Mathematical formulations

* ``dr/dt = Kb - kcatEndoRNase * EndoRNase * r/Km / (1 + Sum(r/Km))``

where

* r = RNA counts
* Kb = RNA production given a RNAP synthesis rate
* kcatEndoRNase = enzymatic activity for EndoRNases
* Km = Michaelis-Menten constants fitted to recapitulate first-order
* RNA decay: ``kd * r = kcatEndoRNase * EndoRNase * r/Km / (1 + sum(r/Km))``

This sub-model encodes molecular simulation of RNA degradation as two main
steps guided by RNases, "endonucleolytic cleavage" and "exonucleolytic
digestion":

1. Compute total counts of RNA to be degraded (D) and total capacity for
   endo-cleavage (C) at each time point
2. Evaluate C and D. If C > D, then define a fraction of active endoRNases
3. Dissect RNA degraded into different species (mRNA, tRNA, and rRNA) by
   accounting endoRNases specificity
4. Update RNA fragments (assumption: fragments are represented as a pool of
   nucleotides) created because of endonucleolytic cleavage
5. Compute total capacity of exoRNases and determine fraction of nucleotides
   that can be digested
6. Update pool of metabolites (H and H2O) created because of exonucleolytic
   digestion
"""

import numpy as np

from ecoli.library.schema import (
    bulk_name_to_idx, counts, attrs, numpy_schema, listener_schema)

from wholecell.utils import units

from ecoli.processes.registries import topology_registry
from ecoli.processes.partition import PartitionedProcess


# Register default topology for this process, associating it with process name
NAME = 'ecoli-rna-degradation'
TOPOLOGY = {
    "bulk": ("bulk",),
    "RNAs": ("unique", "RNA"),
    "active_ribosome": ("unique", "active_ribosome"),
    "listeners": ("listeners",),
    "timestep": ("timestep",)
}
topology_registry.register(NAME, TOPOLOGY)


class RnaDegradation(PartitionedProcess):
    """ RNA Degradation PartitionedProcess """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'rna_ids': [],
        'mature_rna_ids': [],
        'cistron_ids': [],
        'cistron_tu_mapping_matrix': [],
        'mature_rna_cistron_indexes': [],
        'all_rna_ids': [],
        'n_total_RNAs': 0,
        'n_avogadro': 0.0,
        'cell_density': 1100 * units.g / units.L,
        'endoRNase_ids': [],
        'exoRNase_ids': [],
        'kcat_exoRNase': np.array([]) / units.s,
        'Kcat_endoRNases': np.array([]) / units.s,
        'charged_trna_names': [],
        'uncharged_trna_indexes': [],
        'rna_deg_rates': [],
        'is_mRNA': np.array([]),
        'is_rRNA': np.array([]),
        'is_tRNA': np.array([]),
        'is_miscRNA': np.array([]),
        'degrade_misc': False,
        'rna_lengths': np.array([]),
        'nt_counts': np.array([[]]),
        'polymerized_ntp_ids': [],
        'water_id': 'h2o',
        'ppi_id': 'ppi',
        'proton_id': 'h+',
        'nmp_ids': [],
        'rrfa_idx': 0,
        'rrla_idx': 0,
        'rrsa_idx': 0,
        'ribosome30S': 'ribosome30S',
        'ribosome50S': 'ribosome50S',
        'Kms': np.array([]) * units.mol / units.L,
        'seed': 0,
        'emit_unique': False,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.rna_ids = self.parameters['rna_ids']
        self.mature_rna_ids = self.parameters['mature_rna_ids']
        self.n_transcribed_rnas = len(self.rna_ids)
        self.mature_rna_exists = (len(self.mature_rna_ids) > 0)
        self.cistron_ids = self.parameters['cistron_ids']
        self.cistron_tu_mapping_matrix = self.parameters[
            'cistron_tu_mapping_matrix']
        self.mature_rna_cistron_indexes = self.parameters[
            'mature_rna_cistron_indexes']
        self.all_rna_ids = self.parameters['all_rna_ids']
        self.n_total_RNAs = self.parameters['n_total_RNAs']

        # Load constants
        self.n_avogadro = self.parameters['n_avogadro']
        self.cell_density = self.parameters['cell_density']

        # Load RNase kinetic data
        self.endoRNase_ids = self.parameters['endoRNase_ids']
        self.exoRNase_ids = self.parameters['exoRNase_ids']
        self.kcat_exoRNase = self.parameters['kcat_exoRNase']
        self.Kcat_endoRNases = self.parameters['Kcat_endoRNases']

        # Load information about uncharged/charged tRNA
        self.uncharged_trna_indexes = self.parameters['uncharged_trna_indexes']
        self.charged_trna_names = self.parameters['charged_trna_names']

        # Load first-order RNA degradation rates
        # (estimated by mRNA half-life data)
        self.rna_deg_rates = self.parameters['rna_deg_rates']

        self.is_mRNA = self.parameters['is_mRNA']
        self.is_rRNA = self.parameters['is_rRNA']
        self.is_tRNA = self.parameters['is_tRNA']
        
        # NEW to vivarium-ecoli
        self.is_miscRNA = self.parameters['is_miscRNA']
        self.degrade_misc = self.parameters['degrade_misc']

        self.rna_lengths = self.parameters['rna_lengths']
        self.nt_counts = self.parameters['nt_counts']

        # Build stoichiometric matrix
        self.polymerized_ntp_ids = self.parameters['polymerized_ntp_ids']
        self.nmp_ids = self.parameters['nmp_ids']
        self.water_id = self.parameters['water_id']
        self.ppi_id = self.parameters['ppi_id']
        self.proton_id = self.parameters['proton_id']

        self.end_cleavage_metabolite_ids = self.polymerized_ntp_ids + [
            self.water_id, self.ppi_id, self.proton_id]
        nmp_idx = list(range(4))
        water_idx = self.end_cleavage_metabolite_ids.index(self.water_id)
        ppi_idx = self.end_cleavage_metabolite_ids.index(self.ppi_id)
        proton_idx = self.end_cleavage_metabolite_ids.index(self.proton_id)
        self.endo_degradation_stoich_matrix = np.zeros(
            (len(self.end_cleavage_metabolite_ids), self.n_total_RNAs), np.int64)
        self.endo_degradation_stoich_matrix[nmp_idx, :] = self.nt_counts.T
        self.endo_degradation_stoich_matrix[water_idx, :] = 0
        self.endo_degradation_stoich_matrix[ppi_idx, :] = 1
        self.endo_degradation_stoich_matrix[proton_idx, :] = 0

        self.ribosome30S = self.parameters['ribosome30S']
        self.ribosome50S = self.parameters['ribosome50S']
        self.rrfa_idx = self.parameters['rrfa_idx']
        self.rrla_idx = self.parameters['rrla_idx']
        self.rrsa_idx = self.parameters['rrsa_idx']

        # Load Michaelis-Menten constants fitted to recapitulate
        # first-order RNA decay model
        self.Kms = self.parameters['Kms']

        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed = self.seed)

        # Numpy indices for bulk molecules
        self.water_idx = None

    def ports_schema(self):
        return {
            'bulk': numpy_schema('bulk'),
            'active_ribosome': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),
            'RNAs': numpy_schema('RNAs',
                emit=self.parameters['emit_unique']),
            'listeners': {
                'mass': listener_schema({
                    'cell_mass': 0.0,
                    'dry_mass': 0.0}),
                'rna_degradation_listener': listener_schema({
                    'fraction_active_endornases': 0.,
                    'diff_relative_first_order_decay': 0.,
                    'fract_endo_rrna_counts': 0.,
                    'count_rna_degraded': ([0] * len(self.all_rna_ids),
                        self.all_rna_ids),
                    'count_RNA_degraded_per_cistron': (
                        [0] * len(self.cistron_ids),
                        self.cistron_ids),
                    'nucleotides_from_degradation': 0,
                    'fragment_bases_digested': 0,
                }),
            },
            'timestep': {'_default': self.parameters['time_step']}
        }

    def calculate_request(self, timestep, states):
        if self.water_idx is None:
            bulk_ids = states['bulk']['id']
            self.charged_trna_idx = bulk_name_to_idx(
                self.charged_trna_names, bulk_ids)
            self.bulk_rnas_idx = bulk_name_to_idx(self.all_rna_ids, bulk_ids)
            self.nmps_idx = bulk_name_to_idx(self.nmp_ids, bulk_ids)
            self.fragment_metabolites_idx = bulk_name_to_idx(
                self.end_cleavage_metabolite_ids, bulk_ids)
            self.fragment_bases_idx = bulk_name_to_idx(
                self.polymerized_ntp_ids, bulk_ids)
            self.endoRNase_idx = bulk_name_to_idx(self.endoRNase_ids, bulk_ids)
            self.exoRNase_idx = bulk_name_to_idx(self.exoRNase_ids, bulk_ids)
            self.ribosome30S_idx = bulk_name_to_idx(self.ribosome30S, bulk_ids)
            self.ribosome50S_idx = bulk_name_to_idx(self.ribosome50S, bulk_ids)
            self.water_idx = bulk_name_to_idx(self.water_id, bulk_ids)
            self.proton_idx = bulk_name_to_idx(self.proton_id, bulk_ids)

        # Compute factor that convert counts into concentration, and vice versa
        cell_mass = states['listeners']['mass']['cell_mass'] * units.fg
        cell_volume = cell_mass / self.cell_density
        counts_to_molar = 1 / (self.n_avogadro * cell_volume)

        # Get total counts of RNAs including rRNAs, charged tRNAs, and active
        # (translatable) unique mRNAs
        bulk_RNA_counts = counts(states['bulk'], self.bulk_rnas_idx)
        bulk_RNA_counts[self.rrsa_idx] += counts(
            states['bulk'], self.ribosome30S_idx)
        bulk_RNA_counts[[self.rrla_idx, self.rrfa_idx]] += counts(
            states['bulk'], self.ribosome50S_idx)
        bulk_RNA_counts[[self.rrla_idx, self.rrfa_idx, self.rrsa_idx]] \
            += states['active_ribosome']['_entryState'].sum()
        bulk_RNA_counts[self.uncharged_trna_indexes] \
            += counts(states['bulk'], self.charged_trna_idx)

        TU_index, can_translate, is_full_transcript = attrs(
            states['RNAs'],
            ['TU_index', 'can_translate', 'is_full_transcript'])

        TU_index_translatable_mRNAs = TU_index[can_translate]
        unique_RNA_counts = np.bincount(
            TU_index_translatable_mRNAs, minlength=self.n_total_RNAs)
        total_RNA_counts = bulk_RNA_counts + unique_RNA_counts

        # Compute RNA concentrations
        rna_conc_molar = counts_to_molar * total_RNA_counts

        # Get counts of endoRNases
        endoRNase_counts = counts(states['bulk'], self.endoRNase_idx)
        total_kcat_endoRNase = units.dot(self.Kcat_endoRNases, endoRNase_counts)

        # Calculate the fraction of active endoRNases for each RNA based on
        # Michaelis-Menten kinetics
        frac_endoRNase_saturated = (
            rna_conc_molar / self.Kms / (
                1 + units.sum(rna_conc_molar / self.Kms))
        ).asNumber()

        # Calculate difference in degradation rates from first-order decay
        # and the number of EndoRNases per one molecule of RNA
        total_endoRNase_counts = np.sum(endoRNase_counts)
        diff_relative_first_order_decay = units.sum(
            units.abs(self.rna_deg_rates * total_RNA_counts -
                total_kcat_endoRNase * frac_endoRNase_saturated)
            )
        endoRNase_per_rna = total_endoRNase_counts / np.sum(total_RNA_counts)

        requests = {'listeners': {'rna_degradation_listener' : {}}}
        requests['listeners']['rna_degradation_listener'][
            "fraction_active_endoRNases"] = np.sum(frac_endoRNase_saturated)
        requests['listeners']['rna_degradation_listener'][
            "diff_relative_first_order_decay"] = \
                diff_relative_first_order_decay.asNumber()
        requests['listeners']['rna_degradation_listener'][
            "fract_endo_rrna_counts"] = endoRNase_per_rna
        
        # Dissect RNAse specificity into mRNA, tRNA, and rRNA
        # NEW to vivarium-ecoli: Degrade miscRNAs and mRNAs together
        if self.degrade_misc:
            is_transient_rna = (self.is_mRNA | self.is_miscRNA)
            mrna_specificity = np.dot(frac_endoRNase_saturated,
                is_transient_rna)
        else:
            mrna_specificity = np.dot(frac_endoRNase_saturated, self.is_mRNA)
        trna_specificity = np.dot(frac_endoRNase_saturated, self.is_tRNA)
        rrna_specificity = np.dot(frac_endoRNase_saturated, self.is_rRNA)

        n_total_mrnas_to_degrade = self._calculate_total_n_to_degrade(
            states['timestep'], mrna_specificity, total_kcat_endoRNase)
        n_total_trnas_to_degrade = self._calculate_total_n_to_degrade(
            states['timestep'], trna_specificity, total_kcat_endoRNase)
        n_total_rrnas_to_degrade = self._calculate_total_n_to_degrade(
            states['timestep'], rrna_specificity, total_kcat_endoRNase)

        # Compute RNAse specificity
        rna_specificity = frac_endoRNase_saturated / np.sum(
            frac_endoRNase_saturated)

        # Boolean variable that tracks existence of each RNA
        rna_exists = (total_RNA_counts > 0).astype(np.int64)

        # Compute degradation probabilities of each RNA: for mRNAs, this
        # is based on the specificity of each mRNA. For tRNAs and rRNAs,
        # this is distributed evenly.
        if self.degrade_misc:
            mrna_deg_probs = (1. / np.dot(rna_specificity, 
                is_transient_rna * rna_exists) * rna_specificity *
                is_transient_rna * rna_exists)
        else:
            mrna_deg_probs = (1. / np.dot(rna_specificity, 
                self.is_mRNA * rna_exists) * rna_specificity *
                self.is_mRNA * rna_exists)
        trna_deg_probs = 1. / np.dot(self.is_tRNA, rna_exists
            ) * self.is_tRNA * rna_exists
        rrna_deg_probs = 1. / np.dot(self.is_rRNA, rna_exists
            ) * self.is_rRNA * rna_exists

        # Mask RNA counts into each class of RNAs
        if self.degrade_misc:
            mrna_counts = total_RNA_counts * is_transient_rna
        else:
            mrna_counts = total_RNA_counts * self.is_mRNA
        trna_counts = total_RNA_counts * self.is_tRNA
        rrna_counts = total_RNA_counts * self.is_rRNA

        # Determine number of individual RNAs to be degraded for each class
        # of RNA.
        n_mrnas_to_degrade = self._get_rnas_to_degrade(
            n_total_mrnas_to_degrade, mrna_deg_probs, mrna_counts)
        n_trnas_to_degrade = self._get_rnas_to_degrade(
            n_total_trnas_to_degrade, trna_deg_probs, trna_counts)
        n_rrnas_to_degrade = self._get_rnas_to_degrade(
            n_total_rrnas_to_degrade, rrna_deg_probs, rrna_counts)
        n_RNAs_to_degrade = n_mrnas_to_degrade + n_trnas_to_degrade + n_rrnas_to_degrade

        # Bulk RNAs (tRNAs and rRNAs) are degraded immediately. Unique RNAs
        # (mRNAs) are immediately deactivated (becomes unable to bind
        # ribosomes), but not degraded until transcription is finished and the
        # mRNA becomes a full transcript to simplify the transcript elongation
        # process.
        n_bulk_RNAs_to_degrade = n_RNAs_to_degrade.copy()
        n_bulk_RNAs_to_degrade[self.is_mRNA] = 0
        self.n_unique_RNAs_to_deactivate = n_RNAs_to_degrade.copy()
        self.n_unique_RNAs_to_deactivate[np.logical_not(self.is_mRNA)] = 0

        requests.setdefault('bulk', []).extend([
            (self.bulk_rnas_idx, n_bulk_RNAs_to_degrade),
            (self.endoRNase_idx, counts(states['bulk'], self.endoRNase_idx)),
            (self.exoRNase_idx, counts(states['bulk'], self.exoRNase_idx)),
            (self.fragment_bases_idx, counts(
                states['bulk'], self.fragment_bases_idx))
        ])

        # Calculate the amount of water required for total RNA hydrolysis by
        # endo and exonucleases. We first calculate the number of unique RNAs
        # that should be degraded at this timestep.
        self.unique_mRNAs_to_degrade = np.logical_and(
            np.logical_not(can_translate), is_full_transcript)
        self.n_unique_RNAs_to_degrade = np.bincount(
            TU_index[self.unique_mRNAs_to_degrade],
            minlength=self.n_total_RNAs)

        # Assuming complete hydrolysis for now. Note that one additional water
        # molecule is needed for each RNA to hydrolyze the 5' diphosphate.
        water_for_degraded_rnas = np.dot(
            n_bulk_RNAs_to_degrade + self.n_unique_RNAs_to_degrade,
            self.rna_lengths)
        water_for_fragments = counts(states['bulk'],
            self.fragment_bases_idx).sum()
        requests['bulk'].append((self.water_idx,
            water_for_degraded_rnas + water_for_fragments))
        return requests

    def evolve_state(self, timestep, states):
        # Get vector of numbers of RNAs to degrade for each RNA species
        n_degraded_bulk_RNA = counts(states['bulk'], self.bulk_rnas_idx)
        n_degraded_unique_RNA = self.n_unique_RNAs_to_degrade
        n_degraded_RNA = n_degraded_bulk_RNA + n_degraded_unique_RNA

        # Deactivate and degrade unique RNAs
        TU_index, can_translate = attrs(
            states['RNAs'],
            ['TU_index', 'can_translate'])
        n_deactivated_unique_RNA = self.n_unique_RNAs_to_deactivate

        # Deactive unique RNAs
        non_zero_deactivation = (n_deactivated_unique_RNA > 0)

        for index, n_degraded in zip(
                np.arange(n_deactivated_unique_RNA.size)[non_zero_deactivation],
                n_deactivated_unique_RNA[non_zero_deactivation]):
            # Get mask for translatable mRNAs belonging to the degraded species
            mask = np.logical_and(TU_index == index, can_translate)

            # Choose n_degraded indexes randomly to deactivate
            can_translate[self.random_state.choice(
                size=n_degraded, a=np.where(mask)[0], replace=False)] = False
            
        count_RNA_degraded_per_cistron = self.cistron_tu_mapping_matrix.dot(
            n_degraded_RNA[:self.n_transcribed_rnas])
        # Add degraded counts from mature RNAs
        if self.mature_rna_exists:
            count_RNA_degraded_per_cistron[self.mature_rna_cistron_indexes
                ] += n_degraded_RNA[self.n_transcribed_rnas:]

        update = {
            'listeners': {
                'rna_degradation_listener': {
                    'count_rna_degraded': n_degraded_RNA,
                    'nucleotides_from_degradation': np.dot(
                        n_degraded_RNA, self.rna_lengths),
                    'count_RNA_degraded_per_cistron': \
                        count_RNA_degraded_per_cistron}},
            # Degrade bulk RNAs
            'bulk': [(self.bulk_rnas_idx, -n_degraded_bulk_RNA)],
            'RNAs': {
                'set': {
                    'can_translate': can_translate
                },
                # Degrade full mRNAs that are inactive
                'delete': np.where(self.unique_mRNAs_to_degrade)[0]
            }
        }

        # Modeling assumption: Once a RNA is cleaved by an endonuclease its
        # resulting nucleotides are lumped together as "polymerized fragments".
        # These fragments can carry over from previous timesteps. We are also
        # assuming that during endonucleolytic cleavage the 5'terminal
        # phosphate is removed. This is modeled as all of the fragments being
        # one long linear chain of "fragment bases".

        # Example:
        # PPi-Base-PO4(-)-Base-PO4(-)-Base-OH
        # ==>
        # Pi-FragmentBase-PO4(-)-FragmentBase-PO4(-)-FragmentBase + PPi
        # Note: Lack of -OH on 3' end of chain
        metabolites_endo_cleavage = np.dot(
            self.endo_degradation_stoich_matrix, n_degraded_RNA)

        # Increase polymerized fragment counts
        update['bulk'].append((self.fragment_metabolites_idx,
            metabolites_endo_cleavage))
        # fragment_metabolites overlaps with fragment_bases
        bulk_count_copy = states['bulk'].copy()
        if len(bulk_count_copy.dtype) > 1:
            bulk_count_copy = bulk_count_copy['count']
        bulk_count_copy[self.fragment_metabolites_idx] \
            += metabolites_endo_cleavage
        fragment_bases = bulk_count_copy[self.fragment_bases_idx]

        # Check if exonucleolytic digestion can happen
        if fragment_bases.sum() == 0:
            return update

        # Calculate exolytic cleavage events

        # Modeling assumption: We model fragments as one long fragment chain of
        # polymerized nucleotides. We are also assuming that there is no
        # sequence specificity or bias towards which nucleotides are
        # hydrolyzed.

        # Example:
        # Pi-FragmentBase-PO4(-)-FragmentBase-PO4(-)-FragmentBase + 3 H2O
        # ==>
        # 3 NMP + 3 H(+)
        # Note: Lack of -OH on 3' end of chain

        n_exoRNases = counts(states['bulk'], self.exoRNase_idx)
        n_fragment_bases = fragment_bases
        n_fragment_bases_sum = n_fragment_bases.sum()

        exornase_capacity = n_exoRNases.sum() * self.kcat_exoRNase * (
                units.s * states['timestep'])

        if exornase_capacity >= n_fragment_bases_sum:
            update['bulk'].extend([
                (self.nmps_idx, n_fragment_bases),
                (self.water_idx, -n_fragment_bases_sum),
                (self.proton_idx, n_fragment_bases_sum),
                (self.fragment_bases_idx, -n_fragment_bases)
            ])
            total_fragment_bases_digested = n_fragment_bases_sum

        else:
            fragment_specificity = n_fragment_bases / n_fragment_bases_sum
            possible_bases_to_digest = self.random_state.multinomial(
                exornase_capacity, fragment_specificity)
            n_fragment_bases_digested = n_fragment_bases - np.fmax(
                n_fragment_bases - possible_bases_to_digest, 0)

            total_fragment_bases_digested = n_fragment_bases_digested.sum()

            update['bulk'].extend([
                (self.nmps_idx, n_fragment_bases_digested),
                (self.water_idx, -total_fragment_bases_digested),
                (self.proton_idx, total_fragment_bases_digested),
                (self.fragment_bases_idx, -n_fragment_bases_digested)
            ])

        update['listeners']['rna_degradation_listener'][
            'fragment_bases_digested'] = total_fragment_bases_digested

        # Note that once mRNAs have been degraded,
        # chromosome_structure.py will handle deleting the active
        # ribosomes that were translating those mRNAs.

        return update


    def _calculate_total_n_to_degrade(
        self, timestep, specificity, total_kcat_endornase):
        """
        Calculate the total number of RNAs to degrade for a specific class of
        RNAs, based on the specificity of endoRNases on that specific class and
        the total kcat value of the endoRNases.

        Args:
            specificity: Sum of fraction of active endoRNases for all RNAs
                in a given class
            total_kcat_endornase: The summed kcat of all existing endoRNases
        Returns:
            Total number of RNAs to degrade for the given class of RNAs
        """
        return np.round(
            (specificity * total_kcat_endornase
             * (units.s * timestep)).asNumber()
            )


    def _get_rnas_to_degrade(self, n_total_rnas_to_degrade, rna_deg_probs,
            rna_counts):
        """
        Distributes the total count of RNAs to degrade for each class of RNAs
        into individual RNAs, based on the given degradation probabilities
        of individual RNAs. The upper bound is set by the current count of the
        specific RNA.

        Args:
            n_total_rnas_to_degrade: Total number of RNAs to degrade for the
                given class of RNAs (integer, scalar)
            rna_deg_probs: Degradation probabilities of each RNA (vector of
                equal length to the total number of different RNAs)
            rna_counts: Current counts of each RNA molecule (vector of equal
                length to the total number of different RNAs)
        Returns:
            Vector of equal length to rna_counts, specifying the number of
            molecules to degrade for each RNA
        """
        n_rnas_to_degrade = np.zeros_like(rna_counts)
        remaining_rna_counts = rna_counts

        while (n_rnas_to_degrade.sum() < n_total_rnas_to_degrade and
            remaining_rna_counts.sum() != 0):
            n_rnas_to_degrade += np.fmin(
                self.random_state.multinomial(
                    n_total_rnas_to_degrade - n_rnas_to_degrade.sum(),
                    rna_deg_probs
                    ),
                remaining_rna_counts
                )
            remaining_rna_counts = rna_counts - n_rnas_to_degrade

        return n_rnas_to_degrade
