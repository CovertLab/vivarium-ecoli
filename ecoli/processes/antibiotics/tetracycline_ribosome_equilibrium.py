import numpy as np
from scipy.constants import N_A
from scipy.optimize import root_scalar
from vivarium.core.process import Step
from vivarium.library.units import units

from ecoli.library.schema import numpy_schema, bulk_name_to_idx, counts


AVOGADRO = N_A / units.mol
#: The ribosomes in our initial state from wcEcoli have these keys, but
#: we don't use them in vivarium-ecoli.
RIBOSOME_KEYS_TO_REMOVE = ('_globalIndex', '_entryState')
# Dong, Nilsson, and Kurland (1996).
# DOI: 10.1006/jmbi.1996.0428.
# Paper above cites a total charged tRNA count of ~65k / cell.
# Our model currently produces about 3x this in uncharged tRNAs.
TRNA_CORRECTION_FACTOR = 0.33


class TetracyclineRibosomeEquilibrium(Step):

    name = 'tetracycline-ribosome-equilibrium'
    defaults = {
        # K_eq for tetracycline binding ribosome.
        # Source: Epe, B., & Woolley, P. (1984). The binding of
        # 6-demethylchlortetracycline to 70S, 50S and 30S ribosomal
        # particles: A quantitative study by fluorescence anisotropy.
        # The EMBO Journal, 3(1), 121–126.
        'K_Tc': 3e6,
        # K_eq for binding of tRNA to ribosomal A site.
        # Source: Schilling-Bartetzko, S., Franceschi, F., Sternbach, H.,
        # & Nierhaus, K. H. (1992). Apparent association constants of tRNAs
        # for the ribosomal A, P, and E sites. The Journal of biological
        # chemistry, 267(7), 4693–4702. doi: 10.1016/S0021-9258(18)42889-X
        # 1e8 for enzymatic binding at 37°C and 10 mM Mg2+
        # 4.5e6 yields a much more accurate protein synthesis IC50 but
        # is even lower than the K for non-enzymatic binding
        'K_tRNA': 4.5e6,
        'seed': 0,
        'emit_unique': False,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.random_state = np.random.RandomState(
            seed=self.parameters['seed'])
        # Helper indices for Numpy indexing
        self.trna_idx = None

    def ports_schema(self):
        return {
            'bulk': numpy_schema('bulk'),
            'tetracycline': {
                '_default': 0 * units.mM,
                '_emit': True,
            },
            '70s-free': numpy_schema('active_ribosome',
                emit=self.parameters['emit_unique']),
            # Cytoplasm volume.
            'volume': {
                '_default': 0 * units.fL,
            },
            'listeners': {
                'total_internal_tetracycline': {
                    '_default': 0 * units.mM,
                    '_updater': 'set',
                    '_emit': True,
                },
                'frac_ribosomes_bound_tetracycline': {
                    '_default': 0,
                    '_updater': 'set',
                    '_emit': True,
                },
            },
            'global_time': {'_default': 0.},
            'timestep': {'_default': self.parameters['time_step']}
        }

    def update_condition(self, timestep, states):
        return (states['global_time'] % states['timestep']
                ) == 0

    def next_update(self, _, states):
        if self.trna_idx is None:
            bulk_ids = states['bulk']['id']
            self.trna_idx = bulk_name_to_idx(
                self.parameters['trna_ids'], bulk_ids)
            self.free_30s_idx = bulk_name_to_idx(
                'CPLX0-3953[c]', bulk_ids)
            self.free_50s_idx = bulk_name_to_idx(
                'CPLX0-3962[c]', bulk_ids)
            self.tet_30s_idx = bulk_name_to_idx(
                'CPLX0-3953-tetracycline[c]', bulk_ids)

        volume = states['volume']

        count_70s_free = states['70s-free']['_entryState'].sum()

        count_30s_free = counts(states['bulk'], self.free_30s_idx)
        count_30s_tc = counts(states['bulk'], self.tet_30s_idx)
        conc_30s_tc = count_30s_tc / AVOGADRO / volume

        count_ribo_total = (
            count_70s_free + count_30s_free + count_30s_tc)
        
        count_total_trna = int(counts(states['bulk'], self.trna_idx).sum() *
            TRNA_CORRECTION_FACTOR)
        # Assume each active ribosome has two bound tRNAs (in P and E site)
        count_trna = count_total_trna - 2 * count_70s_free
        
        conc_tc_free = states['tetracycline']
        count_tc_free = int(states['tetracycline'] * AVOGADRO * volume)

        conc_tc_total = conc_tc_free + conc_30s_tc

        if not conc_tc_total:
            # If there is no tetracycline, do nothing.
            return {}

        # Cache old counts for checking later
        old_count_tc_free = count_tc_free
        old_count_30s_tc = count_30s_tc
        old_count_70s_free = count_70s_free
        old_count_30s_free = count_30s_free

        # Solve for change in free tetracycline count (by binding or
        # unbinding from 30S) required so the new free count stays
        # the same before and after tetracycline is sequestered away
        # in accordance with tet-ribosome association constants.
        sol = root_scalar(
            f=self.get_delta_tc_free_count,
            args=(
                count_tc_free,
                count_trna,
                count_ribo_total,
                count_70s_free,
                count_30s_tc
            ),
            bracket=[-count_tc_free, count_30s_tc],
            x0=0
        )
        assert sol.converged
        delta_tc_free_count = int(sol.root)
        delta_tc = delta_tc_free_count / AVOGADRO / volume

        # If trying to bind more tetracycline than free 30S subunits (e.g.
        # after dramatic increase in tetracycline count), make up difference
        # by dissociating 70S subunits into 50S and 30S-Tc
        overflow_30s_tc = -(delta_tc_free_count + count_30s_free)
        if overflow_30s_tc > 0:
            count_70s_free -= overflow_30s_tc
        
        # Apply change in free tetracycline concentration
        count_tc_free += delta_tc_free_count
        count_30s_tc -= delta_tc_free_count
        (_, count_70s_to_inhibit, count_30s_to_inhibit, 
            ribo_frac_tc_bound_expected) = self.calculate_delta(
                count_tc_free,
                count_trna,
                count_ribo_total,
                count_70s_free,
                count_30s_tc
            )
        # Remember that calculation is predicated on free tetracycline
        # count changing by delta_tc_count from 30S binding/unbinding,
        # potentially with some overflow into the 70S count
        count_30s_to_inhibit -= delta_tc_free_count
        if overflow_30s_tc > 0:
            count_30s_to_inhibit = min(count_30s_to_inhibit, count_30s_free)
            count_70s_to_inhibit += overflow_30s_tc

        assert delta_tc + conc_tc_free >= 0
        # Ensure free tetracycline count is not changing
        assert np.isclose(old_count_tc_free,
            count_30s_to_inhibit + count_70s_to_inhibit
            + count_tc_free + old_count_30s_tc, atol=1)
        
        # Ensure that total count of ribosomes in cell is not changing
        new_70s_count = old_count_70s_free - count_70s_to_inhibit
        new_30s_count = old_count_30s_free - count_30s_to_inhibit 
        new_30s_tc_count = (old_count_30s_tc + count_30s_to_inhibit
            + count_70s_to_inhibit)
        assert (count_ribo_total == new_70s_count + new_30s_count +
            new_30s_tc_count)

        # Handle the easy updates: the concentrations and subunits.
        update = {
            'bulk': [
                (self.free_30s_idx, -count_30s_to_inhibit),
                (self.tet_30s_idx, count_30s_to_inhibit)
            ],
            'tetracycline': {
                '_value': delta_tc,
                '_updater': 'accumulate',
            },
            'listeners': {
                'total_internal_tetracycline': conc_tc_total,
                'frac_ribosomes_bound_tetracycline': (
                    ribo_frac_tc_bound_expected),
            },
        }

        # Handle the more difficult updates: the active ribosomes.
        if count_70s_to_inhibit > 0:
            # Randomly select which active ribosomes to inhibit.
            row_idx_70s_to_inhibit = self.random_state.choice(
                states['70s-free']['_entryState'].sum(),
                count_70s_to_inhibit,
                replace=False,
            )
            # Assume that when ribosomes are inhibited, they also become
            # inactive.
            update.update({
                '70s-free': {
                    'delete': row_idx_70s_to_inhibit,
                },
                '50s': {
                    '_value': len(row_idx_70s_to_inhibit),
                    '_updater': 'accumulate',
                },
            })
            update['bulk'].append((self.free_50s_idx, count_70s_to_inhibit))
            update['bulk'].append((self.tet_30s_idx, count_70s_to_inhibit))
        return update
    
    def get_delta_tc_free_count(
        self,
        delta_tc_count,
        count_tc_free,
        count_trna,
        count_ribo_total,
        count_70s_free,
        count_30s_tc,
    ):
        # See what happens if free tetracycline changes by delta_tc_count
        count_tc_free += delta_tc_count
        count_30s_tc -= delta_tc_count

        count_30s_free = count_ribo_total - count_30s_tc - count_70s_free
        # If trying to bind more tetracycline than free 30S subunits (e.g.
        # after dramatic increase in tetracycline count), make up difference
        # by dissociating 70S subunits into 50S and 30S-Tc
        overflow_30s_tc = -(delta_tc_count + count_30s_free)
        if overflow_30s_tc > 0:
            count_70s_free -= overflow_30s_tc
        
        delta_tc_free_count, _, _, _ = self.calculate_delta(
            count_tc_free,
            count_trna,
            count_ribo_total,
            count_70s_free,
            count_30s_tc
        )
        return delta_tc_free_count
    
    def calculate_delta(
        self,
        count_tc_free,
        count_trna,
        count_ribo_total,
        count_70s_free,
        count_30s_tc
    ):  
        # The ratio between the concentration of ribosomes (30S or 70S)
        # bound to tetracycline (Tc) and the concentration bound to
        # tRNA.
        tc_trna_binding_ratio = (
            self.parameters['K_Tc'] * count_tc_free
            / self.parameters['K_tRNA'] / count_trna
        )
        # Why this works: Let r be the binding ratio, tc be the
        # tetracycline-bound concentration, and tr be the tRNA-bound
        # concentration. Note that this means that r = tc / tr. We want
        # to find tc / (tc + tr), which we can calculate as r / (1 + r).
        # Here's the proof:
        #
        #     r / (1 + r) = (tc / tr) / (1 + tc / tr)
        #                 = (tc / tr) / ((tr + tc) / tr)
        #                 = tc / (tr + tc)
        #
        # Note that here we are assuming that all ribosomes are bound by
        # either tetracycline or tRNA. This assumption is reasonable
        # because the binding constants are so large (10^6) compared to
        # the number of 30s and 70s ribosomes available (~16,000 total).
        ribo_frac_tc_bound = tc_trna_binding_ratio / (
            1 + tc_trna_binding_ratio)
        count_ribo_total_tc_target = min(
            int(ribo_frac_tc_bound * count_ribo_total),
            # Leave one free tetracycline molecule to account for
            # rounding errors when converting between counts and
            # concentrations.
            max(0, count_tc_free + count_30s_tc - 1),
        )

        # Divide Tc-bound ribosomes between 30s and 70s proportionally.
        count_70s_tc_target = int(count_ribo_total_tc_target * (
            count_70s_free / count_ribo_total))
        count_30s_tc_target = (
            count_ribo_total_tc_target - count_70s_tc_target)

        # For reporting, find the resulting fraction of ribosomes bound
        # to tetracycline.
        ribo_frac_tc_bound_expected = (count_70s_tc_target +
            count_30s_tc_target) / count_ribo_total

        # Determine what needs to be done.
        count_70s_to_inhibit = count_70s_tc_target
        count_30s_to_inhibit = int(count_30s_tc_target - count_30s_tc)
        count_ribos_to_inhibit = (
            count_70s_to_inhibit + count_30s_to_inhibit)
        delta_tc_count = -count_ribos_to_inhibit
        
        return (delta_tc_count, count_70s_to_inhibit, count_30s_to_inhibit,
            ribo_frac_tc_bound_expected)
