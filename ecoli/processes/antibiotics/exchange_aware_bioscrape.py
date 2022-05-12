import copy
import tempfile

from biocrnpyler import (
    ChemicalReactionNetwork,
    Reaction,
    Species,
    ParameterEntry,
    GeneralPropensity,
)
from bioscrape.simulator import (
    ModelCSimInterface,
    DeterministicSimulator,
)
from bioscrape.types import Model
import numpy as np
from scipy.constants import N_A
from scipy.integrate import odeint
from scipy.linalg import lstsq, norm
from scipy.optimize import root, minimize
from vivarium.core.process import Process
from vivarium.library.units import units, Quantity


AVOGADRO = N_A / units.mol


def _transform_dict_leaves(root, transform_func):
    if not isinstance(root, dict):
        # We are at a leaf node, so apply transformation function.
        return transform_func(root)
    # We are not at a leaf node, so recurse.
    transformed = {
        key: _transform_dict_leaves(value, transform_func)
        for key, value in root.items()
    }
    return transformed


def test_transform_dict_leaves():
    root = {
        'a': {
            'b': 1,
        },
        'c': 2,
    }
    transformed = _transform_dict_leaves(root, lambda x: x + 1)
    expected = {
        'a': {
            'b': 2,
        },
        'c': 3,
    }
    assert transformed == expected


def species_array_to_dict(array, species_to_index):
    '''Convert an array of values to a map from name to value index.

    >>> array = [1, 5, 2]
    >>> species_to_index = {
    ...     'C': 2,
    ...     'A': 0,
    ...     'B': 1,
    ... }
    >>> species_array_to_dict(array, species_to_index)
    {'C': 2, 'A': 1, 'B': 5}

    Args:
        array: The array of values. Must be subscriptable with indices.
        species_to_index: Dictionary mapping from names to the index in
            ``array`` of the value associated with each name. The values
            of the dictionary must be exactly the indices of ``array``
            with no duplicates.

    Returns:
        A dictionary mapping from names (the keys of
        ``species_to_index``) to the associated values in ``array``.
    '''
    return {
        species: array[index]
        for species, index in species_to_index.items()
    }


def get_delta(before, after):
    '''Calculate the differences between the values of two dictionaries.

    >>> before = {'a': 2, 'b': -5}
    >>> after = {'b': 1, 'a': 3}
    >>> get_delta(before, after)
    {'a': 1, 'b': 6}

    Args:
        before: One of the dictionaries.
        after: The other dictionary.

    Returns:
        A dictionary with the same keys as ``before`` and ``after``
        where for each key ``k``, the value is equal to ``after[k] -
        before[k]``.

    Raises:
        AssertionError: If ``before`` and ``after`` do not have the same
            keys.
    '''
    assert before.keys() == after.keys()
    return {
        key: after[key] - before_value
        for key, before_value in before.items()
    }


