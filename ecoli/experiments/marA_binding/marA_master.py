import pandas as pd
import numpy as np
import ast
import json
from os.path import exists

from ecoli.experiments.ecoli_master_sim import EcoliSim
from vivarium.plots.simulation_output import plot_variables

def testDefault(paths):
    sim = EcoliSim.from_file()
    sim.raw_output = False
    sim.total_time = 500
    sim.emitter = "database"
    
    query = [i['variable'] for i in paths]
    timeseries = sim.run(query)

    plot_degenes(timeseries, "default", paths)


def testMarA(paths):
    sim = EcoliSim.from_file()
    sim.raw_output = False
    sim.total_time = 500
    sim.emitter = "database"
    if not exists('data/wcecoli_marA_added.json'):
        with open('data/wcecoli_t0.json') as f:
            initial_state = json.load(f)
        for promoter in initial_state['unique']['promoter']:
            initial_state['unique']['promoter'][promoter]['bound_TF'] += [False]
        initial_state['bulk']['PD00365[c]'] *= 4
        with open('data/wcecoli_marA_added.json', 'w') as f:
            json.dump(initial_state, f)
    sim.processes.pop('ecoli-tf-binding')
    sim.processes = {'ecoli-tf-binding-marA': None, **sim.processes}
    sim.initial_state_file = "wcecoli_marA_added"
    
    query = [i['variable'] for i in paths]
    query += [("bulk", "ACRB-MONOMER[p]")]
    timeseries = sim.run(query)
    print(timeseries["bulk"]["ACRB-MONOMER[p]"])

    plot_degenes(timeseries, "marA_long_sim", paths)

def ids_of_interest():
    model_degenes = pd.read_csv("ecoli/experiments/marA_binding/model_degenes.csv")
    bulk_paths = []
    for bulk_names, common_id in zip(
            model_degenes["bulk_ids"], model_degenes["common_name"]):
        common_names = [common_id]*len(bulk_names)
        bulk_names = ast.literal_eval(bulk_names)
        bulk_paths += [
            {
                "variable": ('bulk', bulk_name),
                "display": common_name
            }
            for bulk_name, common_name in zip(
                bulk_names, common_names)
        ]
    complex_paths = []
    for complex_names, monomers_used, common_id, complex_common_names in zip(
            model_degenes["complex_ids"], model_degenes["monomers_used"], 
            model_degenes["common_name"], model_degenes["complex_common_names"]):
        if len(complex_names) == 0:
            continue
        common_names = [common_id]*len(complex_names)
        complex_names = ast.literal_eval(complex_names)
        monomers_used = np.array(ast.literal_eval(monomers_used))
        monomers_used[monomers_used == None] = 0
        complex_common_names = ast.literal_eval(complex_common_names)
        complex_paths += [
            {
                "variable": ('bulk', complex_name),
                "color": "tab:green",
                "display": f"{complex_name} aka {complex_common_name[:45]} " +
                    f"({common_name} x {-monomer_used})"
            }
            for complex_name, common_name, monomer_used, complex_common_name in zip(
                complex_names, common_names, monomers_used, complex_common_names)
        ]
    return bulk_paths + complex_paths

def plot_degenes(timeseries, name, variable_paths):
    
    fig = plot_variables(
        timeseries, 
        variables=variable_paths,
        # Try to find a way to overlay line plots from different runs
        # Put expected fold change in plot as dashed line
        # Get list of misbehaving molecules to report at meetings
    )
    fig.tight_layout()
    fig.savefig(f"ecoli/experiments/marA_binding/{name}.png")
    

def main():
    paths = ids_of_interest()
    #testDefault(paths)
    testMarA(paths)

if __name__=="__main__":
    main()
