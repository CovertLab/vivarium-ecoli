"""
==========
Metabolism
==========

Encodes molecular simulation of microbial metabolism using flux-balance analysis.

This process demonstrates how metabolites are taken up from the environment
and converted into other metabolites for use in other processes.

NOTE:
- In wcEcoli, metabolism only runs after all other processes have completed
and internal states have been updated (deriver-like, no partitioning necessary)
"""

from typing import Any
import warnings

import numpy as np
from scipy.sparse import csr_matrix
from unum import Unum
from vivarium.core.process import Step
from vivarium.library.units import units as vivunits

from ecoli.processes.registries import topology_registry
from ecoli.library.schema import (
    numpy_schema, bulk_name_to_idx, counts, listener_schema)
from wholecell.utils import units
from wholecell.utils.random import stochasticRound
from wholecell.utils.modular_fba import FluxBalanceAnalysis
from reconstruction.ecoli.dataclasses.process.metabolism import REVERSE_TAG


# Register default topology for this process, associating it with process name
NAME = 'ecoli-metabolism'
TOPOLOGY = {
    "bulk": ("bulk",),
    # Non-partitioned counts
    "bulk_total": ("bulk",),
    "listeners": ("listeners",),
    "environment": {
        "_path": ("environment",),
        "exchange": ("exchange",),
    },
    "boundary": ("boundary",),
    "polypeptide_elongation": ("process_state", "polypeptide_elongation"),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", "metabolism"),
    }
topology_registry.register(NAME, TOPOLOGY)

COUNTS_UNITS = units.mmol
VOLUME_UNITS = units.L
MASS_UNITS = units.g
TIME_UNITS = units.s
CONC_UNITS = COUNTS_UNITS / VOLUME_UNITS
CONVERSION_UNITS = MASS_UNITS * TIME_UNITS / VOLUME_UNITS
GDCW_BASIS = units.mmol / units.g / units.h

USE_KINETICS = True