class ExchangeAwareBioscrape(Process):
    name = 'exchange-aware-bioscrape'
    defaults = {
        'sbml_file_path': '',
        'external_species': tuple(),
        'species_to_convert_to_counts': {},
        'name_map': tuple(),
        'units_map': {},
        'species_units_conc': units.mM,
        'species_units_count': units.mmol,
        'equilibrium_species': tuple(),
    }

    def __init__(self, parameters=None):
        '''Sets the equilibrium state for a bioscrape SBML model.

        Parameters:

        * ``sbml_file_path``: Path to the SBML file to use.
        * ``external_species``: Iterable of species names that exist in
          the external environment. These species will be expected in
          the ``external`` port, and their updates will be communicated
          through the ``exchanges`` port. Species should be listed by
          their Vivarium name, not their bioscrape name, if the two
          names are different (see ``name_map`` below).
        * ``species_to_convert_to_counts``: Mapping from species names
          that are represented as concentrations in Vivarium but should
          be passed to bioscrape as counts to the name of the variable
          in ``rates`` that holds the volume to use for the conversion.
          Species should be listed by their bioscrape name, not their
          Vivarium name, if the two names are different (see
          ``name_map`` below).
        * ``name_map``: Iterable of tuples ``(vivarium, bioscrape)``
          where ``vivarium`` is the path of the species in the state
          provided to ``next_update()`` by Vivarium, and ``bioscrape``
          is the name of the molecule to be used internally for
          bioscrape. This conversion is needed because Vivarium supports
          a larger set of variable names than bioscrape. See
          https://github.com/biocircuits/bioscrape/wiki/BioSCRAPE-XML
          for the bioscrape naming rules.
        * ``units_map``: Dictionary reflecting the nested port
          structure, with variables mapped to the expected unit. The
          conversion to these units happens before units are stripped
          and the magnitudes are passed to Bioscrape.
        * ``equilibrium_species``: Sequence of species that should
          essentially reach equilibrium in every timestep. The process
          works by determining what fluxes are needed to get these
          species to equilibrium then using those fluxes to determine
          the updates for species that don't reach equilibrium. These
          should be specified by their bioscrape names.
        '''
        super().__init__(parameters)

        self.model = Model(sbml_filename=self.parameters['sbml_file_path'])
        self.interface = ModelCSimInterface(self.model)
        self.interface.py_prep_deterministic_simulation()
        self.simulator = DeterministicSimulator()

        self.rename_vivarium_to_bioscrape = {}
        for path, new_name in self.parameters['name_map']:
            # Paths must be tuples so that they are hashable.
            path = tuple(path)
            self.rename_vivarium_to_bioscrape[path] = new_name
            if 'species' in path:
                delta_path = tuple(
                    item if item != 'species' else 'delta_species'
                    for item in path
                )
                self.rename_vivarium_to_bioscrape[
                    delta_path] = new_name
            if 'external' in path:
                external_path = tuple(
                    item if item != 'external' else 'exchanges'
                    for item in path
                )
                self.rename_vivarium_to_bioscrape[
                    external_path] = new_name

        self.rename_bioscrape_to_vivarium = self._invert_map(
            self.rename_vivarium_to_bioscrape)

        self.external_species_bioscrape = [
            self.rename_vivarium_to_bioscrape.get(
                ('external', species), species)
            for species in self.parameters['external_species']
        ]

    @staticmethod
    def _invert_map(rename_map):
        '''Invert a renaming map.

        For example:

        >>> renaming_map = {
        ...     ('a', 'b'): 'B',
        ...     ('a', 'c'): 'C'
        ... }
        >>> inverted_map = {
        ...     ('a', 'B'): 'b',
        ...     ('a', 'C'): 'c'
        ... }
        >>> inverted_map == ExchangeAwareBioscrape._invert_map(
        ...     renaming_map)
        True
        '''
        inverted_map = {
            path[:-1] + (new_name,): path[-1]
            for path, new_name in rename_map.items()
        }
        return inverted_map

    def initial_state(self, config=None):
        initial_state = {
            'species': species_array_to_dict(
                self.model.get_species_array(),
                self.model.get_species2index(),
            )
        }
        for k in initial_state['species'].keys():
            initial_state['species'][k] *= self.parameters[
                'species_units_conc']
        return initial_state

    def ports_schema(self):
        schema = {
            'species': {
                species: {
                    '_default': 0.0 * self.parameters[
                        'species_units_conc'],
                    '_updater': 'accumulate',
                    '_emit': True,
                    '_divider': 'set',
                }
                for species in self.model.get_species()
                if species not in self.external_species_bioscrape
                if not (
                    species.endswith('_delta')
                    and species[:-len('_delta')]
                    in self.external_species_bioscrape)
            },
            'delta_species': {
                species: {
                    '_default': 0.0 * self.parameters[
                        'species_units_conc'],
                    '_updater': 'set',
                    '_emit': True,
                }
                for species in self.model.get_species()
                if species not in self.external_species_bioscrape
                if not (
                    species.endswith('_delta')
                    and species[:-len('_delta')]
                    in self.external_species_bioscrape)
            },
            'exchanges': {
                species: {
                    '_default': 0,
                    '_emit': True,
                }
                for species in self.external_species_bioscrape
            },
            'external': {
                species: {
                    '_default': 0 * self.parameters[
                        'species_units_conc'],
                    '_emit': True,
                }
                for species in self.external_species_bioscrape
            },
            'rates': {
                p: {
                    '_default': self.model.get_parameter_dictionary()[p],
                    '_updater': 'set',
                }
                for p in self.model.get_param_list()
            },
        }

        rename_schema_for_vivarium = {}
        for path, new_name in self.rename_bioscrape_to_vivarium.items():
            rename_schema_for_vivarium[path] = new_name
            if 'exchanges' in path:
                for store in ('delta_species', 'species'):
                    other_path = tuple(
                        item if item != 'exchanges' else store
                        for item in path
                    )
                    rename_schema_for_vivarium[other_path] = new_name

        # Apply units map.
        units_map_for_schema = _transform_dict_leaves(
            self.parameters['units_map'], lambda x: {'_default': x})
        schema = self._add_units(schema, units_map_for_schema)

        schema = self._rename_variables(
            schema,
            rename_schema_for_vivarium,
        )
        return schema

    def _remove_units(self, state, units_map=None):
        units_map = units_map or {}
        converted_state = {}
        saved_units = {}
        for key, value in state.items():
            if isinstance(value, dict):
                value_no_units, new_saved_units = self._remove_units(
                    value, units_map=units_map.get(key)
                )
                converted_state[key] = value_no_units
                saved_units[key] = new_saved_units
            elif isinstance(value, Quantity):
                saved_units[key] = value.units
                expected_units = units_map.get(key)
                if expected_units:
                    value_no_units = value.to(expected_units).magnitude
                else:
                    value_no_units = value.magnitude
                converted_state[key] = value_no_units
            else:
                assert not units_map.get(key), f'{key} does not have units'
                converted_state[key] = value

        return converted_state, saved_units

    def _add_units(self, state, saved_units):
        """add units back in"""
        unit_state = state.copy()
        for key, value in saved_units.items():
            before = unit_state[key]
            if isinstance(value, dict):
                unit_state[key] = self._add_units(before, value)
            else:
                unit_state[key] = before * value
        return unit_state

    @staticmethod
    def _rename_variables(state, rename_map, path=tuple()):
        '''Rename variables in a hierarchy dict per a renaming map.

        For example:

        >>> renaming_map = {
        ...     ('a', 'b'): 'B',
        ...     ('a', 'c'): 'C'
        ... }
        >>> state = {
        ...     'a': {
        ...         'b': 1,
        ...         'c': 2,
        ...     }
        ... }
        >>> renamed_state = {
        ...     'a': {
        ...         'B': 1,
        ...         'C': 2,
        ...     }
        ... }
        >>> renamed_state == ExchangeAwareBioscrape._rename_variables(
        ...     state, renaming_map)
        True

        Args:
            state: The hierarchy dict of variables to rename.
            rename_map: The renaming map.
            path: If ``state`` is a sub-dict, the path to the sub-dict.
                Only used for recursive calls.

        Returns:
            The renamed hierarchy dict.
        '''
        # Base Case
        if not isinstance(state, dict):
            return state

        # Recursive Case
        new_state = {}
        for key, value in state.items():
            cur_path = path + (key,)
            new_key = rename_map.get(cur_path, cur_path[-1])
            new_state[new_key] = (
                ExchangeAwareBioscrape._rename_variables(
                    value, rename_map, cur_path
                )
            )

        return new_state

    def _integrate_reactions(self):
        pass

    @staticmethod
    def _compute_stoichiometry_loss(
            reactions, derivatives, species_to_index):
        '''Compute loss for the deviation of deltas from stoichiometry.

        For example, suppose we have the following reactions:

        .. code-block:: text

            (rxn 1) A  -> B
            (rxn 2) 2B -> A

        Then we can write a stoichiometry matrix **N**:

        .. code-block:: text

                 rxn 1    rxn 2
                +-           -+
              A | -1       +1 |
              B | +1       -2 |
                +-           -+

        Now if we have a derivatives vector **d** of the rates of change
        of each species, we can solve for a rate vector **v**
        representing the rates of each reaction needed to produce the
        rates of change in **d**: **d** = **N** · **v**.

        In this function, we compute **N** given a set of reactions and
        use least-squares to find the best **v** to produce a provided
        set of derivatives **d**. We then compute and return the loss as
        the L1 norm of each component of the difference between **d**
        and **N** · **v**.

        Args:
            reactions: Iterable of dictionaries with each dictionary
                describing a reaction as key-value pairs where the key
                is a species in the reaction and the value is the
                coefficient of that species in the reaction.
            derivatives: Iterable of the derivative for each species
                across all reactions.
            species_to_index: Dictionary mapping from species name to
                that species' index in ``derivatives``.
        '''
        N = np.zeros((len(species_to_index), len(reactions)))
        for i_rxn, reaction in enumerate(reactions):
            for species, coefficient in reaction.items():
                i_species = species_to_index[species]
                assert N[i_species, i_rxn] == 0
                N[i_species, i_rxn] = coefficient
        d = np.array(derivatives)
        v, *_ = lstsq(N, d)
        return d - N @ v

    @staticmethod
    def _loss(
            equilibrium_reaction_rates, timestep, interface,
            species_to_index, external_species_bioscrape,
            equilibrium_species, stoich, initial_state,
            equilibrium_reactions, propensities, params):
        assert interface.py_get_number_of_rules() == 0

        initial_state = interface.py_get_initial_state()
        reaction_rates = np.zeros(stoich.shape[1])

        kinetic_reactions = set(
            range(stoich.shape[1])) - equilibrium_reactions
        assert len(equilibrium_reactions) == len(
            equilibrium_reaction_rates)
        for i, rate in zip(equilibrium_reactions,
                equilibrium_reaction_rates):
            assert reaction_rates[i] == 0
            reaction_rates[i] = rate
        reaction_rates[list(kinetic_reactions)] = 0

        species_derivatives = stoich @ reaction_rates
        species_deltas = species_derivatives * timestep

        final_state = initial_state + species_deltas

        derivative = np.empty(len(species_to_index))  # Buffer
        # NOTE: This uses global variables declared in bioscrape.
        interface.py_calculate_deterministic_derivative(
            final_state, derivative, 0)
        for species in external_species_bioscrape:
            i = species_to_index[species]
            assert derivative[i] == 0

        # Zero-out derivative for variables we don't expect to reach
        # equilibrium so that these don't affect the solver.
        for species, i in species_to_index.items():
            if species in equilibrium_species:
                continue
            derivative[i] = 0

        # Penalize negative reaction rates.
        penalty = norm(reaction_rates[reaction_rates < 0])

        return norm(derivative) + penalty

    @staticmethod
    def _derivative_final(
            state, t, stoich, propensities, equilibrium_reactions,
            params):
        reaction_rates = np.zeros(stoich.shape[1])

        kinetic_reactions = set(
            range(stoich.shape[1])) - equilibrium_reactions
        for i in kinetic_reactions:
            assert reaction_rates[i] == 0
            reaction_rates[i] = propensities[i].py_get_propensity(
                state, params)
        # We are assuming that we have already reached equilibrium, so
        # the equilibrium reactions do nothing.

        return stoich @ reaction_rates

    def _bioscrape_update(self, state, timestep):
        self.model.set_species(state['species'])
        self.interface.py_set_initial_state(self.model.get_species_array())
        self.model.set_params(state['rates'])

        equilibrium_reactions = set([0])

        reactions = tuple(tup[2] for tup in self.model.get_reactions())
        initial_rates = np.zeros(len(equilibrium_reactions))
        species_to_index = self.model.get_species2index()
        propensities = self.model.get_propensities()
        params = self.model.get_parameter_values()
        initial_state = self.interface.py_get_initial_state()

        stoich = np.zeros((len(species_to_index), len(reactions)))
        for i_rxn, reaction in enumerate(reactions):
            for species, coefficient in reaction.items():
                i_species = species_to_index[species]
                assert stoich[i_species, i_rxn] == 0
                stoich[i_species, i_rxn] = coefficient

        # Solve for the equilibrium reaction rates.
        args = (
            timestep,
            self.interface,
            species_to_index,
            self.external_species_bioscrape,
            self.parameters['equilibrium_species'],
            stoich,
            self.interface.py_get_initial_state(),
            equilibrium_reactions,
            propensities,
            params,
        )
        result = minimize(
            self._loss,
            initial_rates,
            args=args,
            method='Nelder-Mead',
            options={
                'fatol': 1e-8,
            },
        )
        assert result.success
        equilibrium_reaction_rates = result.x

        # Simulate the equilibrium reactions at the computed rates.
        reaction_rates = np.zeros(len(reactions))
        kinetic_reactions = set(
            range(stoich.shape[1])) - equilibrium_reactions
        reaction_rates[list(kinetic_reactions)] = 0
        assert len(equilibrium_reactions) == len(
            equilibrium_reaction_rates)
        for i, rate in zip(equilibrium_reactions,
                equilibrium_reaction_rates):
            assert reaction_rates[i] == 0
            reaction_rates[i] = rate

        equilibrium_derivatives = stoich @ reaction_rates
        equilibrium_deltas = equilibrium_derivatives * timestep
        equilibrium_final = initial_state + equilibrium_deltas

        # Simulate the kinetic reactions assuming the equilibrium
        # reactions have reached equilibrium.
        species_timepoints = odeint(
            self._derivative_final,
            equilibrium_final,
            np.array([0, timestep]),
            args=(
                stoich,
                propensities,
                equilibrium_reactions,
                params,
            ),
        )

        # Compute the final update.
        species_final = species_timepoints[-1]
        species_deltas = species_final - initial_state
        deltas_dict = species_array_to_dict(
            species_deltas, species_to_index)
        return {
            'species': deltas_dict,
            'delta_species': deltas_dict,
        }

    def next_update(self, timestep, state):
        # NOTE: We assume species are in the units specified by the
        # species_units_conc parameter.

        # Prepare the state for the bioscrape process by moving species
        # from 'external' to 'species' where the bioscrape process
        # expects them to be, renaming variables, and doing unit
        # conversions.
        prepared_state = copy.deepcopy(state)
        prepared_state = self._rename_variables(
            prepared_state, self.rename_vivarium_to_bioscrape)
        prepared_state, saved_units = self._remove_units(
            prepared_state,
            units_map=self.parameters['units_map'])

        for species in self.external_species_bioscrape:
            assert species not in prepared_state['species']
            prepared_state['species'][species] = prepared_state['external'].pop(species)
            # The delta species is just for tracking updates to the
            # external environment for later conversion to exchanges. We
            # assume that `species` is not updated by bioscrape. Note
            # that the `*_delta` convention must be obeyed by the
            # bioscrape model for this to work correctly.
            delta_species = f'{species}_delta'
            assert delta_species not in species
            prepared_state['species'][delta_species] = 0

        for species, volume_name in self.parameters[
                'species_to_convert_to_counts'].items():
            # We do this after the unit conversion and stripping because
            # while it's annoying to add units back in for these
            # calculations, we don't want to break the unit coversion
            # and stripping by changing concentrations to counts first.
            # Doing this after we have handled the external species also
            # lets us convert external species to counts if for some
            # reason that was desired.
            if species in self.external_species_bioscrape:
                conc_units = saved_units['external'][species]
            else:
                conc_units = saved_units['species'][species]
            volume_units = saved_units['rates'][volume_name]

            conc = prepared_state['species'][species] * conc_units
            volume = prepared_state['rates'][volume_name] * volume_units
            # NOTE: We don't include Avogadro's number here because we
            # expect the count units to be in mol, mmol, etc.
            count = (conc * volume).to(self.parameters['species_units_count'])
            prepared_state['species'][species] = count.magnitude

        # Compute the update using the bioscrape process.
        update = self._bioscrape_update(prepared_state, timestep)

        # Make sure there are no NANs in the update.
        assert not np.any(np.isnan(list(update['species'].values())))

        # Convert count updates back to concentrations.
        for species, volume_name in self.parameters[
                'species_to_convert_to_counts'].items():
            if species in self.external_species_bioscrape:
                conc_units = saved_units['external'][species]
            else:
                conc_units = saved_units['species'][species]
            volume_units = saved_units['rates'][volume_name]
            count_units = self.parameters['species_units_count']

            count = update['species'][species] * count_units
            volume = prepared_state['rates'][volume_name] * volume_units
            # NOTE: We don't include Avogadro's number here because we
            # expect the count units to be in mol, mmol, etc.
            conc = (count / volume).to(conc_units)
            update['species'][species] = conc.magnitude

        # Add units back in
        species_update = update['species']
        delta_species_update = update['delta_species']
        update['species'] = self._add_units(
            species_update, saved_units['species'])
        update['delta_species'] = self._add_units(
            delta_species_update, saved_units['species'])

        # Postprocess the update to convert changes to external
        # molecules into exchanges.
        update.setdefault('exchanges', {})
        for species in self.external_species_bioscrape:
            delta_species = f'{species}_delta'
            if species in update['species']:
                assert update['species'][species] == 0
                del update['species'][species]
            if species in update['delta_species']:
                assert update['delta_species'][species] == 0
                del update['delta_species'][species]
            if delta_species in update['species']:
                exchange = (
                    update['species'][delta_species]
                    * self.parameters['species_units_count']
                    * AVOGADRO)
                assert species not in update['exchanges']
                # Exchanges flowing out of the cell are positive.
                update['exchanges'][species] = exchange.to(
                    units.counts).magnitude
                del update['species'][delta_species]
                del update['delta_species'][delta_species]

        update = self._rename_variables(
            update, self.rename_bioscrape_to_vivarium)

        return update


