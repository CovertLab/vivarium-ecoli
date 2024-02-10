import argparse
import os
import pickle
import uuid

from ecoli.experiments.ecoli_master_sim import EcoliSim, SimConfig
from ecoli.variants.parse_variants import parse_variants

from wholecell.utils.filepath import OUT_DIR


def main():
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    config = SimConfig()
    config.update_from_cli()

    if config['variants']:
        with open(config['sim_data_path'], 'rb') as f:
            sim_data = pickle.load(f)
        sim_data_variants = parse_variants(f, config['variants'])
        os.makedirs(config['variants_outdir'])
        if config['experiment_id'] is None:
            config['experiment_id'] = str(uuid.uuid1())



if __name__ == '__main__':
    main()

