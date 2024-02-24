from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli

def test_variant(sim_data: 'SimulationDataEcoli', params: dict[str, list[Any]]
         ) -> 'SimulationDataEcoli':
    """
    Test variant that adds new attributes to sim_data. Also tests composition 
    of variant functions.

    Args:
        sim_data: Simulation data to modify
        params: Parameter dictionary of the following format::

            {
                'a': int,
                'b': str,
                'c': {
                    'd': int,
                    'e': float
                }
            }
    
    Returns:
        Simulation data with the following attributes modified::

            sim_data.a
            sim_data.b
            sim_data.d
            sim_data.e

    """
    sim_data.a = params['a']
    sim_data.b = params['b']
    sim_data = test_variant_2(sim_data, params['c'])
    return sim_data

def test_variant_2(sim_data: 'SimulationDataEcoli', params: dict[str, list[Any]]
         ) -> 'SimulationDataEcoli':
    """
    Test variant that adds new attributes to sim_data.

    Args:
        sim_data: Simulation data to modify
        params: Parameter dictionary of the following format::

            {
                'd': int,
                'e': float,
            }
    
    Returns:
        Simulation data with the following attributes modified::

            sim_data.d
            sim_data.e

    """
    sim_data.d = params['d']
    sim_data.e = params['e']
    return sim_data
