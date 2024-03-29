"""
MetabolismRedux
"""

import numpy as np
import time
from typing import Callable
from unum import Unum
import warnings
from scipy.sparse import csr_matrix

from vivarium.core.process import Step
from vivarium.library.units import units as vivunits

from ecoli.library.schema import (numpy_schema, bulk_name_to_idx,
    listener_schema, counts)

from wholecell.utils import units

from ecoli.processes.registries import topology_registry
import cvxpy as cp
from typing import Iterable, Mapping
from dataclasses import dataclass

from reconstruction.ecoli.dataclasses.process.metabolism import REVERSE_TAG

COUNTS_UNITS = units.mmol
VOLUME_UNITS = units.L
MASS_UNITS = units.g
TIME_UNITS = units.s
CONC_UNITS = COUNTS_UNITS / VOLUME_UNITS
CONVERSION_UNITS = MASS_UNITS * TIME_UNITS / VOLUME_UNITS
GDCW_BASIS = units.mmol / units.g / units.h


NAME = 'ecoli-metabolism-redux'
TOPOLOGY = topology_registry.access('ecoli-metabolism')
# TODO (Cyrus) - Re-add when kinetics are added.
# TOPOLOGY['kinetic_flux_targets'] = ('rates', 'fluxes')
topology_registry.register(NAME, TOPOLOGY)

# TODO (Cyrus) - Remove when have a better way to handle these rxns.
# ParCa mistakes in carbon gen, efflux/influx proton gen, mass gen
BAD_RXNS = ["RXN-12440", "TRANS-RXN-121", "TRANS-RXN-300",
            "TRANS-RXN-8", "R15-RXN-MET/CPD-479//CPD-479/MET.25."]