def test_exchange_aware_bioscrape():
    a = Species('A')
    a_delta = Species('A_delta')
    b = Species('B')
    c = Species('C')
    species = [a, b, a_delta, c]

    k1 = ParameterEntry('k1', 1)  # Diffusivity
    k2 = ParameterEntry('k2', 1)  # Diffusivity
    v = ParameterEntry('v', 1)

    initial_concentrations = {
        a: 10,  # mM
        a_delta: 0,  # mM
        b: 0,  # mmol
        c: 0,  # mmol
    }

    propensity = GeneralPropensity(
        f'k1 * {a}',  # Flux in mmol
        propensity_species=[a],
        propensity_parameters=[k1])
    reaction = Reaction(
        inputs=[a_delta],
        outputs=[b],
        propensity_type=propensity,
    )
    propensity_rev = GeneralPropensity(
        f'k2 * {b} / v',  # Flux in mmol
        propensity_species=[b],
        propensity_parameters=[k2, v])
    reaction_rev = Reaction(
        inputs=[b],
        outputs=[c],
        propensity_type=propensity_rev,
    )
    crn = ChemicalReactionNetwork(
        species=species,
        reactions=[reaction, reaction_rev],
        initial_concentration_dict=initial_concentrations,
    )
    with tempfile.NamedTemporaryFile() as temp_file:
        crn.write_sbml_file(
            file_name=temp_file.name,
            for_bioscrape=True,
            check_validity=True,
        )
        proc = ExchangeAwareBioscrape({
            'sbml_file_path': temp_file.name,
            'external_species': ('a',),
            'name_map': (
                (('external', 'a'), str(a)),
                (('species', 'b'), str(b)),
                (('species', 'c'), str(c)),
            ),
            'units_map': {
                'rates': {
                    'k1': 1 / units.sec,
                    'k2': 1 / units.sec,
                    'v': units.L,
                }
            },
            'species_to_convert_to_counts': {
                'B': 'v',
                'C': 'v',
            },
            'species_units_conc': units.mM,
            'species_units_count': units.mmol,
            'equilibrium_species': ('B',),
        })

    schema = proc.get_schema()
    expected_schema = {
        'delta_species': {
            'b': {
                '_default': 0.0 * units.mM,
                '_emit': True,
                '_updater': 'set',
            },
            'c': {
                '_default': 0.0 * units.mM,
                '_emit': True,
                '_updater': 'set',
            },
        },
        'exchanges': {
            'a': {
                '_default': 0,
                '_emit': True,
            },
        },
        'external': {
            'a': {
                '_default': 0.0 * units.mM,
                '_emit': True,
            },
        },
        'rates': {
            'k1': {
                '_default': 1 / units.sec,
                '_updater': 'set',
            },
            'k2': {
                '_default': 1 / units.sec,
                '_updater': 'set',
            },
            'v': {
                '_default': 1 * units.L,
                '_updater': 'set',
            },
        },
        'species': {
            'b': {
                '_default': 0.0 * units.mM,
                '_divider': 'set',
                '_emit': True,
                '_updater': 'accumulate',
            },
            'c': {
                '_default': 0.0 * units.mM,
                '_divider': 'set',
                '_emit': True,
                '_updater': 'accumulate',
            },
        },
    }
    assert schema == expected_schema

    initial_state = {
        'external': {
            'a': initial_concentrations[a] * units.mM,
        },
        'species': {
            'b': initial_concentrations[b] * units.mM,
            'c': initial_concentrations[c] * units.mM,
        },
        'rates': {
            'k1': 1 / units.sec,
            'k2': 1 / units.sec,
            'v': 10 * units.L,
        },
    }
    update = proc.next_update(1, initial_state)

    expected_update = {
        # Update gets us to equilibrium with the fixed environmental
        # species A.
        'species': {
            'b': 10 * units.mM,
            'c': 1 * units.mM,
        },
        'delta_species': {
            'b': 10 * units.mM,
            'c': 1 * units.mM,
        },
        'exchanges': {
            # Exchanges are in units of counts, but the species are in
            # units of mM with a volume of 1L.
            'a': (
                -10 * units.mM
                * initial_state['rates']['v']
                * AVOGADRO).to(units.count).magnitude,
        },
    }
    assert update.keys() == expected_update.keys()
    for key in expected_update:
        if key in ('species', 'delta_species', 'exchanges'):
            assert update[key].keys() == expected_update[key].keys()
            for species in expected_update[key]:
                assert abs(
                    update[key][species] - expected_update[key][species]
                ) <= 0.1 * abs(expected_update[key][species])
        else:
            assert update[key] == expected_update[key]


if __name__ == '__main__':
    test_exchange_aware_bioscrape()
