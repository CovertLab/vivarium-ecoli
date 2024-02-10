from typing import Any, Callable, TYPE_CHECKING

from ecoli.variants.template import template

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli

VARIANT_REGISTRY: dict[str, 
    Callable[['SimulationDataEcoli', dict[str, Any]],
             'SimulationDataEcoli']] = {
    'template': template
}
"""
Make sure to import and add new variants to this dictionary.
"""