class MetabolismRedux(Step):
    name = NAME
    topology = TOPOLOGY

    defaults = {
        'stoich_dict': {},
        'reaction_catalysts': {},
        # TODO (Cyrus) -- get these passed in, subset of the stoichimetry
        'kinetic_rates': [],
        'media_id': 'minimal',
        'imports': {},
        'concentration_updates': None,
        'maintenance_reaction': {},
        'nutrient_to_doubling_time': {},
        'use_trna_charging': False,
        'include_ppgpp': False,
        'mechanistic_aa_transport': False,
        'aa_targets_not_updated': set(),
        'import_constraint_threshold': 0,
        'exchange_molecules': [],
        'non_growth_associated_maintenance': 8.39 * units.mmol / (units.g * units.h),
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
        'base_reaction_ids': [],
        'fba_reaction_ids_to_base_reaction_ids': [],
        'constraints_to_disable': [],
        'kinetic_objective_weight': 1e-7,
        'kinetic_objective_weight_in_range': 1e-10,
        'secretion_penalty_coeff': 1e-3,
        'time_step': 1
    }

    def __init__(self, parameters):
        super().__init__(parameters)

        # Use information from the environment and sim
        self.nutrient_to_doubling_time = self.parameters['nutrient_to_doubling_time']
        self.use_trna_charging = self.parameters['use_trna_charging']
        self.include_ppgpp = self.parameters['include_ppgpp']
        self.mechanistic_aa_transport = self.parameters[
            'mechanistic_aa_transport']
        self.current_timeline = self.parameters['current_timeline']
        self.media_id = self.parameters['media_id']
        self.exchange_molecules = self.parameters[
            'exchange_molecules']
        self.aa_names = self.parameters['aa_names']
        self.aa_targets_not_updated = self.parameters['aa_targets_not_updated']

        stoich_dict = dict(sorted(self.parameters['stoich_dict'].items()))
        for rxn in BAD_RXNS:
            stoich_dict.pop(rxn)
        # Add maintenance reaction
        stoich_dict['maintenance_reaction'] = self.parameters['maintenance_reaction']

        # Get all metabolite names
        self.metabolite_names = set()
        for reaction, stoich in stoich_dict.items():
            self.metabolite_names.update(stoich.keys())
        self.metabolite_names.update(set(self.parameters['exchange_molecules']))

        self.metabolite_names = sorted(list(self.metabolite_names))
        self.reaction_names = list(stoich_dict.keys())

        metabolites_idx = {species: i for i, species in enumerate(self.metabolite_names)}

        # Get indices of catalysts for each reaction
        reaction_catalysts = self.parameters['reaction_catalysts']
        self.catalyst_ids = self.parameters['catalyst_ids']
        catalyst_idx = {catalyst: i for i, catalyst in enumerate(self.catalyst_ids)}

        # Create stoichiometry matrix (dot reaction fluxes to get
        # molecule deltas) and catalysis matrix (dot catalyst counts
        # to get catalyst counts per enzyme-catalyzed reaction)
        coeffs = []
        met_ind = []
        rxn_ind = []
        catalyst_ind = []
        catalyzed_rxn_ind = []
        catalyzed_rxn_idx = 0
        for rxn_idx, (reaction, stoich) in enumerate(stoich_dict.items()):
            for species, coefficient in stoich.items():
                met_ind.append(metabolites_idx[species])
                rxn_ind.append(rxn_idx)
                coeffs.append(coefficient)
            enzyme_idx = [catalyst_idx[catalyst]
                for catalyst in reaction_catalysts.get(reaction, [])]
            if len(enzyme_idx) > 1:
                catalyst_ind.extend(enzyme_idx)
                catalyzed_rxn_ind.extend([catalyzed_rxn_idx]*len(enzyme_idx))
                catalyzed_rxn_idx += 1
        self.stoichiometry = csr_matrix((coeffs, (met_ind, rxn_ind)), dtype=int)
        self.catalysis_matrix = csr_matrix(([1]*len(catalyst_ind),
            (catalyzed_rxn_ind, catalyst_ind)), dtype=int)

        self.media_id = self.parameters['media_id']
        self.cell_density = self.parameters['cell_density']
        self.n_avogadro = self.parameters['avogadro']
        self.ngam = self.parameters['non_growth_associated_maintenance']
        self.gam = self.parameters['dark_atp'] * self.parameters[
            'cell_dry_mass_fraction']

        # new variables for the model
        self.cell_mass = None
        self.previous_mass = None
        self.reaction_fluxes = None

        # methods from config
        self._biomass_concentrations = {}  # type: dict
        self._getBiomassAsConcentrations = self.parameters[
            'get_biomass_as_concentrations']
        self.concentration_updates = self.parameters['concentration_updates']
        self.exchange_constraints = self.parameters['exchange_constraints']
        self.get_kinetic_constraints = self.parameters['get_kinetic_constraints']
        self.kinetic_constraint_reactions = self.parameters[
            'kinetic_constraint_reactions']

        # Include ppGpp concentration target in objective if not handled
        # kinetically in other processes
        self.ppgpp_id = self.parameters['ppgpp_id']
        self.getppGppConc = self.parameters['get_ppGpp_conc']

        # go through all media in the timeline and add to metaboliteNames
        homeostatic_metabolites = set()
        conc_from_nutrients = (self.concentration_updates.
            concentrations_based_on_nutrients)
        if self.include_ppgpp:
            homeostatic_metabolites.add(self.ppgpp_id)
        for time, media_id in self.current_timeline:
            exchanges = self.parameters['exchange_data_from_media'](media_id)
            homeostatic_metabolites.update(conc_from_nutrients(
                imports=exchanges['importExchangeMolecules']))
        self.homeostatic_metabolites = list(sorted(homeostatic_metabolites))

        # Setup homeostatic objective concentration targets
        # Determine concentrations based on starting environment
        conc_dict = conc_from_nutrients(media_id=self.current_timeline[0][1],
                                        imports=parameters['imports'])
        doubling_time = self.parameters['doubling_time']
        conc_dict.update(self.getBiomassAsConcentrations(doubling_time))
        if self.include_ppgpp:
            conc_dict[self.ppgpp_id] = self.getppGppConc(doubling_time)
        self.homeostatic_objective = dict(
            (key, conc_dict[key].asNumber(CONC_UNITS)) for key in conc_dict)

        # Include all concentrations that will be present in a sim for constant
        # length listeners
        for met in self.homeostatic_metabolites:
            if met not in self.homeostatic_objective:
                self.homeostatic_objective[met] = 0.

        # Molecules with concentration updates for listener
        self.linked_metabolites = self.parameters['linked_metabolites']
        doubling_time = self.nutrient_to_doubling_time.get(
            self.media_id,
            self.nutrient_to_doubling_time['minimal'])
        update_molecules = list(self.getBiomassAsConcentrations(
            doubling_time).keys())
        if self.use_trna_charging:
            update_molecules += [aa for aa in self.aa_names
                if aa not in self.aa_targets_not_updated]
            update_molecules += list(self.linked_metabolites.keys())
        if self.include_ppgpp:
            update_molecules += [self.parameters['ppgpp_id']]
        self.conc_update_molecules = sorted(update_molecules)

        self.aa_exchange_names = self.parameters['aa_exchange_names']
        self.removed_aa_uptake = self.parameters['removed_aa_uptake']
        self.aa_environment_names = [aa[:-3] for aa in self.aa_exchange_names]

        # Remove disabled reactions so they don't get included in the FBA
        # problem setup
        constraints_to_disable = self.parameters['constraints_to_disable']
        self.active_constraints_mask = np.array(
            [(rxn not in constraints_to_disable)
             for rxn in self.kinetic_constraint_reactions])

        # Network flow initialization
        self.network_flow_model = NetworkFlowModel(
            self.stoichiometry, self.metabolite_names,
            self.reaction_names, self.homeostatic_metabolites,
            self.kinetic_constraint_reactions, self.parameters['get_mass'],
            self.gam.asNumber(), self.active_constraints_mask)

        # important bulk molecule names
        self.catalyst_ids = self.parameters['catalyst_ids']
        self.aa_names = self.parameters['aa_names']
        self.kinetic_constraint_enzymes = self.parameters[
            'kinetic_constraint_enzymes']
        self.kinetic_constraint_substrates = self.parameters[
            'kinetic_constraint_substrates']

        # objective weights
        self.kinetic_objective_weight = self.parameters[
            'kinetic_objective_weight']
        self.secretion_penalty_coeff = self.parameters[
            'secretion_penalty_coeff']
        self.kinetic_objective_weight_in_range = self.parameters[
            'kinetic_objective_weight_in_range']

        # Helper indices for Numpy indexing
        self.homeostatic_metabolite_idx = None

        # Cache uptake parameters from previous timestep
        self.allowed_exchange_uptake = None

        # Track updated AA concentration targets with tRNA charging
        self.aa_targets = {}
        self.aa_targets_not_updated = self.parameters['aa_targets_not_updated']
        self.aa_names = self.parameters['aa_names']
        self.import_constraint_threshold = self.parameters[
            'import_constraint_threshold']

        # Get conversion matrix to compile individual fluxes in the FBA
        # solution to the fluxes of base reactions
        self.base_reaction_ids = self.parameters['base_reaction_ids']
        self.base_reaction_ids.append('maintenance_reaction')
        fba_reaction_ids_to_base_reaction_ids = self.parameters[
            'fba_reaction_ids_to_base_reaction_ids']
        fba_reaction_ids_to_base_reaction_ids['maintenance_reaction'
            ] = 'maintenance_reaction'
        fba_reaction_id_to_index = {
            rxn_id: i for (i, rxn_id) in enumerate(self.reaction_names)
            }
        base_reaction_id_to_index = {
            rxn_id: i for (i, rxn_id) in enumerate(self.base_reaction_ids)
            }
        base_rxn_indexes = []
        fba_rxn_indexes = []
        v = []

        for fba_rxn_id in self.reaction_names:
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
        shape = (len(self.base_reaction_ids), len(self.reaction_names))

        self.reaction_mapping_matrix = csr_matrix(
            (v, (base_rxn_indexes, fba_rxn_indexes)),
            shape=shape)

    def ports_schema(self):

        return {
            'bulk': numpy_schema('bulk'),
            'bulk_total': numpy_schema('bulk'),
            # 'kinetic_flux_targets': {reaction_id: {} for reaction_id
            #     in self.parameters['kinetic_rates']},

            'environment': {
                'media_id': {
                    '_default': '',
                    '_updater': 'set'},
                'exchange': {
                    str(element): {'_default': 0}
                    for element in self.exchange_molecules},
                'exchange_data': {
                    'unconstrained': {'_default': {}},
                    'constrained': {'_default': set()}
                }},

            'boundary': {
                'external': {
                    '*': {'_default': 0 * vivunits.mM}
                }
            },

            'polypeptide_elongation': {
                'aa_count_diff': {
                    '_default': {},
                    '_emit': True,
                    '_divider': 'empty_dict'},
                'gtp_to_hydrolyze': {
                    '_default': 0,
                    '_emit': True,
                    '_divider': 'zero'},
                'aa_exchange_rates': {
                    '_default': 0,
                    '_emit': True,
                    '_updater': 'set',
                    '_divider': 'zero'},
            },

            'listeners': {
                'mass': listener_schema({
                    'cell_mass': 0.0,
                    'dry_mass': 0.0,
                    'rna_mass': 0.0,
                    'protein_mass': 0.0}),

                'fba_results': listener_schema({
                    'media_id': '',
                    'conc_updates': ([], self.conc_update_molecules),
                    'base_reaction_fluxes': (
                        [], self.base_reaction_ids),
                    'solution_fluxes': (
                        [], self.network_flow_model.rxns),
                    'solution_dmdt': (
                        [], self.network_flow_model.mets),
                    'time_per_step': 0.0,
                    'estimated_fluxes': (
                        [], self.network_flow_model.rxns),
                    'estimated_homeostatic_dmdt': (
                        [], np.array(self.network_flow_model.mets)[
                            self.network_flow_model.homeostatic_idx]),
                    'target_homeostatic_dmdt': (
                        [], np.array(self.network_flow_model.mets)[
                            self.network_flow_model.homeostatic_idx]),
                    'estimated_exchange_dmdt': {},
                    'estimated_intermediate_dmdt': [],
                    'target_kinetic_fluxes': [],
                    'target_kinetic_bounds': [],
                    'reaction_catalyst_counts': [],
                    'maintenance_target': 0
                })
            },

            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']},
            'next_update_time': {
                '_default': self.parameters['time_step'],
                '_updater': 'set',
                '_divider': 'set'},
        }

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
        # Initialize indices
        if self.homeostatic_metabolite_idx is None:
            bulk_ids = states['bulk']['id']
            self.homeostatic_metabolite_idx = bulk_name_to_idx(
                self.homeostatic_metabolites, bulk_ids)
            self.catalyst_idx = bulk_name_to_idx(self.catalyst_ids, bulk_ids)
            self.kinetics_enzymes_idx = bulk_name_to_idx(
                self.kinetic_constraint_enzymes, bulk_ids)
            self.kinetics_substrates_idx = bulk_name_to_idx(
                self.kinetic_constraint_substrates, bulk_ids)
            self.aa_idx = bulk_name_to_idx(self.aa_names, bulk_ids)

        unconstrained = states['environment']['exchange_data']['unconstrained']
        constrained = states['environment']['exchange_data']['constrained']
        new_allowed_exchange_uptake = set(unconstrained).union(
            constrained.keys())
        new_exchange_molecules = set(self.exchange_molecules).union(
            set(new_allowed_exchange_uptake))

        # set up network flow model exchanges and uptakes
        if (new_exchange_molecules != self.exchange_molecules) or (
            new_allowed_exchange_uptake != self.allowed_exchange_uptake
        ):
            self.network_flow_model.set_up_exchanges(
                new_exchange_molecules, new_allowed_exchange_uptake)
            self.exchange_molecules = new_exchange_molecules
            self.allowed_exchange_uptake = new_allowed_exchange_uptake

        # extract the states from the ports
        homeostatic_metabolite_counts = counts(
            states['bulk'], self.homeostatic_metabolite_idx)
        self.timestep = states['timestep']

        # TODO (Cyrus) - Implement kinetic model
        # kinetic_flux_targets = states['kinetic_flux_targets']
        # needed for kinetics
        current_catalyst_counts = counts(states['bulk'], self.catalyst_idx)
        translation_gtp = states['polypeptide_elongation']['gtp_to_hydrolyze']
        kinetic_enzyme_counts = counts(states['bulk'], self.kinetics_enzymes_idx) # kinetics related
        kinetic_substrate_counts = counts(states['bulk'], self.kinetics_substrates_idx)

        # cell mass difference for calculating GAM
        if self.cell_mass is not None:
            self.previous_mass = self.cell_mass
        self.cell_mass = states['listeners']['mass']['cell_mass'] * units.fg
        dry_mass = states['listeners']['mass']['dry_mass'] * units.fg

        cell_volume = self.cell_mass / self.cell_density
        # Coefficient to convert between flux (mol/g DCW/hr) basis
        # and concentration (M) basis
        conversion_coeff = (dry_mass / self.cell_mass * self.cell_density * self.timestep * units.s)
        self.counts_to_molar = (1 / (self.n_avogadro * cell_volume)).asUnit(CONC_UNITS)

        # maintenance target
        flux_ngam = (self.ngam * conversion_coeff)
        flux_gtp = (self.counts_to_molar * translation_gtp)

        total_ngam = flux_ngam + flux_gtp
        ngam_target = total_ngam.asNumber()

        # binary kinetic targets - sum up enzyme counts for each reaction
        reaction_catalyst_counts = self.catalysis_matrix.dot(current_catalyst_counts)
        # Get reaction indices whose fluxes should be set to zero
        # because there are no enzymes to catalyze the rxn
        binary_kinetic_idx = np.where(reaction_catalyst_counts == 0)[0]

        # TODO: Figure out how to handle changing media ID

        ## Determine updates to concentrations depending on the current state
        doubling_time = self.nutrient_to_doubling_time.get(
            states['environment']['media_id'],
            self.nutrient_to_doubling_time['minimal'])
        if self.include_ppgpp:
            # Sim does not include ppGpp regulation so do not update biomass
            # by RNA/protein ratio and include ppGpp concentration as a
            # homeostatic target
            conc_updates = self.getBiomassAsConcentrations(doubling_time)
            conc_updates[self.ppgpp_id] = self.getppGppConc(
                doubling_time).asUnit(CONC_UNITS)
        else:
            # Sim includes ppGpp regulation so update biomass based on
            # RNA/protein ratio which is controlled by ppGpp and other growth
            # regulation
            rp_ratio = states['listeners']['mass']['rna_mass'
                ] / states['listeners']['mass']['protein_mass']
            conc_updates = self.getBiomassAsConcentrations(
                doubling_time, rp_ratio=rp_ratio)

        if self.use_trna_charging:
            conc_updates.update(self.update_amino_acid_targets(
                self.counts_to_molar,
                states['polypeptide_elongation']['aa_count_diff'],
                dict(zip(self.aa_names,
                         counts(states['bulk_total'], self.aa_idx)))
            ))

        # Converted from units to make reproduction from listener data
        # accurate to model results (otherwise can have floating point diffs)
        conc_updates = {
            met: conc.asNumber(CONC_UNITS)
            for met, conc in conc_updates.items()}

        self.homeostatic_objective = {
            **self.homeostatic_objective, **conc_updates}

        homeostatic_concs = np.zeros(len(self.homeostatic_metabolite_idx))
        for i, met in enumerate(self.homeostatic_metabolites):
            homeostatic_concs[i] = self.homeostatic_objective[met]

        homeostatic_metabolite_concentrations = (homeostatic_metabolite_counts
            * self.counts_to_molar.asNumber())
        target_homeostatic_dmdt = (homeostatic_concs
            - homeostatic_metabolite_concentrations) / self.timestep

        aa_uptake_package = None
        if self.mechanistic_aa_transport:
            aa_in_media = np.array([
                states['boundary']['external'][aa_name
                    ].to('mM').magnitude > self.import_constraint_threshold
                for aa_name in self.aa_environment_names
            ])
            aa_in_media[self.removed_aa_uptake] = False
            exchange_rates = (states['polypeptide_elongation'][
                'aa_exchange_rates'] * timestep).asNumber(
                    CONC_UNITS / TIME_UNITS)
            aa_uptake_package = (exchange_rates[aa_in_media],
                                 self.aa_exchange_names[aa_in_media], True)

        # kinetic constraints
        # TODO (Cyrus) eventually collect isozymes in single reactions, map
        # enzymes to reacts via stoich instead of kinetic_constraint_reactions
        kinetic_enzyme_conc = self.counts_to_molar * kinetic_enzyme_counts
        kinetic_substrate_conc = self.counts_to_molar * kinetic_substrate_counts
        kinetic_constraints = self.get_kinetic_constraints(kinetic_enzyme_conc, kinetic_substrate_conc) # kinetic
        enzyme_kinetic_boundaries = ((self.timestep * units.s) * kinetic_constraints).asNumber(CONC_UNITS).astype(float)
        target_kinetic_values = enzyme_kinetic_boundaries[:, 1]
        target_kinetic_bounds = enzyme_kinetic_boundaries[:, [0, 2]]

        objective_weights = {
            'secretion': self.secretion_penalty_coeff,
            'efficiency': 0.0001,
            'kinetics': self.kinetic_objective_weight,
            'kinetics_in_range': self.kinetic_objective_weight_in_range}
        solution: FlowResult = self.network_flow_model.solve(
            homeostatic_concs=homeostatic_metabolite_concentrations,
            homeostatic_dm_targets=target_homeostatic_dmdt,
            ngam_target=ngam_target,
            kinetic_targets=enzyme_kinetic_boundaries,
            binary_kinetic_idx=binary_kinetic_idx,
            objective_weights=objective_weights,
            aa_uptake_package=aa_uptake_package)

        self.reaction_fluxes = solution.velocities
        self.metabolite_dmdt = solution.dm_dt
        self.metabolite_exchange = solution.exchanges
        self.maintenance_flux = solution.maintenance_flux


        # recalculate flux concentrations to counts
        estimated_reaction_fluxes = self.concentrationToCounts(self.reaction_fluxes)
        metabolite_dmdt_counts = self.concentrationToCounts(self.metabolite_dmdt)
        target_kinetic_flux = self.concentrationToCounts(target_kinetic_values)
        maintenance_flux = self.concentrationToCounts(self.maintenance_flux)
        target_homeostatic_dmdt = self.concentrationToCounts(target_homeostatic_dmdt)
        estimated_exchange_array = self.concentrationToCounts(self.metabolite_exchange)
        target_kinetic_bounds = self.concentrationToCounts(target_kinetic_bounds)

        estimated_homeostatic_dmdt = metabolite_dmdt_counts[self.network_flow_model.homeostatic_idx]
        # Ensure counts do not go negative
        final_metabolite_counts = np.fmax(homeostatic_metabolite_counts + estimated_homeostatic_dmdt, 0)
        estimated_homeostatic_dmdt = final_metabolite_counts - homeostatic_metabolite_counts
        estimated_intermediate_dmdt = metabolite_dmdt_counts[self.network_flow_model.intermediates_idx]
        estimated_exchange_dmdt = {str(metabolite[:-3]): -exchange
            for metabolite, exchange in zip(self.network_flow_model.mets,
            estimated_exchange_array) if metabolite in new_exchange_molecules}

        return {
            'bulk': [(self.homeostatic_metabolite_idx, estimated_homeostatic_dmdt)],
            'environment': {
                'exchange': estimated_exchange_dmdt  # changes to external metabolites
            },
            'listeners': {
                'fba_results': {
                    'media_id': states['environment']['media_id'],
                    'conc_updates': [conc_updates.get(m, 0)
                        for m in self.conc_update_molecules],
                    'estimated_fluxes': estimated_reaction_fluxes,
                    'estimated_homeostatic_dmdt': estimated_homeostatic_dmdt,
                    'target_homeostatic_dmdt': target_homeostatic_dmdt,
                    'target_kinetic_fluxes': target_kinetic_flux,
                    'target_kinetic_bounds': target_kinetic_bounds,
                    'estimated_exchange_dmdt': estimated_exchange_dmdt,
                    'estimated_intermediate_dmdt': estimated_intermediate_dmdt,
                    'maintenance_target': maintenance_flux,
                    'solution_fluxes': solution.velocities,
                    'solution_dmdt': solution.dm_dt,
                    'reaction_catalyst_counts': reaction_catalyst_counts,
                    'time_per_step': time.time(),
                    'base_reaction_fluxes': self.reaction_mapping_matrix.dot(
                        estimated_reaction_fluxes)
                }
            },
            'next_update_time': states['global_time'] + states['timestep']
        }

    def concentrationToCounts(self, concs):
        return np.rint(np.dot(concs, (CONC_UNITS /
            self.counts_to_molar * self.timestep).asNumber())).astype(int)

    def getBiomassAsConcentrations(self, doubling_time: Unum, 
                                   rp_ratio: float=None) -> dict[str, Unum]:
        """
        Caches the result of the sim_data function to improve performance since
        function requires computation but won't change for a given doubling_time.

        Args:
            doubling_time: doubling time of the cell to
                get the metabolite concentrations for

        Returns:
            Mapping from metabolite IDs to concentration targets
        """

        # TODO (Cyrus) Repeats code found in processes/metabolism.py Should think of a way to share.

        minutes = doubling_time.asNumber(units.min)  # hashable
        if (minutes, rp_ratio) not in self._biomass_concentrations:
            self._biomass_concentrations[(minutes, rp_ratio)] = self._getBiomassAsConcentrations(
                doubling_time, rp_ratio)

        return self._biomass_concentrations[(minutes, rp_ratio)]

    def update_amino_acid_targets(self, counts_to_molar: Unum, 
        count_diff: dict[str, float], amino_acid_counts: np.ndarray[float]
        ) -> dict[str, Unum]:
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
                    self.aa_targets[aa] = 1

        # First time step of a simulation so set target to current counts to
        # prevent concentration jumps between generations
        else:
            for aa, counts in amino_acid_counts.items():
                if aa in self.aa_targets_not_updated:
                    continue
                self.aa_targets[aa] = counts

        conc_updates = {aa: counts * counts_to_molar for aa,
                        counts in self.aa_targets.items()}

        # Update linked metabolites that will follow an amino acid
        for met, link in self.linked_metabolites.items():
            conc_updates[met] = conc_updates.get(
                link['lead'], 0 * counts_to_molar) * link['ratio']

        return conc_updates


