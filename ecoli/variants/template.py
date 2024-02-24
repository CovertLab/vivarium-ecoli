from typing import Any, TYPE_CHECKING
if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli

def template(sim_data: 'SimulationDataEcoli', params: dict[str, list[Any]]
         ) -> 'SimulationDataEcoli':
    """
    Base variant that does not modify sim_data. Use this as a template.

    Args:
        sim_data: Simulation data to modify
        params: Parameter dictionary of the following format::

            {
                'param_1': int, # Type hints are for your benefit
                'param_2': float, # Type hints are not enforced
            }
    
    Returns:
        Simulation data with the following attributes modified::

            List attributes here

    """
    return sim_data