class Metabolism(Step):
    """ Metabolism Process """

    name = NAME
    topology = TOPOLOGY
    defaults = {
        'get_import_constraints': lambda u, c, p: (u, c, []),
        'nutrientToDoublingTime': {},
        'use_trna_charging': False,
        'include_ppgpp': False,
        'mechanistic_aa_transport': False,
        'aa_names': [],
        'aa_targets_not_updated': set(),
        'import_constraint_threshold': 0,
        'exchange_molecules': [],
        'current_timeline': None,
        'media_id': 'minimal',
        'imports': {},
        'metabolism': {},
        'ngam': 8.39 * units.mmol / (units.g * units.h),
        'avogadro': 6.02214076e+23 / units.mol,
        'cell_density': 1100 * units.g / units.L,
        'dark_atp': 33.565052868380675 * units.mmol / units.g,
        'cell_dry_mass_fraction': 0.3,
        'get_biomass_as_concentrations': lambda doubling_time: {},
        'ppgpp_id': 'ppgpp',
        'get_ppGpp_conc': lambda media: 0.0,
        'exchange_data_from_media': lambda media: [],
        'get_masses': lambda exchanges: [],
        'doubling_time': 44.0 * units.min,
        'amino_acid_ids': {},
        'linked_metabolites': None,
        'aa_exchange_names': [],
        'removed_aa_uptake': [],
        'seed': 0,
        # TODO: For testing, remove later (perhaps after modifying sim data)
        'reduce_murein_objective': False,
        'base_reaction_ids': [],
        'fba_reaction_ids_to_base_reaction_ids': [],
        'time_step': 1
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        # Use information from the environment and sim
        self.get_import_constraints = self.parameters['get_import_constraints']
        self.nutrientToDoublingTime = self.parameters['nutrientToDoublingTime']
        self.use_trna_charging = self.parameters['use_trna_charging']
        self.include_ppgpp = self.parameters['include_ppgpp']
        self.mechanistic_aa_transport = self.parameters[
            'mechanistic_aa_transport']
        self.current_timeline = self.parameters['current_timeline']
        self.media_id = self.parameters['media_id']
        self.exchange_molecules = self.parameters[
            'exchange_molecules']
        self.environment_molecules = sorted(
            [mol[:-3] for mol in self.exchange_molecules])

        # Create model to use to solve metabolism updates
        self.model = FluxBalanceAnalysisModel(
            self.parameters,
            timeline=self.current_timeline,
            include_ppgpp=self.include_ppgpp)

        # Save constants
        self.nAvogadro = self.parameters['avogadro']
        self.cellDensity = self.parameters['cell_density']

        # Track updated AA concentration targets with tRNA charging
        self.aa_targets = {}
        self.aa_targets_not_updated = self.parameters['aa_targets_not_updated']
        self.aa_names = self.parameters['aa_names']
        # Comparing two values with units is faster than converting units
        # and comparing magnitudes
        self.import_constraint_threshold = self.parameters[
            'import_constraint_threshold'] * vivunits.mM

        # Molecules with concentration updates for listener
        self.linked_metabolites = self.parameters['linked_metabolites']
        doubling_time = self.nutrientToDoublingTime.get(
            self.media_id,
            self.nutrientToDoublingTime['minimal'])
        update_molecules = list(self.model.getBiomassAsConcentrations(
            doubling_time).keys())
        if self.use_trna_charging:
            update_molecules += [aa for aa in self.aa_names
                if aa not in self.aa_targets_not_updated]
            update_molecules += list(self.linked_metabolites.keys())
        if self.include_ppgpp:
            update_molecules += [self.model.ppgpp_id]
        self.conc_update_molecules = sorted(update_molecules)

        self.aa_exchange_names = self.parameters['aa_exchange_names']
        self.removed_aa_uptake = self.parameters['removed_aa_uptake']
        self.aa_environment_names = [aa[:-3] for aa in self.aa_exchange_names]

        self.seed = self.parameters['seed']
        self.random_state = np.random.RandomState(seed=self.seed)

        # TODO: For testing, remove later (perhaps after modifying sim data)
        self.reduce_murein_objective = self.parameters[
            'reduce_murein_objective']

        # Helper indices for Numpy indexing
        self.metabolite_idx = None

        # Get conversion matrix to compile individual fluxes in the FBA
        # solution to the fluxes of base reactions
        self.fba_reaction_ids = self.model.fba.getReactionIDs()
        self.base_reaction_ids = self.parameters['base_reaction_ids']
        fba_reaction_ids_to_base_reaction_ids = self.parameters[
            'fba_reaction_ids_to_base_reaction_ids']
        self.externalMoleculeIDs = self.model.fba.getExternalMoleculeIDs()
        self.outputMoleculeIDs = self.model.fba.getOutputMoleculeIDs()
        self.kineticTargetFluxNames = self.model.fba.getKineticTargetFluxNames()
        self.homeostaticTargetMolecules = self.model.fba.getHomeostaticTargetMolecules()
        fba_reaction_id_to_index = {
            rxn_id: i for (i, rxn_id) in enumerate(self.fba_reaction_ids)
            }
        base_reaction_id_to_index = {
            rxn_id: i for (i, rxn_id) in enumerate(self.base_reaction_ids)
            }
        base_rxn_indexes = []
        fba_rxn_indexes = []
        v = []

        for fba_rxn_id in self.fba_reaction_ids:
            base_rxn_id = fba_reaction_ids_to_base_reaction_ids[fba_rxn_id]
            base_rxn_indexes.append(base_reaction_id_to_index[base_rxn_id])
            fba_rxn_indexes.append(fba_reaction_id_to_index[fba_rxn_id])
            if fba_rxn_id.endswith(REVERSE_TAG):
                v.append(-1)
            else:
                v.append(1)

        base_rxn_indexes = np.array(base_rxn_indexes)
        fba_rxn_indexes = np.array(fba_rxn_indexes)
        v = np.array(v)
        shape = (len(self.base_reaction_ids), len(self.fba_reaction_ids))

        self.reaction_mapping_matrix = csr_matrix(
            (v, (base_rxn_indexes, fba_rxn_indexes)),
            shape=shape)


    def __getstate__(self):
        return self.parameters

    def __setstate__(self, state):
        self.__init__(state)

    def ports_schema(self):
        ports = {
            'bulk': numpy_schema('bulk'),
            'bulk_total': numpy_schema('bulk'),

            'environment': {
                'media_id': {
                    '_default': '',
                    '_updater': 'set'},
                'exchange': {
                    str(element): {'_default': 0}
                    for element in self.environment_molecules},
                'exchange_data': {
                    'unconstrained': {'_default': {}},
                    'constrained': {'_default': set()}
                }
            },

            'boundary': {
                'external': {
                    '*': {'_default': 0 * vivunits.mM}
                }
            },

            'listeners': {
                'mass': listener_schema({
                    'cell_mass': 0.0,
                    'dry_mass': 0.0,
                    'rna_mass': 0.0,
                    'protein_mass': 0.0}),

                'fba_results': listener_schema({
                    'media_id': '',
                    'conc_updates': ([0.] * len(self.conc_update_molecules),
                        self.conc_update_molecules),
                    'catalyst_counts': ([0] * len(self.model.catalyst_ids),
                        self.model.catalyst_ids),
                    'translation_gtp': 0,
                    'coefficient': 0.0,
                    'unconstrained_molecules': (
                        [False] * len(self.exchange_molecules),
                        self.exchange_molecules),
                    'constrained_molecules': (
                        [False] * len(self.exchange_molecules),
                        self.exchange_molecules),
                    'uptake_constraints': (
                        [-1.] * len(self.exchange_molecules),
                        self.exchange_molecules),
                    'delta_metabolites': (
                        [0] * len(self.model.metaboliteNamesFromNutrients),
                        self.model.metaboliteNamesFromNutrients),
                    'reaction_fluxes': ([0.] * len(self.fba_reaction_ids),
                        self.fba_reaction_ids),
                    'external_exchange_fluxes': (
                        [0.] * len(self.externalMoleculeIDs),
                        self.externalMoleculeIDs),
                    'objective_value': 0.,
                    'shadow_prices': ([0.] * len(self.outputMoleculeIDs),
                        self.outputMoleculeIDs),
                    'reduced_costs': ([0.] * len(self.fba_reaction_ids),
                        self.fba_reaction_ids),
                    'target_concentrations': (
                        [0.] * len(self.homeostaticTargetMolecules),
                        self.homeostaticTargetMolecules),
                    'homeostatic_objective_values': (
                        [0.] * len(self.homeostaticTargetMolecules),
                        self.homeostaticTargetMolecules),
                    'kinetic_objective_values': (
                        [0.] * len(self.kineticTargetFluxNames),
                        self.kineticTargetFluxNames),
                    'base_reaction_fluxes': (
                        [0.] * len(self.base_reaction_ids),
                        self.base_reaction_ids),
                    # 'estimated_fluxes': {},
                    # 'estimated_homeostatic_dmdt': {},
                    # 'target_homeostatic_dmdt': {},
                    # 'estimated_exchange_dmdt': {},
                    # 'target_kinetic_fluxes': {},
                    # 'target_kinetic_bounds': {},
                    # 'target_maintenance_flux': 0
                }),

                'enzyme_kinetics': listener_schema({
                    'metabolite_counts_init': (
                        [0] * len(self.model.metaboliteNamesFromNutrients),
                        self.model.metaboliteNamesFromNutrients),
                    'metabolite_counts_final': (
                        [0] * len(self.model.metaboliteNamesFromNutrients),
                        self.model.metaboliteNamesFromNutrients),
                    'enzyme_counts_init': (
                        [0] * len(self.model.kinetic_constraint_enzymes),
                        self.model.kinetic_constraint_enzymes),
                    'counts_to_molar': 1.0,
                    'actual_fluxes': (
                        [0.] * len(self.model.kinetics_constrained_reactions),
                        self.model.kinetics_constrained_reactions),
                    'target_fluxes': (
                        [0.] * len(self.model.kinetics_constrained_reactions),
                        self.model.kinetics_constrained_reactions),
                    'target_fluxes_upper': (
                        [0.] * len(self.model.kinetics_constrained_reactions),
                        self.model.kinetics_constrained_reactions),
                    'target_fluxes_lower': (
                        [0.] * len(self.model.kinetics_constrained_reactions),
                        self.model.kinetics_constrained_reactions),
                    'target_aa_conc': ([0.] * len(self.aa_names), self.aa_names)})
            },

            'polypeptide_elongation': {
                'aa_count_diff': {
                    '_default': {},
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'empty_dict'},
                'gtp_to_hydrolyze': {
                    '_default': 0,
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'zero',
                },
                'aa_exchange_rates': {
                    '_default': 0,
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'zero'
                },
            },
            'global_time': {'_default': 0},
            'timestep': {'_default': self.parameters['time_step']},
            'next_update_time': {
                '_default': self.parameters['time_step'],
                '_updater': 'set',
                '_divider': 'set'}
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
        # At t=0, convert all strings to indices
        if self.metabolite_idx is None:
            self.metabolite_idx = bulk_name_to_idx(
                self.model.metaboliteNamesFromNutrients, states['bulk']['id'])
            self.catalyst_idx = bulk_name_to_idx(
                self.model.catalyst_ids, states['bulk']['id'])
            self.kinetics_enzymes_idx = bulk_name_to_idx(
                self.model.kinetic_constraint_enzymes, states['bulk']['id'])
            self.kinetics_substrates_idx = bulk_name_to_idx(
                self.model.kinetic_constraint_substrates, states['bulk']['id'])
            self.aa_idx = bulk_name_to_idx(self.aa_names, states['bulk']['id'])

        timestep = states['timestep']

        # Load current state of the sim
        # Get internal state variables
        metabolite_counts_init = counts(
            states['bulk'], self.metabolite_idx)
        catalyst_counts = counts(states['bulk'], self.catalyst_idx)
        kinetic_enzyme_counts = counts(
            states['bulk'], self.kinetics_enzymes_idx)
        kinetic_substrate_counts = counts(
            states['bulk'], self.kinetics_substrates_idx)

        translation_gtp = states['polypeptide_elongation']['gtp_to_hydrolyze']
        cell_mass = states['listeners']['mass']['cell_mass'] * units.fg
        dry_mass = states['listeners']['mass']['dry_mass'] * units.fg

        # Calculate state values
        cellVolume = cell_mass / self.cellDensity
        counts_to_molar = (1 / (self.nAvogadro * cellVolume)
                           ).asUnit(CONC_UNITS)

        # Coefficient to convert between flux (mol/g DCW/hr) basis and
        # concentration (M) basis
        coefficient = (dry_mass / cell_mass * self.cellDensity
            * timestep * units.s)
        
        # Get exchange constraints
        unconstrained = set(states['environment']['exchange_data'][
            'unconstrained'])
        constrained = states['environment']['exchange_data']['constrained']

        # Determine updates to concentrations depending on the current state
        current_media_id = states['environment']['media_id']
        doubling_time = self.nutrientToDoublingTime.get(
            current_media_id, self.nutrientToDoublingTime[self.media_id])        
        if self.include_ppgpp:
            # Sim does not include ppGpp regulation so do not update biomass
            # by RNA/protein ratio and include ppGpp concentration as a
            # homeostatic target
            conc_updates = self.model.getBiomassAsConcentrations(doubling_time)
            conc_updates[self.model.ppgpp_id] = self.model.getppGppConc(
                doubling_time).asUnit(CONC_UNITS)
        else:
            # Sim includes ppGpp regulation so update biomass based on
            # RNA/protein ratio which is controlled by ppGpp and other growth
            # regulation
            rp_ratio = states['listeners']['mass']['rna_mass'
                ] / states['listeners']['mass']['protein_mass']
            conc_updates = self.model.getBiomassAsConcentrations(
                doubling_time, rp_ratio=rp_ratio)
            
        if self.use_trna_charging:
            conc_updates.update(self.update_amino_acid_targets(
                counts_to_molar,
                states['polypeptide_elongation']['aa_count_diff'],
                dict(zip(self.aa_names,
                         counts(states['bulk_total'], self.aa_idx)))
            ))

        # Converted from units to make reproduction from listener data
        # accurate to model results (otherwise can have floating point diffs)
        conc_updates = {
            met: conc.asNumber(CONC_UNITS)
            for met, conc in conc_updates.items()}
        
        aa_uptake_package = None
        if self.mechanistic_aa_transport:
            aa_in_media = np.array([
                states['boundary']['external'][aa_name
                    ] > self.import_constraint_threshold
                for aa_name in self.aa_environment_names
            ])
            aa_in_media[self.removed_aa_uptake] = False
            exchange_rates = (states['polypeptide_elongation'][
                'aa_exchange_rates'] * timestep).asNumber(
                    CONC_UNITS / TIME_UNITS)
            aa_uptake_package = (exchange_rates[aa_in_media],
                                 self.aa_exchange_names[aa_in_media], True)

        if self.parameters['reduce_murein_objective']:
            conc_updates['CPD-12261[p]'] /= 2.27

        # Update FBA problem based on current state
        # Set molecule availability (internal and external)
        self.model.set_molecule_levels(metabolite_counts_init, counts_to_molar,
            coefficient, current_media_id, unconstrained, constrained,
            conc_updates, aa_uptake_package)

        # Set reaction limits for maintenance and catalysts present
        self.model.set_reaction_bounds(catalyst_counts, counts_to_molar,
            coefficient, translation_gtp)

        # Constrain reactions based on targets
        targets, upper_targets, lower_targets = \
            self.model.set_reaction_targets(kinetic_enzyme_counts,
                kinetic_substrate_counts, counts_to_molar, timestep * units.s)

        # Solve FBA problem and update states
        n_retries = 3
        fba = self.model.fba
        fba.solve(n_retries)

        # Internal molecule changes
        delta_metabolites = (1 / counts_to_molar) * (CONC_UNITS *
            fba.getOutputMoleculeLevelsChange())
        metabolite_counts_final = np.fmax(stochasticRound(
            self.random_state,
            metabolite_counts_init + delta_metabolites.asNumber()
            ), 0).astype(np.int64)
        delta_metabolites_final = metabolite_counts_final - metabolite_counts_init

        # Environmental changes
        exchange_fluxes = CONC_UNITS * fba.getExternalExchangeFluxes()
        converted_exchange_fluxes = (
            exchange_fluxes / coefficient).asNumber(GDCW_BASIS)
        delta_nutrients = ((1 / counts_to_molar) *
                           exchange_fluxes).asNumber().astype(int)

        # Write outputs to listeners
        unconstrained, constrained, uptake_constraints = \
            self.get_import_constraints(unconstrained, constrained,
            GDCW_BASIS)

        # below is used for comparing target and estimate between GD-FBA
        # and LP-FBA, no effect on model
        # maintenance_ngam = ((self.ngam * coefficient) /
        #     (counts_to_molar*timestep)).asNumber()
        # # TODO (Cyrus) Add change in mass when implementing,
        # # currently counts/mass.
        # maintenance_gam = (self.gam).asNumber()
        # maintenance_gam_active = translation_gtp/timestep
        # maintenance_target = maintenance_ngam + maintenance_gam \
        #     + maintenance_gam_active


        # objective_counts = {str(key): int((self.model.homeostatic_objective[
        #     key] / counts_to_molar).asNumber()) - int(states['bulk']['count'][
        #         states['bulk']['id'] == key])
        #     for key in fba.getHomeostaticTargetMolecules()}

        # denom = counts_to_molar*timestep
        # kinetic_targets = {str(self.model.kinetics_constrained_reactions[i]):
        #     int((targets[i] / denom).asNumber())
        #     for i in range(len(targets))}

        # target_kinetic_bounds = {
        #     str(self.model.kinetics_constrained_reactions[i]):
        #         (int((lower_targets[i] / denom).asNumber()),
        #         int((upper_targets[i] / denom).asNumber()))
        #     for i in range(len(targets))}

        # fluxes = fba.getReactionFluxes() / timestep
        # names = fba.getReactionIDs()

        # flux_dict = {str(names[i]): int((fluxes[i] / denom).asNumber())
        #     for i in range(len(names))}

        reaction_fluxes = fba.getReactionFluxes() / timestep
        update = {
            'bulk': [(self.metabolite_idx, delta_metabolites_final)],

            'environment': {
                'exchange': {
                    str(molecule[:-3]): delta_nutrients[index]
                    for index, molecule in enumerate(
                        self.externalMoleculeIDs)}},

            'listeners': {
                'fba_results': {
                    'media_id': current_media_id,
                    'conc_updates': [conc_updates.get(m, 0)
                        for m in self.conc_update_molecules],
                    'catalyst_counts': catalyst_counts,
                    'translation_gtp': translation_gtp,
                    'coefficient': coefficient.asNumber(CONVERSION_UNITS),
                    'unconstrained_molecules': unconstrained,
                    'constrained_molecules': constrained,
                    'uptake_constraints': uptake_constraints,
                    'delta_metabolites': delta_metabolites_final,
                    'reaction_fluxes': reaction_fluxes,
                    'external_exchange_fluxes': converted_exchange_fluxes,
                    'objective_value': fba.getObjectiveValue(),
                    'shadow_prices': fba.getShadowPrices(
                        self.model.metaboliteNamesFromNutrients),
                    'reduced_costs': fba.getReducedCosts(fba.getReactionIDs()),
                    'target_concentrations': [
                        self.model.homeostatic_objective[mol]
                        for mol in fba.getHomeostaticTargetMolecules()],
                    'homeostatic_objective_values': \
                        fba.getHomeostaticObjectiveValues(),
                    'kinetic_objective_values': \
                        fba.getKineticObjectiveValues(),
                    'base_reaction_fluxes': self.reaction_mapping_matrix.dot(
                        reaction_fluxes),
                    
                    # Quite large, comment out to reduce emit size
                    # 'estimated_fluxes': flux_dict ,
                    # 'estimated_homeostatic_dmdt': {
                    #     metabolite: delta_metabolites_final[index]
                    #     for index, metabolite in enumerate(
                    #         self.model.metaboliteNamesFromNutrients)},
                    # 'target_homeostatic_dmdt': objective_counts,
                    # 'estimated_exchange_dmdt': {
                    #     molecule: delta_nutrients[index]
                    #     for index, molecule in enumerate(
                    #         fba.getExternalMoleculeIDs())},
                    # 'target_kinetic_fluxes': kinetic_targets,
                    # 'target_kinetic_bounds': target_kinetic_bounds,
                    # 'target_maintenance_flux': maintenance_target,
                },

                'enzyme_kinetics': {
                    'metabolite_counts_init': metabolite_counts_init,
                    'metabolite_counts_final': metabolite_counts_final,
                    'enzyme_counts_init': kinetic_enzyme_counts,
                    'counts_to_molar': counts_to_molar.asNumber(CONC_UNITS),
                    'actual_fluxes': fba.getReactionFluxes(
                        self.model.kinetics_constrained_reactions) / timestep,
                    'target_fluxes': targets / timestep,
                    'target_fluxes_upper': upper_targets / timestep,
                    'target_fluxes_lower': lower_targets / timestep,
                    'target_aa_conc': [self.aa_targets.get(id_, 0.)
                                       for id_ in self.aa_names]}},
                                       
                'next_update_time': states['global_time'] + states['timestep']}

        return update

    def update_amino_acid_targets(self, counts_to_molar: Unum, 
        count_diff: dict[str, float],
        amino_acid_counts: np.ndarray[int]) -> dict[str, Unum]:
        """
        Finds new amino acid concentration targets based on difference in
        supply and number of amino acids used in polypeptide_elongation. 
        Skips updates to molecules defined in self.aa_targets_not_updated:
        - L-SELENOCYSTEINE: rare AA that led to high variability when updated

        Args:
            counts_to_molar: conversion from counts to molar

        Returns:
            ``{AA name (str): new target AA conc (float with mol/volume units)}``
        """

        if len(self.aa_targets):
            for aa, diff in count_diff.items():
                if aa in self.aa_targets_not_updated:
                    continue
                self.aa_targets[aa] += diff
                # TODO (Santiago): Improve targets update
                if self.aa_targets[aa] < 0:
                    print('Warning: updated amino acid target for '
                          f'{aa} was negative - adjusted to be positive.')
                    self.aa_targets[aa] = 1.

        # First time step of a simulation so set target to current counts to
        # prevent concentration jumps between generations
        else:
            for aa, counts in amino_acid_counts.items():
                if aa in self.aa_targets_not_updated:
                    continue
                self.aa_targets[aa] = float(counts)

        conc_updates = {aa: counts * counts_to_molar for aa,
                        counts in self.aa_targets.items()}

        # Update linked metabolites that will follow an amino acid
        for met, link in self.linked_metabolites.items():
            conc_updates[met] = conc_updates.get(
                link['lead'], 0 * counts_to_molar) * link['ratio']

        return conc_updates


class FluxBalanceAnalysisModel(object):
    """
    Metabolism model that solves an FBA problem with modular_fba.
    """

    def __init__(self, parameters: dict[str, Any], 
                 timeline: tuple[tuple[str]]=None, include_ppgpp: bool=True):
        """
        Args:
            parameters: parameters from simulation data
            timeline: timeline for nutrient changes during simulation
                (time of change, media ID), by default [(0.0, 'minimal')]
            include_ppgpp: if True, ppGpp is included as a concentration target
        """
        nutrients = timeline[0][1]

        # Local sim_data references
        metabolism = parameters['metabolism']
        self.stoichiometry = metabolism.reaction_stoich
        self.maintenance_reaction = metabolism.maintenance_reaction

        # Load constants
        self.ngam = parameters['ngam']
        gam = parameters['dark_atp'] * parameters['cell_dry_mass_fraction']

        self.exchange_constraints = metabolism.exchange_constraints

        self._biomass_concentrations = {}  # type: dict
        self.getBiomassAsConcentrations = parameters[
            'get_biomass_as_concentrations']

        # Include ppGpp concentration target in objective if not handled
        # kinetically in other processes
        self.ppgpp_id = parameters['ppgpp_id']
        self.getppGppConc = parameters['get_ppGpp_conc']

        # go through all media in the timeline and add to metaboliteNames
        metaboliteNamesFromNutrients = set()
        conc_from_nutrients = (metabolism.concentration_updates.
            concentrations_based_on_nutrients)
        if include_ppgpp:
            metaboliteNamesFromNutrients.add(self.ppgpp_id)
        for time, media_id in timeline:
            exchanges = parameters['exchange_data_from_media'](media_id)
            metaboliteNamesFromNutrients.update(conc_from_nutrients(
                imports=exchanges['importExchangeMolecules']))
        self.metaboliteNamesFromNutrients = list(
            sorted(metaboliteNamesFromNutrients))
        exchange_molecules = sorted(parameters['exchange_molecules'])
        molecule_masses = dict(zip(exchange_molecules, 
            parameters['get_masses'](exchange_molecules).asNumber(
                MASS_UNITS / COUNTS_UNITS)))

        # Setup homeostatic objective concentration targets
        # Determine concentrations based on starting environment
        conc_dict = conc_from_nutrients(media_id=nutrients,
                                        imports=parameters['imports'])
        doubling_time = parameters['doubling_time']
        conc_dict.update(self.getBiomassAsConcentrations(doubling_time))
        if include_ppgpp:
            conc_dict[self.ppgpp_id] = self.getppGppConc(doubling_time)
        self.homeostatic_objective = dict(
            (key, conc_dict[key].asNumber(CONC_UNITS)) for key in conc_dict)

        # TODO: For testing, remove later (perhaps after modifying sim data)
        if parameters["reduce_murein_objective"]:
            self.homeostatic_objective['CPD-12261[p]'] /= 2.27

        # Include all concentrations that will be present in a sim for constant
        # length listeners
        for met in self.metaboliteNamesFromNutrients:
            if met not in self.homeostatic_objective:
                self.homeostatic_objective[met] = 0.

        # Data structures to compute reaction bounds based on enzyme
        # presence/absence
        self.catalyst_ids = metabolism.catalyst_ids
        self.reactions_with_catalyst = metabolism.reactions_with_catalyst

        i = metabolism.catalysis_matrix_I
        j = metabolism.catalysis_matrix_J
        v = metabolism.catalysis_matrix_V
        shape = (i.max() + 1, j.max() + 1)
        self.catalysis_matrix = csr_matrix((v, (i, j)), shape=shape)

        # Function to compute reaction targets based on kinetic parameters and
        # molecule concentrations
        self.get_kinetic_constraints = metabolism.get_kinetic_constraints

        # Remove disabled reactions so they don't get included in the FBA
        # problem setup
        kinetic_constraint_reactions = metabolism.kinetic_constraint_reactions
        constraintsToDisable = metabolism.constraints_to_disable
        self.active_constraints_mask = np.array(
            [(rxn not in constraintsToDisable)
             for rxn in kinetic_constraint_reactions])
        self.kinetics_constrained_reactions = list(
            np.array(kinetic_constraint_reactions)[
                self.active_constraints_mask])

        self.kinetic_constraint_enzymes = metabolism.kinetic_constraint_enzymes
        self.kinetic_constraint_substrates = \
            metabolism.kinetic_constraint_substrates

        # Set solver and kinetic objective weight (lambda)
        solver = metabolism.solver
        kinetic_objective_weight = metabolism.kinetic_objective_weight
        kinetic_objective_weight_in_range = \
            metabolism.kinetic_objective_weight_in_range

        # Disable kinetics completely if weight is 0 or specified in file above
        if not USE_KINETICS or kinetic_objective_weight == 0:
            objective_type = 'homeostatic'
            self.use_kinetics = False
            kinetic_objective_weight = 0
        else:
            objective_type = 'homeostatic_kinetics_mixed'
            self.use_kinetics = True

        # Set up FBA solver
        # reactionRateTargets value is just for initialization, it gets reset
        # each timestep during evolveState
        fba_options = {
            "reactionStoich": metabolism.reaction_stoich,
            "externalExchangedMolecules": exchange_molecules,
            "objective": self.homeostatic_objective,
            "objectiveType": objective_type,
            "objectiveParameters": {
                "kineticObjectiveWeight": kinetic_objective_weight,
                'kinetic_objective_weight_in_range': \
                    kinetic_objective_weight_in_range,
                "reactionRateTargets": {reaction: 1 for reaction
                    in self.kinetics_constrained_reactions},
                "oneSidedReactionTargets": [],
            },
            "moleculeMasses": molecule_masses,
            # The "inconvenient constant"--limit secretion (e.g., of CO2)
            "secretionPenaltyCoeff": metabolism.secretion_penalty_coeff,
            "solver": solver,
            "maintenanceCostGAM": gam.asNumber(COUNTS_UNITS / MASS_UNITS),
            "maintenanceReaction": metabolism.maintenance_reaction,
        }
        self.fba = FluxBalanceAnalysis(**fba_options)

        self.metabolite_names = {met: i for i, met in enumerate(
            self.fba.getOutputMoleculeIDs())}
        self.aa_names_no_location = [x[:-3]
                                     for x in parameters['amino_acid_ids']]

    def update_external_molecule_levels(self, objective: dict[str, Unum],
        metabolite_concentrations: Unum, 
        external_molecule_levels: np.ndarray[float]) -> np.ndarray[float]:
        """
        Limit amino acid uptake to what is needed to meet concentration
        objective to prevent use as carbon source, otherwise could be used
        as an infinite nutrient source.

        Args:
            objective: homeostatic objective for internal
                molecules (molecule ID: concentration in counts/volume units)
            metabolite_concentrations: concentration for each
                molecule in metabolite_names
            external_molecule_levels: current limits on
                external molecule availability

        Returns:
            Updated limits on external molecule availability

        TODO(wcEcoli):
            determine rate of uptake so that some amino acid uptake can
            be used as a carbon/nitrogen source
        """

        external_exchange_molecule_ids = self.fba.getExternalMoleculeIDs()
        for aa in self.aa_names_no_location:
            if aa + "[p]" in external_exchange_molecule_ids:
                idx = external_exchange_molecule_ids.index(aa + "[p]")
            elif aa + "[c]" in external_exchange_molecule_ids:
                idx = external_exchange_molecule_ids.index(aa + "[c]")
            else:
                continue

            conc_diff = objective[aa + "[c]"] - \
                metabolite_concentrations[self.metabolite_names[aa +
                    "[c]"]].asNumber(CONC_UNITS)
            if conc_diff < 0:
                conc_diff = 0

            if external_molecule_levels[idx] > conc_diff:
                external_molecule_levels[idx] = conc_diff

        return external_molecule_levels

    def set_molecule_levels(self, metabolite_counts: np.ndarray[int], 
        counts_to_molar: Unum, coefficient: Unum, current_media_id: str, 
        unconstrained: set[str], constrained: set[str], 
        conc_updates: dict[str, Unum], aa_uptake_package: tuple[
            np.ndarray[float], np.ndarray[str], bool]=None):
        """
        Set internal and external molecule levels available to the FBA solver.

        Args:
            metabolite_counts: counts for each metabolite with a concentration target
            counts_to_molar: conversion from counts to molar (counts/volume)
            coefficient: coefficient to convert from mmol/g DCW/hr to mM basis 
                (mass*time/volume)
            current_media_id: ID of current media
            unconstrained: molecules that have unconstrained import
            constrained: molecules (keys) and their limited max uptake rates 
                (mol / mass / time)
            conc_updates: updates to concentrations targets for molecules (mmol/L)
            aa_uptake_package: (uptake rates, amino acid names, force levels), 
                determines whether to set hard uptake rates
        """

        # Update objective from media exchanges
        external_molecule_levels, objective = self.exchange_constraints(
            self.fba.getExternalMoleculeIDs(), coefficient, CONC_UNITS,
            current_media_id, unconstrained, constrained, conc_updates,
        )
        self.fba.update_homeostatic_targets(objective)
        self.homeostatic_objective = {**self.homeostatic_objective, **objective}

        # Internal concentrations
        metabolite_conc = counts_to_molar * metabolite_counts
        self.fba.setInternalMoleculeLevels(
            metabolite_conc.asNumber(CONC_UNITS))

        # External concentrations
        external_molecule_levels = self.update_external_molecule_levels(
            objective, metabolite_conc, external_molecule_levels)
        self.fba.setExternalMoleculeLevels(external_molecule_levels)

        if aa_uptake_package:
            levels, molecules, force = aa_uptake_package
            self.fba.setExternalMoleculeLevels(levels, molecules=molecules,
                force=force, allow_export=True)

    def set_reaction_bounds(self, catalyst_counts: np.ndarray[int], 
        counts_to_molar: Unum, coefficient: Unum, gtp_to_hydrolyze: float):
        """
        Set reaction bounds for constrained reactions in the FBA object.

        Args:
            catalyst_counts: counts of enzyme catalysts
            counts_to_molar: conversion from counts to molar (counts/volume)
            coefficient: coefficient to convert from mmol/g DCW/hr to mM basis 
                (mass*time/volume)
            gtp_to_hydrolyze: number of GTP molecules to hydrolyze to
                account for consumption in translation
        """

        # Maintenance reactions
        # Calculate new NGAM
        flux = (self.ngam * coefficient).asNumber(CONC_UNITS)
        self.fba.setReactionFluxBounds(
            self.fba._reactionID_NGAM,
            lowerBounds=flux, upperBounds=flux,
        )

        # Calculate GTP usage based on how much was needed in polypeptide
        # elongation in previous step.
        flux = (counts_to_molar * gtp_to_hydrolyze).asNumber(CONC_UNITS)
        self.fba.setReactionFluxBounds(
            self.fba._reactionID_polypeptideElongationEnergy,
            lowerBounds=flux, upperBounds=flux,
        )

        # Set hard upper bounds constraints based on enzyme presence
        # (infinite upper bound) or absence (upper bound of zero)
        reaction_bounds = np.inf * np.ones(len(self.reactions_with_catalyst))
        no_rxn_mask = self.catalysis_matrix.dot(catalyst_counts) == 0
        reaction_bounds[no_rxn_mask] = 0
        self.fba.setReactionFluxBounds(self.reactions_with_catalyst,
            upperBounds=reaction_bounds, raiseForReversible=False)

    def set_reaction_targets(self, kinetic_enzyme_counts: np.ndarray[int],
        kinetic_substrate_counts: np.ndarray[int], counts_to_molar: Unum, 
        time_step: Unum) -> tuple[np.ndarray[float], np.ndarray[float], 
                                  np.ndarray[float]]:
        """
        Set reaction targets for constrained reactions in the FBA object.

        Args:
            kinetic_enzyme_counts: counts of enzymes used in kinetic constraints
            kinetic_substrate_counts: counts of substrates used in kinetic 
                constraints
            counts_to_molar: conversion from counts to molar (counts/volume)
            time_step: current time step (time)

        Returns:
            3-element tuple containing

            - **mean_targets**: mean target for each constrained reaction
            - **upper_targets**: upper target limit for each constrained reaction
            - **lower_targets**: lower target limit for each constrained reaction
        """

        if self.use_kinetics:
            enzyme_conc = counts_to_molar * kinetic_enzyme_counts
            substrate_conc = counts_to_molar * kinetic_substrate_counts

            # Set target fluxes for reactions based on their most relaxed
            # constraint
            reaction_targets = self.get_kinetic_constraints(
                enzyme_conc, substrate_conc)

            # Calculate reaction flux target for current time step
            targets = (time_step * reaction_targets).asNumber(CONC_UNITS)[
                self.active_constraints_mask, :]
            lower_targets = targets[:, 0]
            mean_targets = targets[:, 1]
            upper_targets = targets[:, 2]

            # Set kinetic targets only if kinetics is enabled
            self.fba.set_scaled_kinetic_objective(time_step.asNumber(units.s))
            self.fba.setKineticTarget(
                self.kinetics_constrained_reactions, mean_targets,
                lower_targets=lower_targets, upper_targets=upper_targets)
        else:
            lower_targets = np.zeros(len(self.kinetics_constrained_reactions))
            mean_targets = np.zeros(len(self.kinetics_constrained_reactions))
            upper_targets = np.zeros(len(self.kinetics_constrained_reactions))

        return mean_targets, upper_targets, lower_targets


def test_metabolism_listener():
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    sim = EcoliSim.from_file()
    sim.total_time = 2
    sim.raw_output = False
    sim.build_ecoli()
    sim.run()
    data = sim.query()
    reaction_fluxes = data['agents']['0']['listeners']['fba_results']['reaction_fluxes']
    assert(type(reaction_fluxes[0]) == list)
    assert(type(reaction_fluxes[1]) == list)


if __name__ == '__main__':
    test_metabolism_listener()
