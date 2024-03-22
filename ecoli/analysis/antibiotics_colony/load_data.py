import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from functools import partial

import pandas as pd
from tqdm import tqdm
from vivarium.library.dict_utils import get_value_from_path

from ecoli.analysis.antibiotics_colony import (EXPERIMENT_ID_MAPPING,
                                               PATHS_TO_LOAD)
from ecoli.analysis.db import access_counts_old, deserialize_and_remove_units


def agent_data_table(raw_data, paths_dict, condition, seed):
    """Combine data from all agents into DataFrames for each timestep.

    Args:
        raw_data: Tuple of (time, dictionary at time for one replicate).
        paths_dict: Dictionary mapping paths within each agent to names
            that will be used the keys in the returned dictionary.
        condition: String identifier for experimental condition
        seed: Initial seed for this replicate

    Returns:
        Dataframe where each column is a path and each row is an agent."""
    time = raw_data[0]
    raw_data = raw_data[1]
    collected_data = {'Agent ID': []}
    agents_at_time = raw_data['agents']
    for agent_id, agent_at_time in agents_at_time.items():
        collected_data['Agent ID'].append(agent_id)
        for name, path in paths_dict.items():
            value_in_agent = get_value_from_path(agent_at_time, path)
            # Replace missing values with 0
            if value_in_agent is None:
                value_in_agent = 0
            path_data = collected_data.setdefault(name, [])
            path_data.append(value_in_agent)
    collected_data = pd.DataFrame(collected_data)
    collected_data['Time'] = [time] * len(collected_data)
    collected_data['Seed'] = [seed] * len(collected_data)
    collected_data['Condition'] = [condition] * len(collected_data)
    return collected_data


def load_data(experiment_id=None, cpus=8, sampling_rate=2,
    host="10.138.0.75", port=27017
):
    # Get data for the specified experiment_id
    monomers = [path[-1] for path in PATHS_TO_LOAD.values() if path[0]=='monomer']
    mrnas = [path[-1] for path in PATHS_TO_LOAD.values() if path[0]=='mrna']
    inner_paths = [path for path in PATHS_TO_LOAD.values()
        if path[-1] not in mrnas and path[-1] not in monomers
        and path != ('total_mrna',)]
    outer_paths = [('data', 'dimensions'), ('data', 'fields')]
    for condition, seeds in EXPERIMENT_ID_MAPPING.items():
        for seed, curr_experiment_id in seeds.items():
            if curr_experiment_id != experiment_id:
                continue
            metadata = {condition: {seed: {}}}
            rep_data = access_counts_old(
                experiment_id=experiment_id,
                monomer_names=monomers,
                mrna_names=mrnas,
                rna_synth_prob=mrnas,
                inner_paths=inner_paths,
                outer_paths=outer_paths,
                host=host,
                port=port,
                sampling_rate=sampling_rate,
                cpus=cpus,
                start_time=0,
                end_time=26000)
            with ProcessPoolExecutor(cpus) as executor:
                print('Deserializing data and removing units...')
                deserialized_data = list(tqdm(executor.map(
                    deserialize_and_remove_units, rep_data.values()),
                    total=len(rep_data)))
            rep_data = dict(zip(rep_data.keys(), deserialized_data))
            # Get spatial environment data for snapshot plots
            print('Extracting spatial environment data...')
            metadata[condition][seed]['bounds'] = rep_data[
                min(rep_data)]['dimensions']['bounds']
            metadata[condition][seed]['fields'] = {
                time: data_at_time['fields']
                for time, data_at_time in rep_data.items()
            }
            agent_df_paths = partial(agent_data_table,
                paths_dict=PATHS_TO_LOAD, condition=condition, seed=seed)
            with ProcessPoolExecutor(cpus) as executor:
                print('Converting data to DataFrame...')
                rep_dfs = list(tqdm(executor.map(
                    agent_df_paths, rep_data.items()),
                    total=len(rep_data)))
            # Save data for each experiment as local csv
            pd.concat(rep_dfs).to_csv(
                f'data/colony_data/sim_dfs/{experiment_id}.csv')
            with open(f'data/colony_data/sim_dfs{experiment_id}_metadata.json',
                'wb'
            ) as f:
                json.dump(metadata, f)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--experiment_id",
        "-e",
        help="Experiment ID to load data for",
        required=True,
    )
    parser.add_argument(
        "--cpus",
        "-c",
        type=int,
        help="# of CPUs to use for deserializing",
        required=True,
    )
    args = parser.parse_args()
    os.makedirs('data/colony_data/sim_dfs/', exist_ok=True)
    load_data(args.experiment_id, cpus=args.cpus)

if __name__ == '__main__':
    main()