@dataclass
class FlowResult:
    """Reaction velocities and dm/dt for an FBA solution, with metrics."""
    velocities: Iterable[float]
    dm_dt: Iterable[float]
    exchanges: Iterable[float]
    maintenance_flux: float
    objective: float


class NetworkFlowModel:
    # TODO Documentation
    """A network flow model for estimating fluxes in the metabolic network based on network structure. Flow is mainly
    driven by precursor demand (homeostatic objective) and availability of nutrients."""
    def __init__(self,
        stoich_arr: Iterable[dict],
        metabolites: Iterable[list],
        reactions: Iterable[list],
        homeostatic_metabolites: Iterable[str],
        kinetic_reactions: Iterable[str],
        get_mass: Callable[[str], Unum],
        gam: float = 0,
        active_constraints_mask: np.ndarray = None,
    ):
        self.S_orig = csr_matrix(stoich_arr.astype(np.int64))
        self.S_exch = None
        self.n_mets, self.n_orig_rxns = self.S_orig.shape
        self.mets = metabolites
        self.met_map = {metabolite: i for i, metabolite in enumerate(metabolites)}
        self.rxns = reactions
        self.rxn_map = {reaction: i for i, reaction in enumerate(reactions)}
        self.kinetic_rxn_idx = np.array([self.rxn_map[rxn] for rxn in kinetic_reactions]) if kinetic_reactions else None
        self.get_mass = get_mass
        self.gam = gam

        # steady state indices, secretion indices
        self.intermediates = list(set(self.mets) - set(homeostatic_metabolites))
        self.intermediates_idx = np.array([self.met_map[met] for met in self.intermediates])
        self.homeostatic_idx = np.array([self.met_map[met] for met in homeostatic_metabolites])
        # TODO (Cyrus) - use name provided
        self.maintenance_idx = self.rxn_map['maintenance_reaction'] if 'maintenance_reaction' in self.rxn_map else None

        self.active_constraints_mask = active_constraints_mask


    def set_up_exchanges(self,
        exchanges: Iterable[str],
        uptakes: Iterable[str]
    ):
        """Set up exchange reactions for the network flow model. Exchanges allow certain metabolites to have flow out of
        the system. Uptakes allow certain metabolites to also have flow into the system."""
        all_exchanges = exchanges.copy()
        all_exchanges.update(uptakes)

        # All exchanges can secrete but only uptakes go in both directions
        self.S_exch = np.zeros((self.n_mets, len(exchanges) + len(uptakes)))
        self.exchanges = []
        self.secretion_idx = []
        self.exchange_masses = []
        exch_idx = 0
        for met in all_exchanges:
            exch_name = met + " exchange"
            met_idx = self.met_map[met]
            exch_mass = self.get_mass(met).asNumber(MASS_UNITS / COUNTS_UNITS)
            if met in uptakes:
                self.S_exch[met_idx, exch_idx] = 1
                self.exchanges.append(exch_name)
                self.exchange_masses.append(exch_mass)
                exch_idx += 1
            self.exchanges.append(exch_name + " rev")
            self.secretion_idx.append(exch_idx)
            self.exchange_masses.append(-exch_mass)
            self.S_exch[met_idx, exch_idx] = -1
            exch_idx += 1

        self.S_exch = csr_matrix(self.S_exch)

        _, self.n_exch_rxns = self.S_exch.shape

        self.secretion_idx = np.array(self.secretion_idx, dtype=int)
        self.exchange_masses = np.array(self.exchange_masses)

    def solve(self,
        homeostatic_concs: Iterable[float] = None,
        homeostatic_dm_targets: Iterable[float] = None,
        ngam_target: float = 0,
        kinetic_targets: Iterable[float] = None,
        binary_kinetic_idx: Iterable[int] = None,
        objective_weights: Mapping[str, float] = None,
        aa_uptake_package: Mapping[str, float] = None,
        upper_flux_bound: float = 100,
        # ortools > 9.5 required for Python 3.11 but will only
        # get support in the 9/2023 release of cvxpy
        solver = cp.GLOP
    ) -> FlowResult:
        """Solve the network flow model for fluxes and dm/dt values."""
        # Convert to array
        homeostatic_concs = np.array(homeostatic_concs)
        homeostatic_dm_targets = np.array(homeostatic_dm_targets)
        target_fluxes = np.zeros(self.n_orig_rxns)
        if kinetic_targets is not None:
            target_fluxes[self.kinetic_rxn_idx] += kinetic_targets[:, 1]

        # set up variables
        v_diff_in_range = cp.Variable(self.n_orig_rxns)
        v_diff_outside_range = cp.Variable(self.n_orig_rxns)
        v = target_fluxes + v_diff_in_range + v_diff_outside_range
        e = cp.Variable(self.n_exch_rxns)
        dm = self.S_orig @ v + self.S_exch @ e
        exch = self.S_exch @ e

        total_maintenance = ngam_target + self.gam * e @ self.exchange_masses

        constr = []
        constr.append(dm[self.intermediates_idx] == 0)

        if self.maintenance_idx is not None:
            constr.append(v[self.maintenance_idx] == total_maintenance)
            constr.append(v[self.maintenance_idx] >= ngam_target)
        # If enzymes not present, constrain rxn flux to 0
        if binary_kinetic_idx is not None:
            if len(binary_kinetic_idx) > 0:
                constr.append(v[binary_kinetic_idx] == 0)

        constr.extend([v >= 0, v <= upper_flux_bound, e >= 0, e <= upper_flux_bound])

        if aa_uptake_package:
            levels, molecules, force = aa_uptake_package
            for level, mol in zip(levels, molecules):
                exch_idx = self.exchanges.index(mol + " exchange")
                constr.append(e[exch_idx] == level)

        # Calculate target concs (current + delta) for denominator of objective
        # (target conc - actual conc) / (target conc) is the same as
        # (target delta - actual delta) / (target conc)
        homeostatic_target_concs = homeostatic_concs + homeostatic_dm_targets
        # Fix divide by zero
        homeostatic_target_concs[homeostatic_target_concs==0] = 1

        loss = 0
        loss += cp.norm1((dm[self.homeostatic_idx] - homeostatic_dm_targets)
                         / homeostatic_target_concs)
        if 'secretion' in objective_weights:
            loss += objective_weights['secretion'] * cp.sum(e[
                self.secretion_idx] @ -self.exchange_masses[self.secretion_idx])
        if 'kinetics' in objective_weights:
            # Fix divide by zero
            nonzero_kinetic_targets = kinetic_targets[:, 1].copy()
            nonzero_kinetic_targets[nonzero_kinetic_targets==0] = 1
            # Calculate lower and upper limit for flux diff
            lower_flux_diff = kinetic_targets[:, 0] - kinetic_targets[:, 1]
            upper_flux_diff = kinetic_targets[:, 2] - kinetic_targets[:, 1]
            constr.extend([v_diff_in_range[self.kinetic_rxn_idx] >= lower_flux_diff,
                v_diff_in_range[self.kinetic_rxn_idx] <= upper_flux_diff])
            # Heavily weight fluxes outside limits
            loss += objective_weights['kinetics'] * cp.norm1((
                v_diff_outside_range[self.kinetic_rxn_idx]
                 / nonzero_kinetic_targets)[self.active_constraints_mask])
            # Lightly weight fluxes in expected range
            loss += objective_weights['kinetics'] * \
                objective_weights['kinetics_in_range'] * cp.norm1((
                v_diff_in_range[self.kinetic_rxn_idx]
                 / nonzero_kinetic_targets)[self.active_constraints_mask])

        p = cp.Problem(
            cp.Minimize(loss),
            constr
        )

        p.solve(solver=solver, verbose=False)
        if p.status != "optimal":
            raise ValueError("Network flow model of metabolism did not "
                "converge to an optimal solution.")

        velocities = np.array(v.value)
        dm_dt = np.array(dm.value)
        exchanges = np.array(exch.value)
        maintenance_flux = total_maintenance.value
        objective = p.value

        return FlowResult(velocities=velocities,
                          dm_dt=dm_dt,
                          exchanges=exchanges,
                          maintenance_flux=maintenance_flux,
                          objective=objective)

