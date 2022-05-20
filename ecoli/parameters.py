from vivarium.library.topology import get_in, assoc_path
from vivarium.library.units import units

class Parameter:

    def __init__(self, value, source='', canonicalize=None, note=''):
        self.value = value
        self.source = source
        self.canonicalize = canonicalize or (lambda x: x)
        self.note = note


class ParameterStore:

    def __init__(self, parameters, derivation_rules=None):
        self._parameters = parameters
        self.derive_parameters(derivation_rules or {})

    def get(self, path):
        param_obj = get_in(self._parameters, path)
        if not param_obj:
            raise RuntimeError(
                f'No parameter found at path {path}')
        return param_obj.canonicalize(param_obj.value)

    def add(self, path, parameter):
        assert get_in(self._parameters, path) is None
        assoc_path(self._parameters, path, parameter)

    def derive_parameters(self, derivation_rules):
        for path, deriver in derivation_rules.items():
            new_param = deriver(self)
            self.add(path, new_param)


PARAMETER_DICT = {
    'ampicillin': {
        'permeability': {
            'outer': Parameter(
                0.28e-5 * units.cm / units.sec,
                'Kojima and Nikaido (2013)',
                note='This is total, not per-porin, permeability.',
            ),
        },
        'mic': Parameter(
            2 * units.micrograms / units.mL,
            'Mazzariol, Cornaglia, and Nikaido (2000)',
            lambda x: (
                # Divide by molecular weight from PubChem.
                x / (349.4 * units.g / units.mol)
            ).to(units.mM),
        ),
        'efflux': {
            'vmax': Parameter(
                0.085 * units.nmol / units.mg / units.sec,
                'Kojima and Nikaido (2013)',
            ),
            'km': Parameter(
                2.16e-3 * units.mM,
                'Kojima and Nikaido (2013)',
            ),
            'n': Parameter(
                1.9,
                'Kojima and Nikaido (2013)',
            ),
        },
        'hydrolysis': {
            'kcat': Parameter(
                6.5e-3 / units.sec,
                'Mazzariol, Cornaglia, and Nikaido (2000)',
            ),
            'km': Parameter(
                0.9e-3 * units.mM,
                'Mazzariol, Cornaglia, and Nikaido (2000)',
            ),
            'n': Parameter(
                1,
                'Mazzariol, Cornaglia, and Nikaido (2000)',
            ),
        },
    },
    'cephaloridine': {
        'permeability': {
            'outer': Parameter(
                (52.6e-5 + 4.5e-5) * units.cm / units.sec,
                'Nikaido, Rosenberg, and Foulds (1983)',
                note='This is total, not per-porin, permeability',
            ),
        },
        'mic': Parameter(
            0.5 * units.micrograms / units.mL,
            'Rolinson (1980)',
            lambda x: (
                # Divide by molecular weight from PubChem.
                x / (415.5 * units.g / units.mol)
            ).to(units.mM),
        ),
        'efflux': {
            'vmax': Parameter(
                1.82 * units.nmol / units.mg / units.sec,
                'Nagano and Nikaido (2009)',
            ),
            'km': Parameter(
                0.288 * units.mM,
                'Nagano and Nikaido (2009)',
            ),
            'n': Parameter(
                1.75,
                'Nagano and Nikaido (2009)',
            ),
        },
        'hydrolysis': {
            'kcat': Parameter(
                130 / units.sec,
                'Galleni et al. (1988)',
                note='Not confirmed',
            ),
            'km': Parameter(
                0.17 * units.mM,
                'Galleni et al. (1988)',
                note='Not confirmed',
            ),
            'n': Parameter(
                1
            ),
        },
    },
    'shape': {
        'periplasm_fraction': Parameter(
            0.2,
            'Stock et al. (1977)'
        ),
        'initial_cell_mass': Parameter(
            1170 * units.fg,
            'Model',
        ),
        'initial_cell_volume': Parameter(
            1.2 * units.fL,
            'Model',
        ),
        'initial_area': Parameter(
            4.52 * units.um**2,
            'Model',
        ),
    },
    'concs': {
        'initial_pump': Parameter(
            6.7e-4 * units.mM,
            'Model',
        ),
        'initial_hydrolase': Parameter(
            7.1e-4 * units.mM,
            'Model',
        ),
    },
}

DERIVATION_RULES = {
    ('shape', 'initial_periplasm_volume'): lambda params: Parameter(
        (
            params.get(('shape', 'initial_cell_volume'))
            * params.get(('shape', 'periplasm_fraction'))
        ),
    ),
    ('ampicillin', 'efflux', 'kcat'): lambda params: Parameter(
        (
            params.get(('ampicillin', 'efflux', 'vmax'))
            / params.get(('concs', 'initial_pump'))
            * params.get(('shape', 'initial_cell_mass'))
            / params.get(('shape', 'initial_periplasm_volume'))
        )
    ),
    ('cephaloridine', 'efflux', 'kcat'): lambda params: Parameter(
        (
            params.get(('cephaloridine', 'efflux', 'vmax'))
            / params.get(('concs', 'initial_pump'))
            * params.get(('shape', 'initial_cell_mass'))
            / params.get(('shape', 'initial_periplasm_volume'))
        )
    ),
}

param_store = ParameterStore(PARAMETER_DICT, DERIVATION_RULES)
