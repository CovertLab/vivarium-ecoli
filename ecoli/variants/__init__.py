from typing import Any, Callable, TYPE_CHECKING

from ecoli.variants.test_variant import test_variant

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli

VARIANT_REGISTRY: dict[str, 
    Callable[['SimulationDataEcoli', dict[str, Any]],
             'SimulationDataEcoli']] = {
    'test_variant': test_variant
}
"""
Make sure to import and add new variants to this dictionary.
"""

