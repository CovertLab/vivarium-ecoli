"""
MetabolismRedux
"""

import numpy as np
import time
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

COUNTS_UNITS = units.mmol
VOLUME_UNITS = units.L
MASS_UNITS = units.g
TIME_UNITS = units.s
CONC_UNITS = COUNTS_UNITS / VOLUME_UNITS
CONVERSION_UNITS = MASS_UNITS * TIME_UNITS / VOLUME_UNITS
GDCW_BASIS = units.mmol / units.g / units.h


NAME = 'ecoli-metabolism-redux-classic'
TOPOLOGY = topology_registry.access('ecoli-metabolism')
# TODO (Cyrus) - Re-add when kinetics are added.
# TOPOLOGY['kinetic_flux_targets'] = ('rates', 'fluxes')
topology_registry.register(NAME, TOPOLOGY)

# TODO (Cyrus) - Remove when have a better way to handle these rxns.
# ParCa mistakes in carbon gen, efflux/influx proton gen, mass gen
BAD_RXNS = ["RXN-12440", "TRANS-RXN-121", "TRANS-RXN-300",
            "TRANS-RXN-8", "R15-RXN-MET/CPD-479//CPD-479/MET.25.", 'TRANS-RXN-218',
            "TRANS-RXN0-601-PROTON//PROTON.15. (reverse)"]

FREE_RXNS = ["TRANS-RXN-145", "TRANS-RXN0-545", "TRANS-RXN0-474"]

