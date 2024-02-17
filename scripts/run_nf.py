import argparse
import os

from ecoli.composites.ecoli_configs import CONFIG_DIR_PATH
from ecoli.experiments.ecoli_master_sim import SimConfig

SIM_TAG = "seed_{seed}_gen_{gen}_cell_{cell}"
SIM_GEN_0_IN = "include {{ sim_gen_0 as sim_{name} }} from './sim_gen_0.nf'"
SIM_GEN_X_IN = "include {{ sim_gen_x as sim_{name} }} from './sim_gen_x.nf'"
ANALYSIS_SINGLE_IN = ("include {{ analysis_single as analysis_{name} }}"
    "from './analysis.nf'")
ANALYSIS_MULTIGEN_IN = ("include {{ analysis_multigen as analysis_{name} }}"
    "from './analysis.nf'")
ANALYSIS_COHORT_IN = ("include {{ analysis_cohort as analysis_{name} }}"
    "from './analysis.nf'")
ANALYSIS_VARIANT_IN = ("include {{ analysis_variant as analysis_{name} }}"
    "from './analysis.nf'")

SIM_GEN_0_FLOW = "\t{name}(params.config, variant_ch, {seed})"
SIM_GEN_X_FLOW = ("\t{name}({parent}.out.config, {parent}.out.sim_data, "
    "{parent}.out.initial_seed, {parent}.out.generation, "
    "{parent}.out.{daughter}, {sim_seed})")

ALL_SIM_CHANNEL = """
    {task_one}.out.db
        .mix({other_tasks})
        .set {{ all_sim_ch }}
"""
MULTIGEN_CHANNEL = """
    all_sim_ch
        .groupTuple(by: [0, 1], size: {size})
        .set {{ multigen_ch }}
"""
COHORT_CHANNEL = """
    all_sim_ch
        .groupTuple(by: 0, size: {size})
        .set {{ cohort_ch }}
"""
VARIANT_CHANNEL = """
    all_sim-ch
        .collect()
        .set {{ variant_ch }}
"""

def generate_colony(seeds: int):
    """
    Create strings to import and compose Nextflow processes for colony sims.
    """
    pass


def generate_lineage(seed: int, n_init_sims: int, generations: int, 
    single_daughters: bool, analysis_config: dict[str, list[str]]):
    """
    Create strings to import and compose Nextflow processes for lineage sims:
    cells that divide for a number of generations but do not interact. Also
    contains import statements and workflow jobs for analysis scripts.

    Args:
        seed: First seed for first sim
        n_init_sims: Number of sims to initialize with different seeds
        generations: Number of generations to run for each seed
        single_daughters: If True, only simulate one daughter cell each gen
        analysis_config: Dictionary with any of the following keys::

            {
                'variant': analyses to run on output of all cells combined,
                'cohort': analyses to run on output grouped by variant,
                'multigen': analyses to run on output grouped by variant & seed,
                'single': analyses to run on output for each individual cell,
            }

    Returns:
        2-element tuple containing
    
        - **sim_imports**: All `include` statements for Nextflow sim processes
        - **sim_workflow**: Fully composed workflow for entire lineage
    """
    sim_imports = []

    sim_workflow = []
    
    
    variant_analyses = analysis_config.get('variant', [])
    cohort_analyses = analysis_config.get('cohort', [])
    multigen_analyses = analysis_config.get('multigen', [])
    single_analyses = analysis_config.get('single', [])

    if variant_analyses:
        sim_imports.append("include { analysis_variant } from './analysis.nf'")
    if cohort_analyses:
        sim_imports.append("include { analysis_cohort } from './analysis.nf'")

    all_sim_tasks = []
    for seed in range(seed, seed + n_init_sims):
        multigen_sim_tasks = []
        for gen in range(generations):
            parents_encountered = []
            for cell in (range(2**gen) if not single_daughters else [0]):
                name = SIM_TAG.format(seed=seed, gen=gen, cell=cell)
                if gen == 0:
                    sim_imports.append(SIM_GEN_0_IN.format(name=name))
                    sim_workflow.append(SIM_GEN_0_FLOW.format(
                        name=name, seed=seed))
                else:
                    sim_imports.append(SIM_GEN_X_IN.format(name=name))
                    parent = SIM_TAG.format(seed=seed, gen=gen-1, cell=cell//2)
                    if parent in parents_encountered:
                        daughter = 'd2'
                    else:
                        daughter = 'd1'
                    sim_workflow.append(SIM_GEN_X_FLOW.format(
                        name=name, initial_seed=seed, sim_seed=seed+gen+cell,
                        gen=gen, parent=parent, daughter=daughter))
                all_sim_tasks.append(f'sim_{name}.out[3]')
    sim_workflow.append(ALL_SIM_CHANNEL.format(task_one=all_sim_tasks[0],
        other_tasks=', '.join(all_sim_tasks[1:])))
    multigen_size = sum([2**gen if not single_daughters else 1
                         for gen in range(generations)])
    sim_workflow.append(MULTIGEN_CHANNEL.format(size=multigen_size))
    sim_workflow.append(COHORT_CHANNEL.format(size=multigen_size*n_init_sims))
    sim_workflow.append(VARIANT_CHANNEL)


def generate_code(config):
    seed = config.get('seed', 0)
    n_init_sims = lineage.get('n_init_sims')
    lineage = config.get('lineage', {})
    if lineage:
        generations = lineage.get('generations', 1)
        single_daughters = lineage.get('single_daughters', True)
        sim_imports, sim_workflow = generate_lineage(
            seed, n_init_sims, generations, single_daughters)
        
    else:
        sim_imports, sim_workflow = generate_colony(seed, n_init_sims)         


def main():
    parser = argparse.ArgumentParser()
    default_config = os.path.join(CONFIG_DIR_PATH, 'default.json')
    parser.add_argument(
        '--config', action='store',
        default=default_config,
        help=(
            'Path to configuration file for the simulation. '
            'All key-value pairs in this file will be applied on top '
            f'of the options defined in {default_config}.'))
    config = SimConfig(parser=parser)
    config.update_from_cli()

    nf_template_path = os.path.join(os.path.dirname(__file__), 'nf.template')
    with open(nf_template_path, 'r') as f:
        nf_template = f.readlines()
    nf_template = ''.join(nf_template)
    sim_imports, sim_workflow = generate_code(config)
    nf_template = nf_template.replace("IMPORTS", sim_imports)
    nf_template = nf_template.replace("WORKFLOW", sim_workflow)


if __name__ == '__main__':
    main()