def test_network_flow_model():
    """Test the network flow model on a simple example, using only the homeostatic objective along with secretion and
    efficiency penalties."""

    S_matrix = np.array([[-1, 1, 0], [0, -1, 1], [1, 0, -1]]).T

    metabolites = ["A", "B", "C"]
    reactions = ["r1", "r2", "r3"]
    homeostatic_metabolites = {"C": 1}

    exchanges = {"A"}
    uptakes = {"A"}

    model = NetworkFlowModel(stoich_arr=S_matrix, reactions=reactions,
                             metabolites=metabolites, homeostatic_metabolites=list(homeostatic_metabolites.keys()),
                             kinetic_reactions=None, get_mass=lambda _: 1 * units.g/units.mol)

    model.set_up_exchanges(exchanges=exchanges, uptakes=uptakes)

    solution: FlowResult = model.solve(
        homeostatic_concs=list(homeostatic_metabolites.values()),
        homeostatic_dm_targets=list(homeostatic_metabolites.values()),
        objective_weights={'secretion': 0.01, 'efficiency': 0.0001},
        upper_flux_bound=100)

    assert np.isclose(solution.velocities, np.array([1, 1, 0])).all() == True, "Network flow toy model did not converge to correct solution."

# TODO (Cyrus) Add test for entire process

if __name__ == '__main__':
    test_network_flow_model()