class MetabolismReduxClassic(Step):
    name = NAME
    topology = TOPOLOGY

    defaults = {
        'stoichiometry': [],
        'reaction_catalysts': [],
        'catalyst_ids': [],
        # TODO (Cyrus) -- get these passed in, subset of the stoichimetry
        'kinetic_rates': [],
        'media_id': 'minimal',
        'objective_type': 'homeostatic',
        'cell_density': 1100 * units.g / units.L,
        'concentration_updates': None,
        'maintenance_reaction': {},
    }

    def __init__(self, parameters):
        super().__init__(parameters)

        stoich_dict = dict(sorted(self.parameters['stoich_dict'].items()))
        for rxn in BAD_RXNS:
            stoich_dict[rxn] = {}
        # Add maintenance reaction
        stoich_dict['maintenance_reaction'] = self.parameters['maintenance_reaction']

        # Get all metabolite names
        self.metabolite_names = set()
        for reaction, stoich in stoich_dict.items():
            self.metabolite_names.update(stoich.keys())

        self.metabolite_names = sorted(list(self.metabolite_names))
        self.reaction_names = list(stoich_dict.keys())
        n_metabolites = len(self.metabolite_names)
        n_reactions = len(self.reaction_names)

        metabolites_idx = {species: i for i, species in enumerate(self.metabolite_names)}

        # Convert stoichiometric dictionary to array
        self.stoichiometry = np.zeros((n_metabolites, n_reactions), dtype=np.int8)

        # Get indices of catalysts for each reaction
        reaction_catalysts = self.parameters['reaction_catalysts']
        self.catalyst_ids = self.parameters['catalyst_ids']
        catalyst_idx = {catalyst: i for i, catalyst in enumerate(self.catalyst_ids)}

        self.catalyzed_rxn_enzymes_idx = []

        # Create stoichiometry matrix and enzyme catalyzed reaction index
        for col_idx, (reaction, stoich) in enumerate(stoich_dict.items()):
            for species, coefficient in stoich.items():
                i = metabolites_idx[species]
                self.stoichiometry[i, col_idx] = coefficient

            enzyme_idx = [catalyst_idx[catalyst] for catalyst in reaction_catalysts.get(reaction, [])]
            self.catalyzed_rxn_enzymes_idx.append(enzyme_idx)

        self.media_id = self.parameters['media_id']
        self.cell_density = self.parameters['cell_density']
        self.nAvogadro = self.parameters['avogadro']
        self.ngam = self.parameters['non_growth_associated_maintenance']
        self.gam = self.parameters['dark_atp'] * self.parameters['cell_dry_mass_fraction']

        # new variables for the model
        self.cell_mass = None
        self.previous_mass = None
        self.reaction_fluxes = None

        # methods from config
        self._biomass_concentrations = {}  # type: dict
        self._getBiomassAsConcentrations = self.parameters['get_biomass_as_concentrations']
        self.concentration_updates = self.parameters['concentration_updates']
        self.exchange_constraints = self.parameters['exchange_constraints']
        self.get_kinetic_constraints = self.parameters['get_kinetic_constraints']
        self.kinetic_constraint_reactions = self.parameters['kinetic_constraint_reactions']
        self.nutrient_to_doubling_time = self.parameters['nutrient_to_doubling_time']

        # retrieve exchanged molecules
        self.exchange_molecules = set()
        exchanges = parameters['exchange_data_from_media'](self.media_id)
        self.exchange_molecules.update(exchanges['externalExchangeMolecules'])

        # retrieve conc dict and get homeostatic objective.
        conc_dict = self.concentration_updates.concentrations_based_on_nutrients(self.media_id)
        doubling_time = parameters['doubling_time']
        conc_dict.update(self.getBiomassAsConcentrations(doubling_time))
        
        # Separate homeostatic objective into metabolite and conc arrays
        self.homeostatic_metabolites = np.array(list(conc_dict.keys()))
        self.homeostatic_concs = np.array([conc.asNumber(CONC_UNITS) for conc in conc_dict.values()])

        # Network flow initialization
        self.network_flow_model = NetworkFlowModel(
            stoich_arr=self.stoichiometry,
            metabolites=self.metabolite_names,
            reactions=self.reaction_names,
            homeostatic_metabolites=self.homeostatic_metabolites,
            kinetic_reactions=self.kinetic_constraint_reactions,
            free_reactions=FREE_RXNS)

        # important bulk molecule names
        self.catalyst_ids = self.parameters['catalyst_ids']
        self.aa_names = self.parameters['aa_names']
        self.kinetic_constraint_enzymes = self.parameters['kinetic_constraint_enzymes']
        self.kinetic_constraint_substrates = self.parameters['kinetic_constraint_substrates']

        # Helper indices for Numpy indexing
        self.homeostatic_metabolite_idx = None

        # Cache uptake parameters from previous timestep
        self.allowed_exchange_uptake = None

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
                    for element in self.exchange_molecules
                },
                # can probably remove, identical to exchanges except tuple.
                'exchange_data': {
                    'unconstrained': {'_default': []},
                    'constrained': {'_default': []}}},

            'polypeptide_elongation': {
                'aa_count_diff': {
                    '_default': {},
                    '_emit': True,
                    '_divider': 'empty_dict'},
                'gtp_to_hydrolyze': {
                    '_default': 0,
                    '_emit': True,
                    '_divider': 'zero'}
            },

            'listeners': {
                'mass': listener_schema({
                    'cell_mass': 0.0,
                    'dry_mass': 0.0}),

                # TODO: Not empty list default
                'fba_results': listener_schema({
                    'solution_fluxes': [],
                    'solution_dmdt': [],
                    'solution_residuals': [],
                    'time_per_step': 0.0,
                    'estimated_fluxes': [],
                    'estimated_homeostatic_dmdt': [],
                    'target_homeostatic_dmdt': [],
                    'estimated_exchange_dmdt': {},
                    'estimated_intermediate_dmdt': [],
                    'target_kinetic_fluxes': [],
                    'target_kinetic_bounds': [],
                    'reaction_catalyst_counts': [],
                    'maintenance_target': 0
                }),

                'enzyme_kinetics': listener_schema({
                    'metabolite_counts_init': 0,
                    'metabolite_counts_final': 0,
                    'enzyme_counts_init': 0,
                    'counts_to_molar': 1.0,
                    'actual_fluxes': [],
                    'target_fluxes': [],
                    'target_fluxes_upper': [],
                    'target_fluxes_lower': []})
            },

            # these three were added in parca update, may be able to remove
            'boundary': {
                'external': {
                    '*': {'_default': 0 * vivunits.mM}
                }
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
            self.bulk_ids = states['bulk']['id']
            self.homeostatic_metabolite_idx = bulk_name_to_idx(self.homeostatic_metabolites, self.bulk_ids)
            self.catalyst_idx = bulk_name_to_idx(self.catalyst_ids, self.bulk_ids)
            self.kinetics_enzymes_idx = bulk_name_to_idx(self.kinetic_constraint_enzymes, self.bulk_ids)
            self.kinetics_substrates_idx = bulk_name_to_idx(self.kinetic_constraint_substrates, self.bulk_ids)

        # metabolites not in either set are constrained to zero uptake.
        exchange_data = states['environment']['exchange_data']
        unconstrained_uptake = exchange_data['unconstrained']
        constrained_uptake =  exchange_data['constrained']
        
        new_allowed_exchange_uptake = set(unconstrained_uptake).union(constrained_uptake.keys())
        new_exchange_molecules = set(self.exchange_molecules).union(set(new_allowed_exchange_uptake))

        # set up network flow model exchanges and uptakes
        if (new_exchange_molecules != self.exchange_molecules) or (
            new_allowed_exchange_uptake != self.allowed_exchange_uptake
        ):
            self.network_flow_model.set_up_exchanges(new_exchange_molecules, new_allowed_exchange_uptake)
            self.exchange_molecules = new_exchange_molecules
            self.allowed_exchange_uptake = new_allowed_exchange_uptake

        # extract the states from the ports
        homeostatic_metabolite_counts = counts(states['bulk'], self.homeostatic_metabolite_idx)
        self.timestep = self.calculate_timestep(states)

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
        self.counts_to_molar = (1 / (self.nAvogadro * cell_volume)).asUnit(CONC_UNITS)

        # maintenance target
        if self.previous_mass is not None:
            flux_gam = self.gam * (self.cell_mass - self.previous_mass) / VOLUME_UNITS
        else:
            flux_gam = 0 * CONC_UNITS
        flux_ngam = (self.ngam * conversion_coeff)
        flux_gtp = (self.counts_to_molar * translation_gtp)

        total_maintenance = flux_gam + flux_ngam + flux_gtp
        maintenance_target = total_maintenance.asNumber()

        # binary kinetic targets - sum up enzyme counts for each reaction. -1 means missing catalyst.
        reaction_catalyst_counts = np.array([sum([current_catalyst_counts[enzyme_idx]
                                                  for enzyme_idx in enzymes_idx])
                                             if len(enzymes_idx) > 0 else -1
                                             for enzymes_idx in self.catalyzed_rxn_enzymes_idx])
        # Get reaction indices whose fluxes should be set to zero
        # because there are no enzymes to catalyze the rxn
        binary_kinetic_idx = np.where(~reaction_catalyst_counts.astype(np.bool_))
        
        # TODO: Figure out how to handle changing media ID
        
        homeostatic_metabolite_concentrations = (homeostatic_metabolite_counts * self.counts_to_molar.asNumber())
        target_homeostatic_dmdt = (self.homeostatic_concs - homeostatic_metabolite_concentrations) / self.timestep

        # kinetic constraints 
        # TODO (Cyrus) eventually collect isozymes in single reactions, map
        # enzymes to reacts via stoich instead of kinetic_constraint_reactions
        kinetic_enzyme_conc = self.counts_to_molar * kinetic_enzyme_counts
        kinetic_substrate_conc = self.counts_to_molar * kinetic_substrate_counts
        kinetic_constraints = self.get_kinetic_constraints(kinetic_enzyme_conc, kinetic_substrate_conc) # kinetic
        enzyme_kinetic_boundaries = ((self.timestep * units.s) * kinetic_constraints).asNumber(CONC_UNITS).astype(float)
        target_kinetic_values = enzyme_kinetic_boundaries[:, 1]
        target_kinetic_bounds = enzyme_kinetic_boundaries[:, [0, 2]]

        # TODO (Cyrus) solve network flow problem to get fluxes
        objective_weights = {'secretion': 0.01, 'efficiency': 0.000001, 'kinetics': 0.0000001}
        solution: FlowResult = self.network_flow_model.solve(
            homeostatic_targets=target_homeostatic_dmdt,
            maintenance_target=maintenance_target,
            kinetic_targets=target_kinetic_values,
            binary_kinetic_idx=binary_kinetic_idx,
            objective_weights=objective_weights,
            solver=cp.GLOP)

        self.reaction_fluxes = solution.velocities
        self.metabolite_dmdt = solution.dm_dt
        self.metabolite_exchange = solution.exchanges


        # recalculate flux concentrations to counts
        estimated_reaction_fluxes = self.concentrationToCounts(self.reaction_fluxes)
        metabolite_dmdt_counts = self.concentrationToCounts(self.metabolite_dmdt)
        target_kinetic_flux = self.concentrationToCounts(target_kinetic_values)
        target_maintenance_flux = self.concentrationToCounts(maintenance_target)
        target_homeostatic_dmdt = self.concentrationToCounts(target_homeostatic_dmdt)
        estimated_exchange_array = self.concentrationToCounts(self.metabolite_exchange)
        target_kinetic_bounds = self.concentrationToCounts(target_kinetic_bounds)

        estimated_homeostatic_dmdt = metabolite_dmdt_counts[self.network_flow_model.homeostatic_idx]
        estimated_intermediate_dmdt = metabolite_dmdt_counts[self.network_flow_model.intermediates_idx]
        estimated_exchange_dmdt = {metabolite: exchange
            for metabolite, exchange in zip(self.network_flow_model.mets,
            estimated_exchange_array) if metabolite in new_exchange_molecules}

        return {
            'bulk': [(self.homeostatic_metabolite_idx, estimated_homeostatic_dmdt)],
            'environment': {
                'exchanges': estimated_exchange_dmdt  # changes to external metabolites
            },
            'listeners': {
                'fba_results': {
                    'estimated_fluxes': estimated_reaction_fluxes,
                    'estimated_homeostatic_dmdt': estimated_homeostatic_dmdt,
                    'target_homeostatic_dmdt': target_homeostatic_dmdt,
                    'target_kinetic_fluxes': target_kinetic_flux,
                    'target_kinetic_bounds': target_kinetic_bounds,
                    'estimated_exchange_dmdt': estimated_exchange_dmdt,
                    'estimated_intermediate_dmdt': estimated_intermediate_dmdt,
                    'maintenance_target': target_maintenance_flux,
                    'solution_fluxes': solution.velocities,
                    'solution_dmdt': solution.dm_dt,
                    'reaction_catalyst_counts': reaction_catalyst_counts,
                    'time_per_step': time.time()
                }
            },
            'next_update_time': states['global_time'] + states['timestep'],
        }

    def concentrationToCounts(self, concs):
        return np.rint(np.dot(concs, (CONC_UNITS /
            self.counts_to_molar * self.timestep).asNumber())).astype(int)

    def getBiomassAsConcentrations(self, doubling_time: Unum):
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
        if minutes not in self._biomass_concentrations:
            self._biomass_concentrations[minutes] = self._getBiomassAsConcentrations(doubling_time)

        return self._biomass_concentrations[minutes]


@dataclass
class FlowResult:
    """Reaction velocities and dm/dt for an FBA solution, with metrics."""
    velocities: Iterable[float]
    dm_dt: Iterable[float]
    exchanges: Iterable[float]
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
                 free_reactions: Iterable[str] = None, # TODO Use free reactions
    ):

        self.S_orig = csr_matrix(stoich_arr.astype(np.int64))
        self.S_exch = None
        self.n_mets, self.n_orig_rxns = self.S_orig.shape
        self.mets = metabolites
        self.met_map = {metabolite: i for i, metabolite in enumerate(metabolites)}
        self.rxns = reactions
        self.rxn_map = {reaction: i for i, reaction in enumerate(reactions)}
        self.kinetic_rxn_idx = np.array([self.rxn_map[rxn] for rxn in kinetic_reactions]) if kinetic_reactions else None

        # steady state indices, secretion indices
        self.intermediates = list(set(self.mets) - set(homeostatic_metabolites))
        self.intermediates_idx = np.array([self.met_map[met] for met in self.intermediates])
        self.homeostatic_idx = np.array([self.met_map[met] for met in homeostatic_metabolites])
        # TODO (Cyrus) - use name provided
        self.maintenance_idx = self.rxn_map['maintenance_reaction'] if 'maintenance_reaction' in self.rxn_map else None


    def set_up_exchanges(self,
        exchanges: Iterable[str],
        uptakes: Iterable[str]
    ):
        """Set up exchange reactions for the network flow model. Exchanges allow certain metabolites to have flow out of
        the system. Uptakes allow certain metabolites to also have flow into the system."""
        all_exchanges = exchanges.copy()
        all_exchanges.update(uptakes)
        
        # All exchanges can secrete but only uptakes go in both directions
        self.S_exch = np.zeros((self.n_mets, len(all_exchanges) + len(uptakes)))
        self.exchanges = []
        self.secretion_idx = []
        exch_idx = 0
        for met in all_exchanges:
            exch_name = met + " exchange"
            met_idx = self.met_map[met]
            if met in uptakes:
                self.S_exch[met_idx, exch_idx] = 1
                self.exchanges.append(exch_name)
                exch_idx += 1
            self.exchanges.append(exch_name + " rev")
            self.secretion_idx.append(exch_idx)
            self.S_exch[met_idx, exch_idx] = -1
            exch_idx += 1

        self.S_exch = csr_matrix(self.S_exch)

        _, self.n_exch_rxns = self.S_exch.shape

        self.secretion_idx = np.array(self.secretion_idx, dtype=int)

    def solve(self,
        homeostatic_targets: Iterable[float] = None,
        maintenance_target: float = 0,
        kinetic_targets: Iterable[float] = None,
        binary_kinetic_idx: Iterable[int] = None,
        objective_weights: Mapping[str, float] = None,
        upper_flux_bound: float = 100,
        solver = cp.GLOP
    ) -> FlowResult:
        """Solve the network flow model for fluxes and dm/dt values."""
        # set up variables
        v = cp.Variable(self.n_orig_rxns)
        e = cp.Variable(self.n_exch_rxns)
        dm = self.S_orig @ v + self.S_exch @ e
        exch = self.S_exch @ e

        constr = []
        constr.append(dm[self.intermediates_idx] == 0)

        if self.maintenance_idx is not None:
            constr.append(v[self.maintenance_idx] == maintenance_target)
        # If enzymes not present, constrain rxn flux to 0
        if binary_kinetic_idx:
            constr.append(v[binary_kinetic_idx] == 0)

        constr.extend([v >= 0, v <= upper_flux_bound, e >= 0, e <= upper_flux_bound])

        loss = 0
        loss += cp.norm1(dm[self.homeostatic_idx] - homeostatic_targets)
        loss += objective_weights['secretion'] * (cp.sum(e[self.secretion_idx])) if 'secretion' in objective_weights else loss
        loss += objective_weights['efficiency'] * (cp.sum(v)) if 'efficiency' in objective_weights else loss
        loss = loss + objective_weights['kinetics'] * cp.norm1(v[self.kinetic_rxn_idx] - kinetic_targets) if 'kinetics' in objective_weights else loss

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
        objective = p.value

        return FlowResult(velocities=velocities,
                          dm_dt=dm_dt,
                          exchanges=exchanges,
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
                             kinetic_reactions=None)

    model.set_up_exchanges(exchanges=exchanges, uptakes=uptakes)

    solution: FlowResult = model.solve(homeostatic_targets=list(homeostatic_metabolites.values()),
                                       objective_weights={'secretion': 0.01, 'efficiency': 0.0001},
                                       upper_flux_bound=100, solver=cp.GLOP)

    assert np.isclose(solution.velocities, np.array([1, 1, 0])).all() == True, "Network flow toy model did not converge to correct solution."

# TODO (Cyrus) Add test for entire process

if __name__ == '__main__':
    test_network_flow_model()
