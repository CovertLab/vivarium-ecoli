from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli

def test_variant(sim_data: 'SimulationDataEcoli', params: dict[str, list[Any]]
         ) -> 'SimulationDataEcoli':
    """
    Test variant that adds new attributes to sim_data.

    Args:
        sim_data: Simulation data to modify
        params: Parameter dictionary of the following format::

            {
                'a': int,
                'b': float,
            }
    
    Returns:
        Simulation data with the following attributes modified::

            sim_data.a
            sim_data.b

    """
    sim_data.a = params['a']
    sim_data.b = params['b']
    return sim_data

def test_variant_2(sim_data: 'SimulationDataEcoli', params: dict[str, list[Any]]
         ) -> 'SimulationDataEcoli':
    """
    Test variant that adds new attributes to sim_data.

    Args:
        sim_data: Simulation data to modify
        params: Parameter dictionary of the following format::

            {
                'c': int,
                'd': float,
            }
    
    Returns:
        Simulation data with the following attributes modified::

            sim_data.c
            sim_data.d

    """
    sim_data.c = params['c']
    sim_data.d = params['d']
    return sim_data
