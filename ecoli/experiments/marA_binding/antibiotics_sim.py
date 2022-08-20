import os
import json

from ecoli.composites.ecoli_engine_process import run_simulation
from ecoli.experiments.ecoli_master_sim import CONFIG_DIR_PATH, SimConfig
from ecoli.experiments.marA_binding.antibiotic_gene_plots import ids_of_interest

def run_sim(tet_conc=0, baseline=False, seed=0):
    config = SimConfig()
    config.update_from_json(os.path.join(
        CONFIG_DIR_PATH, "antibiotics_tetracycline_cephaloridine.json"))
    tetracycline_gradient = {
        'total_time': 10000,
        'initial_state_file': 'wcecoli_tet',
        'emitter': 'database',
        'spatial_environment_config': {
            'reaction_diffusion': {
                'gradient': {
                    'molecules': {
                        'tetracycline': tet_conc   
                    }
                }
            },
            'field_timeline': {
                'timeline': [
                    [4000, {
                        "tetracycline": 0
                    }]
                ]
            }
        },
        'engine_process_reports': [],
        'seed': seed
    }
    marA_regulated = [monomer['variable'] for monomer in ids_of_interest()]
    tetracycline_gradient['engine_process_reports'] += marA_regulated
    if not os.path.exists('data/wcecoli_tet.json'):
        with open('data/wcecoli_t0.json') as f:
            initial_state = json.load(f)
        # Add bulk tetracycline and marR-tet complex
        initial_state['bulk']['tetracycline[c]'] = 0
        initial_state['bulk']['marR-tet[c]'] = 0
        # Add promoter binding data for marA and marR
        for promoter_data in initial_state['unique']['promoter'].values():
            promoter_data['bound_TF'] += [False, False]
        with open('data/wcecoli_tet.json', 'w') as f:
            json.dump(initial_state, f)
    config.update_from_dict(tetracycline_gradient)
    if baseline:
        config._config['add_processes'].remove('antibiotic-transport-steady-state')
        config._config['add_processes'].remove('ecoli-rna-interference')
        config._config['add_processes'].remove('tetracycline-ribosome-equilibrium')
        config._config['process_configs'].pop('ecoli-rna-interference')
        config._config['engine_process_reports'].remove(['bulk', 'marR-tet[c]'])
        config._config['engine_process_reports'].remove(('bulk', 'marR-tet[c]'))
        config._config['engine_process_reports'].remove(['bioscrape_deltas',])
        config._config['flow'].pop('ecoli-polypeptide-initiation_requester')
        config._config['mar_regulon'] = False
        config._config['initial_state_file'] = 'wcecoli_t0'
    run_simulation(config)

def generate_data():
    run_sim(0.003375, seed = 10000, baseline=True)
        
if __name__ == "__main__":
    generate_data()
    